"""Opponent-PGN ingest + OpponentPositionMove materializer.

Follows the player-import pipeline (recursive variation walk via
``pgn_import.walk_game``), with two differences:

- The ``source`` on the ``Game`` row is ``opponent:<profile_id>`` and
  ``provenance_json["opponent_color"]`` records which side the opponent
  played in that specific PGN, matched against OpponentProfile's
  ``display_name`` / ``aliases_json``.
- For every edge where it is the opponent's turn to move, we upsert an
  ``OpponentPositionMove`` row so the sampler's tier-1 lookup is served
  by in-table state rather than a scan of ``Game.pgn`` blobs.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import chess
import chess.pgn
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import AsyncSessionLocal
from chessmind.models import (
    Game,
    ImportJob,
    OpponentPositionMove,
    OpponentProfile,
)
from chessmind.services.opening_graph import (
    bump_position_popularity,
    upsert_move_edge,
    upsert_position,
)
from chessmind.services.pgn_import import ImportStats, iter_games, walk_game, _parse_rating, _parse_date

logger = logging.getLogger("chessmind.opponent_import")


def _match_opponent_color(headers: chess.pgn.Headers, profile: OpponentProfile) -> str | None:
    aliases = {profile.display_name.strip().lower()}
    for alias in profile.aliases_json or []:
        aliases.add(str(alias).strip().lower())
    white = (headers.get("White") or "").strip().lower()
    black = (headers.get("Black") or "").strip().lower()
    if white and any(white == a or a in white for a in aliases):
        return "white"
    if black and any(black == a or a in black for a in aliases):
        return "black"
    return None


async def _upsert_opponent_move(
    session: AsyncSession,
    *,
    profile_id: UUID,
    position_id: UUID,
    color: str,
    uci: str,
    result_counts: tuple[int, int, int],
) -> None:
    wins, losses, draws = result_counts
    stmt = (
        pg_insert(OpponentPositionMove)
        .values(
            id=uuid4(),
            opponent_profile_id=profile_id,
            position_id=position_id,
            color=color,
            move_uci=uci,
            games_count=1,
            wins=wins,
            losses=losses,
            draws=draws,
            recency_weight=1.0,
            sample_size_bucket="small",
        )
        .on_conflict_do_update(
            index_elements=["opponent_profile_id", "position_id", "color", "move_uci"],
            set_={
                "games_count": OpponentPositionMove.__table__.c.games_count + 1,
                "wins": OpponentPositionMove.__table__.c.wins + wins,
                "losses": OpponentPositionMove.__table__.c.losses + losses,
                "draws": OpponentPositionMove.__table__.c.draws + draws,
            },
        )
    )
    await session.execute(stmt)


def _result_counts_for_opponent(result: str, opponent_color: str) -> tuple[int, int, int]:
    if result == "1-0":
        return (1, 0, 0) if opponent_color == "white" else (0, 1, 0)
    if result == "0-1":
        return (0, 1, 0) if opponent_color == "white" else (1, 0, 0)
    if result == "1/2-1/2":
        return (0, 0, 1)
    return (0, 0, 0)


def _bump_sample_bucket(games: int) -> str:
    if games >= 50:
        return "large"
    if games >= 10:
        return "medium"
    return "small"


async def import_opponent_pgn_text(
    session: AsyncSession,
    *,
    owner_player_id: UUID,
    profile: OpponentProfile,
    pgn_text: str,
    import_job_id: UUID | None = None,
    stats: ImportStats | None = None,
) -> ImportStats:
    stats = stats or ImportStats()
    for game in iter_games(pgn_text):
        try:
            await _import_single_opponent_game(
                session,
                game=game,
                owner_player_id=owner_player_id,
                profile=profile,
                import_job_id=import_job_id,
                stats=stats,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("opponent game import failed")
            stats.errors.append(str(exc))
    return stats


async def _import_single_opponent_game(
    session: AsyncSession,
    *,
    game: chess.pgn.Game,
    owner_player_id: UUID,
    profile: OpponentProfile,
    import_job_id: UUID | None,
    stats: ImportStats,
) -> None:
    headers = game.headers
    opponent_color = _match_opponent_color(headers, profile)
    if opponent_color is None:
        stats.errors.append(f"skipped: opponent not in game headers {headers.get('White')!r} vs {headers.get('Black')!r}")
        return

    pgn_text = str(game)
    game_row = Game(
        player_id=owner_player_id,
        source=f"opponent:{profile.id}",
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
            "opponent_profile_id": str(profile.id),
            "opponent_color": opponent_color,
            "pgn_headers": dict(headers),
        },
    )
    session.add(game_row)
    await session.flush()

    touched_positions: set[UUID] = set()
    result_str = headers.get("Result") or "*"

    for visit in walk_game(game):
        from_pos_id = await upsert_position(session, visit.board_before)
        after = visit.board_before.copy(stack=False)
        after.push(visit.move)
        to_pos_id = await upsert_position(session, after)

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

        # Only register OpponentPositionMove when it was the opponent's turn.
        side_to_move = "white" if visit.board_before.turn == chess.WHITE else "black"
        if side_to_move == opponent_color:
            counts = _result_counts_for_opponent(result_str, opponent_color)
            await _upsert_opponent_move(
                session,
                profile_id=profile.id,
                position_id=from_pos_id,
                color=opponent_color,
                uci=visit.uci,
                result_counts=counts,
            )

    await bump_position_popularity(session, touched_positions)
    for pos_id in touched_positions:
        stats.positions_touched.add(str(pos_id))
    stats.games += 1


async def refresh_sample_buckets(session: AsyncSession, profile_id: UUID) -> None:
    """Recompute sample_size_bucket for every row of this opponent."""
    rows = (
        await session.execute(
            select(OpponentPositionMove).where(
                OpponentPositionMove.opponent_profile_id == profile_id
            )
        )
    ).scalars().all()
    for r in rows:
        r.sample_size_bucket = _bump_sample_bucket(r.games_count)


async def run_opponent_import_job(job_id: UUID, pgn_text: str) -> None:
    """Background entry point — same pattern as player PGN imports."""
    async with AsyncSessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job is None or job.opponent_profile_id is None:
            logger.error("opponent job %s missing or lacks profile id", job_id)
            return
        profile = await session.get(OpponentProfile, job.opponent_profile_id)
        if profile is None:
            logger.error("opponent profile %s vanished", job.opponent_profile_id)
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()
        owner = job.player_id

    stats = ImportStats()
    try:
        async with AsyncSessionLocal() as session:
            profile = await session.get(OpponentProfile, job.opponent_profile_id)
            await import_opponent_pgn_text(
                session,
                owner_player_id=owner,
                profile=profile,
                pgn_text=pgn_text,
                import_job_id=job_id,
                stats=stats,
            )
            await refresh_sample_buckets(session, profile.id)
            profile.games_imported = (profile.games_imported or 0) + stats.games
            profile.last_import_at = datetime.now(timezone.utc)
            await session.commit()
        final_status = "succeeded" if not stats.errors else "partial"
    except Exception as exc:  # noqa: BLE001
        logger.exception("opponent import job %s failed", job_id)
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
                    "errors": stats.errors,
                },
                finished_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
