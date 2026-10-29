"""Stockfish worker — long-lived singleton, lock-guarded, cache-first.

The UCI subprocess is spawned lazily on the first analysis request in a
given worker process and reused for the lifetime of that process. All
callers go through a single ``EngineClient`` with a ``threading.Lock``:
python-chess's ``SimpleEngine`` is a synchronous wrapper over
subprocess pipes (no asyncio affinity), so a plain threading lock is
the right level and ``asyncio.to_thread`` keeps the async surface clean.

This is the behaviour ARCHITECTURE.md §6.2 prescribes: cache is the
primary path, engine runs only on cache miss, every result is stamped
with engine version + depth + nodes + time + ``perspective='white'``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import chess
import chess.engine
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.config import get_settings
from chessmind.models import EngineAnalysis, Position
from chessmind.services.opening_graph import upsert_position

logger = logging.getLogger("chessmind.engine")


@dataclass
class AnalysisResult:
    position_id: UUID
    engine_version: str
    depth: int
    multipv: int
    nodes: int
    time_ms: int
    perspective: str
    results: list[dict[str, Any]]
    cached: bool


# --------------------------------------------------------------------------- #
# Singleton engine client
# --------------------------------------------------------------------------- #


_VERSION_RE = re.compile(r"Stockfish\s+([^\s]+)")


class EngineClient:
    """Lazy, process-wide, lock-guarded Stockfish wrapper."""

    def __init__(self) -> None:
        self._engine: chess.engine.SimpleEngine | None = None
        self._version: str | None = None
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()

    def _start(self) -> None:
        with self._start_lock:
            if self._engine is not None:
                return
            path = get_settings().stockfish_path
            if not os.path.isabs(path):
                # resolve relative to this file so uvicorn-cwd doesn't matter
                here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                path = os.path.normpath(os.path.join(here, path))
            if not os.path.exists(path):
                raise RuntimeError(f"stockfish binary not found at {path}")
            logger.info("stockfish start path=%s", path)
            engine = chess.engine.SimpleEngine.popen_uci(path)
            try:
                raw_name = engine.id.get("name", "stockfish/unknown")
                match = _VERSION_RE.search(raw_name)
                self._version = match.group(1) if match else raw_name
                self._engine = engine
            except Exception:
                engine.close()
                raise

    def shutdown(self) -> None:
        with self._start_lock:
            if self._engine is not None:
                try:
                    self._engine.close()
                except Exception:
                    logger.exception("error closing stockfish")
                self._engine = None
                self._version = None

    def _analyze_sync(self, board: chess.Board, depth: int, multipv: int) -> tuple[str, list[dict[str, Any]], int, int]:
        self._start()
        with self._lock:
            assert self._engine is not None
            limit = chess.engine.Limit(depth=depth)
            infos = self._engine.analyse(board, limit, multipv=multipv)
            # Normalize to a list even when multipv=1 returns a single dict in some versions.
            if isinstance(infos, dict):
                infos = [infos]
            results: list[dict[str, Any]] = []
            total_nodes = 0
            total_time_ms = 0
            for rank, info in enumerate(infos, start=1):
                score = info.get("score")
                white_score = score.white() if score is not None else None
                cp: int | None
                mate: int | None
                if white_score is None:
                    cp = None
                    mate = None
                else:
                    cp = white_score.score()
                    mate = white_score.mate()
                pv_moves = list(info.get("pv", []) or [])
                pv_uci = [m.uci() for m in pv_moves]
                # Compute SAN by replaying on a copy of the board.
                pv_san: list[str] = []
                replay = board.copy(stack=False)
                for mv in pv_moves:
                    pv_san.append(replay.san(mv))
                    replay.push(mv)
                results.append({
                    "line_rank": rank,
                    "cp": cp,
                    "mate": mate,
                    "pv_uci": pv_uci,
                    "pv_san": pv_san,
                })
                total_nodes = max(total_nodes, int(info.get("nodes", 0) or 0))
                total_time_ms = max(total_time_ms, int((info.get("time", 0) or 0) * 1000))
            return self._version or "unknown", results, total_nodes, total_time_ms

    async def analyze(self, board: chess.Board, depth: int, multipv: int) -> tuple[str, list[dict[str, Any]], int, int]:
        return await asyncio.to_thread(self._analyze_sync, board, depth, multipv)


engine_client = EngineClient()


# --------------------------------------------------------------------------- #
# Cache-first analyse
# --------------------------------------------------------------------------- #


async def _lookup_cache(
    session: AsyncSession, *, position_id: UUID, engine_version: str, min_depth: int, min_multipv: int
) -> EngineAnalysis | None:
    stmt = (
        select(EngineAnalysis)
        .where(
            EngineAnalysis.position_id == position_id,
            EngineAnalysis.engine_version == engine_version,
            EngineAnalysis.depth >= min_depth,
            EngineAnalysis.multipv >= min_multipv,
        )
        .order_by(desc(EngineAnalysis.depth))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def analyze_board(
    session: AsyncSession,
    *,
    board: chess.Board,
    depth: int | None = None,
    multipv: int | None = None,
) -> AnalysisResult:
    """Analyze ``board`` with cache-first; persist new runs; return a stable result."""
    settings = get_settings()
    depth = depth or settings.stockfish_default_depth
    multipv = multipv or settings.stockfish_default_multipv

    # Resolve the position row (upsert so an analyse of a fresh FEN still caches).
    position_id = await upsert_position(session, board)

    # Peek at the engine version WITHOUT burning a search — just to key the cache.
    # The first call pays the warmup; subsequent cache lookups don't.
    engine_version = engine_client._version or "stockfish/unknown"
    cached: EngineAnalysis | None = None
    if engine_version != "stockfish/unknown":
        cached = await _lookup_cache(
            session,
            position_id=position_id,
            engine_version=engine_version,
            min_depth=depth,
            min_multipv=multipv,
        )

    if cached is not None:
        return AnalysisResult(
            position_id=position_id,
            engine_version=cached.engine_version,
            depth=cached.depth,
            multipv=cached.multipv,
            nodes=cached.nodes,
            time_ms=cached.time_ms,
            perspective=cached.perspective,
            results=cached.results_json,
            cached=True,
        )

    version, results, nodes, time_ms = await engine_client.analyze(board, depth, multipv)

    # Try the cache again now that we know the real version — avoids a duplicate row
    # if the first call started cold.
    existing = await _lookup_cache(
        session,
        position_id=position_id,
        engine_version=version,
        min_depth=depth,
        min_multipv=multipv,
    )
    if existing is not None:
        return AnalysisResult(
            position_id=position_id,
            engine_version=existing.engine_version,
            depth=existing.depth,
            multipv=existing.multipv,
            nodes=existing.nodes,
            time_ms=existing.time_ms,
            perspective=existing.perspective,
            results=existing.results_json,
            cached=True,
        )

    row = EngineAnalysis(
        id=uuid4(),
        position_id=position_id,
        engine="stockfish",
        engine_version=version,
        depth=depth,
        multipv=multipv,
        nodes=nodes,
        time_ms=time_ms,
        perspective="white",
        results_json=results,
    )
    session.add(row)
    await session.flush()
    return AnalysisResult(
        position_id=position_id,
        engine_version=version,
        depth=depth,
        multipv=multipv,
        nodes=nodes,
        time_ms=time_ms,
        perspective="white",
        results=results,
        cached=False,
    )
