"""Cache-first Stockfish analysis with isolated, deadline-bound execution lanes."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

import chess
import chess.engine
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.config import get_settings
from chessmind.models import EngineAnalysis, Position
from chessmind.services.canonical_fen import canonical_key
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


_CLEANUP_TIMEOUT_SEC = 0.5

_VERSION_RE = re.compile(r"Stockfish\s+([^\s]+)")


class EngineUnavailable(RuntimeError):
    """Bounded engine failure; safe to retry without restarting the application."""


class EngineBusy(EngineUnavailable):
    """Admission capacity or queue deadline exceeded."""


class EngineClient:
    """One native asyncio UCI subprocess per lane; no blocking worker threads.

    Only the lock owner can replace or kill its subprocess. Cancellation kills
    that exact transport before releasing admission, so late cleanup cannot
    affect a replacement. Queued callers never touch the active process.
    """

    def __init__(self, lane: Literal["interactive", "background"] = "interactive") -> None:
        self.lane = lane
        self._engine: chess.engine.UciProtocol | None = None
        self._transport: asyncio.SubprocessTransport | None = None
        self._retired: chess.engine.UciProtocol | None = None
        self._retired_search: asyncio.Task[list[chess.engine.InfoDict]] | None = None
        self._version: str | None = None
        self._lock = asyncio.Lock()
        self._waiting = 0
        self.active = asyncio.Event()

    def shutdown(self) -> None:
        # Nonblocking emergency close, also usable by synchronous test teardown.
        transport, self._transport = self._transport, None
        self._engine = None
        if transport is not None:
            transport.close()  # asyncio kills and reaps the child

    async def _discard(
        self, search: asyncio.Task[list[chess.engine.InfoDict]] | None = None
    ) -> None:
        # Register quarantine and kill synchronously BEFORE any cancellation point.
        # Keep at most one retired owner; no detached cleanup worker is created.
        engine = self._engine or self._retired
        self._retired = engine
        if search is not None:
            self._retired_search = search
        self.shutdown()
        pending = {
            future
            for future in (
                engine.returncode if engine is not None else None,
                self._retired_search,
            )
            if future is not None and not future.done()
        }
        try:
            if pending:
                # Unlike wait_for/gather, wait never waits for cancellation to
                # finish and never propagates caller cancellation into children.
                await asyncio.wait(pending, timeout=_CLEANUP_TIMEOUT_SEC)
        finally:
            if engine is not None and engine.returncode.done():
                self._retired = None
            task = self._retired_search
            if task is not None:
                if task.done():
                    if not task.cancelled():
                        task.exception()  # consume a failed search's exception
                    self._retired_search = None
                elif self._retired is None and not task.cancelling():
                    # Only cancel python-chess futures after process exit, to
                    # preserve its connection_lost bookkeeping. No unbounded join.
                    task.cancel()
            if self._retired is not None or self._retired_search is not None:
                logger.warning("engine cleanup pending; lane quarantined lane=%s", self.lane)

    async def _start(self) -> None:
        if (self._retired is not None and not self._retired.returncode.done()) or (
            self._retired_search is not None and not self._retired_search.done()
        ):
            raise EngineUnavailable(f"{self.lane} engine cleanup pending; retry later")
        if self._engine is not None and not self._engine.returncode.done():
            return
        await self._discard()
        if self._retired is not None or self._retired_search is not None:
            # Never accumulate replacement processes if the OS cannot reap a
            # killed child promptly; a later request can recheck it.
            raise EngineUnavailable(f"{self.lane} engine cleanup pending; retry later")
        settings = get_settings()
        configured = settings.stockfish_path
        path = configured
        if not os.path.isabs(path):
            here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            path = os.path.normpath(os.path.join(here, path))
        if not os.path.exists(path) and os.sep not in configured:
            path = shutil.which(configured) or path
        logger.info("engine restart attempt lane=%s", self.lane)
        # Capture the transport before UCI initialization: even a hung handshake
        # can be killed, unlike cancelling the popen_uci convenience helper.
        async with asyncio.timeout(settings.stockfish_engine_startup_timeout_sec):
            self._transport, self._engine = await chess.engine.UciProtocol.popen(path)
            if self.lane == "background" and hasattr(os, "setpriority"):
                try:
                    os.setpriority(os.PRIO_PROCESS, self._transport.get_pid(), 10)
                except OSError:
                    logger.info("background process priority unavailable")
            await self._engine.initialize()
            await self._engine.configure(
                {
                    "Threads": settings.stockfish_threads,
                    "Hash": settings.stockfish_hash_mb,
                }
            )
            await self._engine.ping()
        raw_name = self._engine.id.get("name", "stockfish/unknown")
        match = _VERSION_RE.search(raw_name)
        self._version = match.group(1) if match else raw_name
        logger.info("engine restart success lane=%s pid=%s", self.lane, self._transport.get_pid())

    async def _search(
        self, engine: chess.engine.UciProtocol, board: chess.Board, depth: int, multipv: int
    ) -> list[chess.engine.InfoDict]:
        started = time.monotonic()
        try:
            analysis = await engine.analysis(
                board, chess.engine.Limit(depth=depth), multipv=multipv
            )
            self.active.set()  # UCI readyok received and go issued, not merely admitted
            await analysis.wait()
            return analysis.multipv
        finally:
            logger.info(
                "engine search lane=%s operation=analyse duration_ms=%.1f",
                self.lane,
                (time.monotonic() - started) * 1000,
            )

    async def analyze(
        self, board: chess.Board, depth: int, multipv: int
    ) -> tuple[str, list[dict[str, Any]], int, int]:
        settings = get_settings()
        timeout = (
            settings.stockfish_interactive_timeout_sec
            if self.lane == "interactive"
            else settings.stockfish_background_timeout_sec
        )
        queue_timeout = (
            settings.stockfish_interactive_queue_timeout_sec
            if self.lane == "interactive"
            else settings.stockfish_background_timeout_sec
        )
        capacity = 1 if self.lane == "interactive" else settings.stockfish_background_queue_max
        if self._lock.locked() and self._waiting >= capacity:
            logger.warning("engine queue saturated lane=%s operation=analyse", self.lane)
            raise EngineBusy(f"{self.lane} engine queue full; retry later")
        queued = time.monotonic()
        self._waiting += 1
        try:
            try:
                async with asyncio.timeout(queue_timeout):
                    await self._lock.acquire()
            except TimeoutError as exc:
                logger.warning(
                    "engine admission timeout lane=%s wait_ms=%.1f",
                    self.lane,
                    (time.monotonic() - queued) * 1000,
                )
                raise EngineBusy(f"{self.lane} engine admission timeout") from exc
        finally:
            self._waiting -= 1
        started = time.monotonic()
        logger.info(
            "engine admitted lane=%s operation=analyse wait_ms=%.1f",
            self.lane,
            (started - queued) * 1000,
        )
        search = None
        healthy = False
        try:
            await self._start()
            assert self._engine is not None
            async with asyncio.timeout(timeout):
                # Preserve python-chess's futures until connection_lost reaps
                # the child; cancellation of analyse itself can corrupt them.
                search = asyncio.create_task(self._search(self._engine, board, depth, multipv))
                infos = await asyncio.shield(search)
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
                results.append(
                    {
                        "line_rank": rank,
                        "cp": cp,
                        "mate": mate,
                        "pv_uci": pv_uci,
                        "pv_san": pv_san,
                    }
                )
                total_nodes = max(total_nodes, int(info.get("nodes", 0) or 0))
                total_time_ms = max(total_time_ms, int((info.get("time", 0) or 0) * 1000))
            healthy = True
            return self._version or "unknown", results, total_nodes, total_time_ms

        except (TimeoutError, chess.engine.EngineError, OSError, ValueError) as exc:
            logger.warning(
                "engine failure lane=%s operation=analyse type=%s", self.lane, type(exc).__name__
            )
            raise EngineUnavailable(f"{self.lane} engine unavailable; retry later") from exc
        finally:
            try:
                if not healthy:
                    # One shared budget for child termination AND task cleanup.
                    # Interrupted/expired cleanup leaves both owners quarantined.
                    await self._discard(search)
            finally:
                # Also release if cleanup receives a second cancellation.
                self.active.clear()
                self._lock.release()
                logger.info(
                    "engine finished lane=%s operation=analyse duration_ms=%.1f",
                    self.lane,
                    (time.monotonic() - started) * 1000,
                )


engine_client = EngineClient("interactive")
background_engine_client = EngineClient("background")


# --------------------------------------------------------------------------- #
# Cache-first analyse
# --------------------------------------------------------------------------- #


async def _lookup_cache(
    session: AsyncSession,
    *,
    position_id: UUID,
    engine_version: str,
    min_depth: int,
    min_multipv: int,
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


async def get_cached_analysis(session: AsyncSession, board: chess.Board) -> EngineAnalysis | None:
    """Read the best persisted analysis for a position without writing or searching Stockfish."""
    position_id = (
        await session.execute(
            select(Position.id).where(Position.canonical_key == canonical_key(board))
        )
    ).scalar_one_or_none()
    if position_id is None:
        return None
    stmt = (
        select(EngineAnalysis)
        .where(EngineAnalysis.position_id == position_id, EngineAnalysis.engine == "stockfish")
        .order_by(desc(EngineAnalysis.depth), desc(EngineAnalysis.cached_at))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def analyze_board(
    session: AsyncSession,
    *,
    board: chess.Board,
    depth: int | None = None,
    multipv: int | None = None,
    lane: Literal["interactive", "background"] = "interactive",
) -> AnalysisResult:
    """Analyze ``board`` with cache-first; persist new runs; return a stable result."""
    settings = get_settings()
    client = engine_client if lane == "interactive" else background_engine_client
    depth = depth if depth is not None else getattr(settings, f"stockfish_{lane}_depth")
    multipv = multipv if multipv is not None else getattr(settings, f"stockfish_{lane}_multipv")
    # Explicit analysis requests retain depth/MultiPV; the wall deadline still applies.

    # Resolve the position row (upsert so an analyse of a fresh FEN still caches).
    position_id = (
        await session.execute(
            select(Position.id).where(Position.canonical_key == canonical_key(board))
        )
    ).scalar_one_or_none()

    # Peek at the engine version WITHOUT burning a search — just to key the cache.
    # The first call pays the warmup; subsequent cache lookups don't.
    engine_version = client._version or "stockfish/unknown"
    cached: EngineAnalysis | None = None
    if position_id is not None and engine_version != "stockfish/unknown":
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

    version, results, nodes, time_ms = await client.analyze(board, depth, multipv)

    # Do not hold a newly inserted Position's unique-key lock during search:
    # another lane analyzing the same FEN must remain independent at the DB too.
    if position_id is None:
        position_id = await upsert_position(session, board)

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
