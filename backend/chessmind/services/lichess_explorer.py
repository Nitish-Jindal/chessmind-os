"""Lichess Masters opening explorer adapter — real master move statistics.

GET https://explorer.lichess.ovh/masters?fen=... returns, for one position,
the number of master games and the White-win / draw / Black-win split for
each continuation. This module:

1. fetches that response (free endpoint; an optional ``LICHESS_API_TOKEN`` is
   sent as a Bearer token if set),
2. validates every move through python-chess (``parse_san`` on the queried
   board; Lichess's king-takes-rook castling UCI is normalized to e1g1 form;
   anything python-chess rejects is dropped and reported),
3. caches the parsed snapshot in ``master_explorer_cache`` keyed by canonical
   FEN with a TTL, and mirrors per-move counts into ``master_move_stat`` so
   the sampler's master tier uses the same real data,
4. degrades gracefully: on network failure it serves a stale cached snapshot
   if one exists, otherwise reports ``unavailable`` so callers fall back to
   local counts. A short in-process backoff keeps a dead upstream from adding
   a timeout to every request.

Counts are never synthesized here: every number served comes from a Lichess
response (live or cached).
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

import chess
import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind import __version__
from chessmind.config import get_settings
from chessmind.models import MasterExplorerCache, MasterMoveStat
from chessmind.services.canonical_fen import canonical_key
from chessmind.services.opening_graph import upsert_position

logger = logging.getLogger("chessmind.lichess")

SOURCE = "lichess_masters"
ATTRIBUTION = "Lichess Masters database · lichess.org opening explorer"

MastersStatus = Literal["live", "cached", "stale", "unavailable", "disabled"]


@dataclass
class MasterMove:
    uci: str
    san: str
    white: int
    draws: int
    black: int
    games: int
    average_rating: int | None


@dataclass
class MastersSnapshot:
    fen: str
    canonical_key: str
    white: int
    draws: int
    black: int
    total_games: int
    opening_eco: str | None
    opening_name: str | None
    moves: list[MasterMove] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MastersSnapshot:
        moves = [MasterMove(**m) for m in data.get("moves", [])]
        return cls(**{**data, "moves": moves})


@dataclass
class MastersResult:
    status: MastersStatus
    snapshot: MastersSnapshot | None
    fetched_at: datetime | None
    error: str | None = None
    source: str = SOURCE
    attribution: str = ATTRIBUTION


def explorer_web_url(board: chess.Board) -> str:
    return f"https://lichess.org/analysis/standard/{board.fen().replace(' ', '_')}#explorer"


def _count(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def parse_masters_response(board: chess.Board, payload: dict[str, Any]) -> MastersSnapshot:
    """Turn a /masters JSON body into a validated snapshot for ``board``.

    python-chess is the authority on every move: SAN is parsed against the
    queried board and the stored UCI comes from python-chess, never from the
    payload.
    """
    if not isinstance(payload, dict):
        raise ValueError("explorer payload is not a JSON object")
    moves: list[MasterMove] = []
    dropped: list[dict[str, Any]] = []
    for raw in payload.get("moves") or []:
        san = raw.get("san") if isinstance(raw, dict) else None
        try:
            move = board.parse_san(san)
        except (TypeError, ValueError):
            dropped.append({"san": san, "uci": raw.get("uci") if isinstance(raw, dict) else None})
            continue
        white, draws, black = _count(raw.get("white")), _count(raw.get("draws")), _count(raw.get("black"))
        rating = raw.get("averageRating")
        moves.append(MasterMove(
            uci=move.uci(),
            san=board.san(move),
            white=white,
            draws=draws,
            black=black,
            games=white + draws + black,
            average_rating=int(rating) if isinstance(rating, (int, float)) else None,
        ))
    moves.sort(key=lambda m: (-m.games, m.san))
    white, draws, black = _count(payload.get("white")), _count(payload.get("draws")), _count(payload.get("black"))
    opening = payload.get("opening") if isinstance(payload.get("opening"), dict) else {}
    return MastersSnapshot(
        fen=board.fen(),
        canonical_key=canonical_key(board),
        white=white,
        draws=draws,
        black=black,
        total_games=white + draws + black,
        opening_eco=opening.get("eco"),
        opening_name=opening.get("name"),
        moves=moves,
        dropped=dropped,
    )


async def fetch_masters_payload(board: chess.Board, *, client: httpx.AsyncClient) -> dict[str, Any]:
    settings = get_settings()
    headers = {"User-Agent": f"chessmind-os/{__version__}", "Accept": "application/json"}
    if settings.lichess_api_token:
        headers["Authorization"] = f"Bearer {settings.lichess_api_token}"
    r = await client.get(
        f"{settings.lichess_explorer_url.rstrip('/')}/masters",
        params={"fen": board.fen(), "moves": settings.lichess_explorer_moves, "topGames": 0},
        headers=headers,
        timeout=settings.lichess_explorer_timeout_s,
    )
    r.raise_for_status()
    return r.json()


# Monotonic deadline before which we do not call upstream again after a failure.
_backoff_until = 0.0


def reset_backoff() -> None:
    global _backoff_until
    _backoff_until = 0.0


def _start_backoff(exc: Exception) -> None:
    global _backoff_until
    seconds = get_settings().lichess_explorer_backoff_s
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        retry_after = exc.response.headers.get("retry-after", "")
        seconds = max(seconds, float(retry_after) if retry_after.isdigit() else 60.0)
    _backoff_until = time.monotonic() + seconds


async def _store(session: AsyncSession, board: chess.Board, snap: MastersSnapshot, now: datetime) -> None:
    stmt = pg_insert(MasterExplorerCache).values(
        canonical_key=snap.canonical_key,
        fen=snap.fen,
        source=SOURCE,
        fetched_at=now,
        total_games=snap.total_games,
        payload_json=snap.to_json(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["canonical_key"],
        set_={
            "fen": stmt.excluded.fen,
            "fetched_at": stmt.excluded.fetched_at,
            "total_games": stmt.excluded.total_games,
            "payload_json": stmt.excluded.payload_json,
            "updated_at": now,
        },
    )
    await session.execute(stmt)

    position_id = await upsert_position(session, board, eco=snap.opening_eco, opening_name=snap.opening_name)
    # Upsert (not delete + insert) so two concurrent first fetches of one position cannot
    # collide on uq_master_move_stat; moves that dropped out of the response are removed.
    await session.execute(
        delete(MasterMoveStat).where(
            MasterMoveStat.position_id == position_id,
            MasterMoveStat.source == SOURCE,
            MasterMoveStat.move_uci.not_in([m.uci for m in snap.moves]),
        )
    )
    for m in snap.moves:
        ins = pg_insert(MasterMoveStat).values(
            id=uuid4(),
            position_id=position_id,
            source=SOURCE,
            move_uci=m.uci,
            games_count=m.games,
            white_wins=m.white,
            black_wins=m.black,
            draws=m.draws,
        )
        await session.execute(ins.on_conflict_do_update(
            constraint="uq_master_move_stat",
            set_={"games_count": ins.excluded.games_count, "white_wins": ins.excluded.white_wins,
                  "black_wins": ins.excluded.black_wins, "draws": ins.excluded.draws, "updated_at": now},
        ))
    await session.flush()


async def get_masters(
    session: AsyncSession,
    board: chess.Board,
    *,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> MastersResult:
    """Cache-first master stats for ``board``; never raises for upstream failures."""
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    key = canonical_key(board)
    row = (
        await session.execute(select(MasterExplorerCache).where(MasterExplorerCache.canonical_key == key))
    ).scalar_one_or_none()
    cached = MastersSnapshot.from_json(row.payload_json) if row is not None else None

    if row is not None and row.fetched_at >= now - timedelta(hours=settings.lichess_explorer_ttl_hours):
        return MastersResult("cached", cached, row.fetched_at)
    if not settings.lichess_explorer_enabled:
        return MastersResult("stale" if cached else "disabled", cached, row.fetched_at if row else None,
                             error="LICHESS_EXPLORER_ENABLED is off")
    if time.monotonic() < _backoff_until:
        return MastersResult("stale" if cached else "unavailable", cached, row.fetched_at if row else None,
                             error="explorer recently unreachable; retrying shortly")

    try:
        if client is None:
            async with httpx.AsyncClient() as own:
                payload = await fetch_masters_payload(board, client=own)
        else:
            payload = await fetch_masters_payload(board, client=client)
        snap = parse_masters_response(board, payload)
    except (httpx.HTTPError, ValueError) as exc:
        _start_backoff(exc)
        logger.warning("lichess explorer unavailable for %s: %s", key, exc)
        return MastersResult("stale" if cached else "unavailable", cached, row.fetched_at if row else None,
                             error=f"{type(exc).__name__}: {exc}"[:300])

    await _store(session, board, snap, now)
    return MastersResult("live", snap, now)
