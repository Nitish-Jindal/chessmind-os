"""PGN ingestion — recursive variation walk + graph upserts.

§11.2 resolution: variations are walked recursively. The first child at
every node is the mainline continuation; every other child is a sideline
whose descendants are also sidelines, so ``is_mainline`` is the AND of
"was the first child at every step from the root".
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, Iterator
from uuid import UUID

import chess
import chess.pgn
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import AsyncSessionLocal
from chessmind.models import Game, ImportJob, Position
from chessmind.services.opening_graph import (
    bump_position_popularity,
    upsert_move_edge,
    upsert_position,
    upsert_repertoire_line,
)

logger = logging.getLogger("chessmind.import")


@dataclass
class ImportStats:
    games: int = 0
    positions_touched: set[str] = field(default_factory=set)
    edges_created_or_bumped: int = 0
    repertoire_lines: int = 0
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# PGN tree walking
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EdgeVisit:
    board_before: chess.Board
    move: chess.Move
    san: str
    uci: str
    is_mainline: bool
    path_sans: tuple[str, ...]
    comment: str
    nags: tuple[int, ...]


def walk_game(game: chess.pgn.Game) -> Iterator[EdgeVisit]:
    """DFS over every variation. Mainline = first child at every depth from the root."""
    def recur(node: chess.pgn.GameNode, board: chess.Board, path: list[str], mainline: bool) -> Iterator[EdgeVisit]:
        for idx, child in enumerate(node.variations):
            move = child.move
            san = board.san(move)
            edge_mainline = mainline and (idx == 0)
            yield EdgeVisit(
                board_before=board.copy(stack=False),
                move=move,
                san=san,
                uci=move.uci(),
                is_mainline=edge_mainline,
                path_sans=tuple(path + [san]),
                comment=(child.comment or "").strip(),
                nags=tuple(sorted(child.nags)),
            )
            board.push(move)
            yield from recur(child, board, path + [san], edge_mainline)
            board.pop()

    board = game.board()
    yield from recur(game, board, [], True)


# --------------------------------------------------------------------------- #
# PGN parsing
# --------------------------------------------------------------------------- #


def iter_games(pgn_text: str) -> Iterator[chess.pgn.Game]:
    stream = io.StringIO(pgn_text)
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            return
        yield game


def _parse_rating(headers: chess.pgn.Headers, key: str) -> int | None:
    raw = headers.get(key)
    if not raw or not raw.strip() or raw == "?":
        return None
    try:
        v = int(raw)
    except ValueError:
        return None
    return v if 0 <= v <= 4000 else None


def _parse_date(headers: chess.pgn.Headers) -> date | None:
    raw = headers.get("Date") or headers.get("UTCDate")
    if not raw or "?" in raw:
        return None
    for fmt in ("%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Import entry points
# --------------------------------------------------------------------------- #


async def import_pgn_text(
    session: AsyncSession,
    *,
    player_id: UUID,
    pgn_text: str,
    source: str = "upload",
    import_job_id: UUID | None = None,
    stats: ImportStats | None = None,
) -> ImportStats:
    """Parse ``pgn_text`` and persist every game it contains."""
    stats = stats or ImportStats()
    for game in iter_games(pgn_text):
        try:
            await _import_single_game(
                session,
                game=game,
                player_id=player_id,
                source=source,
                import_job_id=import_job_id,
                stats=stats,
            )
        except Exception as exc:  # noqa: BLE001 — surface in artifacts, keep importing
            logger.exception("game import failed")
            stats.errors.append(str(exc))
    return stats


async def _import_single_game(
    session: AsyncSession,
    *,
    game: chess.pgn.Game,
    player_id: UUID,
    source: str,
    import_job_id: UUID | None,
    stats: ImportStats,
) -> None:
    headers = game.headers
    pgn_text = str(game)

    game_row = Game(
        player_id=player_id,
        source=source,
        pgn=pgn_text,
        imported_from_batch=import_job_id,
        white_name=(headers.get("White") or "?")[:200],
        black_name=(headers.get("Black") or "?")[:200],
        white_rating=_parse_rating(headers, "WhiteElo"),
        black_rating=_parse_rating(headers, "BlackElo"),
        result=headers.get("Result") or "*",
        date_played=_parse_date(headers),
        time_control=(headers.get("TimeControl") or None),
        eco=(headers.get("ECO") or None),
        provenance_json={
            "event": headers.get("Event"),
            "site": headers.get("Site"),
            "round": headers.get("Round"),
            "pgn_headers": dict(headers),
        },
    )
    session.add(game_row)
    await session.flush()

    header_eco = headers.get("ECO") or None
    opening_name = headers.get("Opening") or None

    touched_positions: set[UUID] = set()
    result_str = headers.get("Result") or "*"

    for visit in walk_game(game):
        from_pos_id = await upsert_position(
            session, visit.board_before, eco=header_eco, opening_name=opening_name
        )
        after = visit.board_before.copy(stack=False)
        after.push(visit.move)
        to_pos_id = await upsert_position(session, after, eco=header_eco, opening_name=opening_name)

        await upsert_move_edge(
            session,
            from_position_id=from_pos_id,
            to_position_id=to_pos_id,
            uci=visit.uci,
            san=visit.san,
            result=result_str,
        )
        stats.edges_created_or_bumped += 1
        touched_positions.add(from_pos_id)
        touched_positions.add(to_pos_id)

        color = "white" if visit.board_before.turn == chess.WHITE else "black"
        await upsert_repertoire_line(
            session,
            player_id=player_id,
            color=color,
            from_position_id=from_pos_id,
            preferred_uci=visit.uci,
            is_mainline=visit.is_mainline,
            source_game_id=game_row.id,
            source_variation_path=" ".join(visit.path_sans) if visit.path_sans else None,
            comments=visit.comment or None,
            nags=list(visit.nags),
        )
        stats.repertoire_lines += 1

    await bump_position_popularity(session, touched_positions)
    for pos_id in touched_positions:
        stats.positions_touched.add(str(pos_id))
    stats.games += 1


# --------------------------------------------------------------------------- #
# Async job runner (same process; ARQ worker swaps in post-MVP)
# --------------------------------------------------------------------------- #


async def run_import_job(job_id: UUID, pgn_text: str) -> None:
    """Load the job, run the import, write status + artifacts.

    Runs in the same process as the API via ``asyncio.create_task``. The
    pattern (ImportJob rows + background execution) is identical to the
    future ARQ worker; swap this entry point when ARQ lands.
    """
    async with AsyncSessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job is None:
            logger.error("import job %s vanished before start", job_id)
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()
        player_id = job.player_id

    stats = ImportStats()
    try:
        async with AsyncSessionLocal() as session:
            await import_pgn_text(
                session,
                player_id=player_id,
                pgn_text=pgn_text,
                source="upload",
                import_job_id=job_id,
                stats=stats,
            )
            await session.commit()
        final_status = "succeeded" if not stats.errors else "partial"
    except Exception as exc:  # noqa: BLE001
        logger.exception("import job %s failed", job_id)
        final_status = "failed"
        stats.errors.append(str(exc))

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(
                status=final_status,
                progress_games=stats.games,
                total_games=stats.games + len(stats.errors),
                error="\n".join(stats.errors) if stats.errors else None,
                artifacts_json={
                    "games": stats.games,
                    "positions_touched": len(stats.positions_touched),
                    "edges": stats.edges_created_or_bumped,
                    "repertoire_lines": stats.repertoire_lines,
                    "errors": stats.errors,
                },
                finished_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def import_pgn_sync(
    session: AsyncSession, *, player_id: UUID, pgn_text: str, source: str = "upload"
) -> ImportStats:
    """Inline, non-ImportJob entry point for tests and seed scripts."""
    stats = ImportStats()
    await import_pgn_text(
        session,
        player_id=player_id,
        pgn_text=pgn_text,
        source=source,
        import_job_id=None,
        stats=stats,
    )
    return stats
