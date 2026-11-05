"""Background analysis for settled session positions.

The API keeps move responses voice-responsive by scheduling the final position
analysis after a session transition. Jobs use the existing cache-first Stockfish
path and a fresh database session, matching the in-process import-job pattern.
"""

from __future__ import annotations

import asyncio
import logging

import chess

from chessmind.config import get_settings
from chessmind.db import AsyncSessionLocal
from chessmind.services.canonical_fen import canonical_key
from chessmind.services.engine import analyze_board, get_cached_analysis

logger = logging.getLogger("chessmind.session_evaluation")

_pending: dict[str, asyncio.Task[None]] = {}


def schedule_position_analysis(fen: str) -> None:
    """Schedule cache-first analysis once per canonical position."""
    board = chess.Board(fen)
    key = canonical_key(board)
    task = _pending.get(key)
    if task is not None and not task.done():
        return
    if len(_pending) >= get_settings().stockfish_background_queue_max + 1:
        logger.warning("background evaluation queue saturated; retry via /analysis/position or a session transition")
        return
    _pending[key] = asyncio.create_task(_analyze_position(key, board), name=f"analysis:{key[:24]}")


def is_position_analysis_pending(fen: str) -> bool:
    """Return whether this process is analyzing the canonical position."""
    key = canonical_key(chess.Board(fen))
    task = _pending.get(key)
    return task is not None and not task.done()


async def wait_for_position_analysis(fen: str) -> None:
    """Wait for a scheduled analysis; exposed for deterministic backend tests."""
    task = _pending.get(canonical_key(chess.Board(fen)))
    if task is not None:
        await task


async def wait_for_pending_analyses() -> None:
    """Drain background work before an isolated test resets its database."""
    while tasks := [task for task in _pending.values() if not task.done()]:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _analyze_position(key: str, board: chess.Board) -> None:
    current = asyncio.current_task()
    try:
        settings = get_settings()
        deadline = (settings.stockfish_engine_startup_timeout_sec
                    + 2 * settings.stockfish_background_timeout_sec + 5)
        async with asyncio.timeout(deadline), AsyncSessionLocal() as session:
            if await get_cached_analysis(session, board) is None:
                await analyze_board(
                    session,
                    board=board,
                    lane="background",
                )
            await session.commit()
    except Exception:  # noqa: BLE001
        # Analysis is optional to the move transaction and must never fail the
        # player's move or suppress the opponent's response.
        logger.exception("background session evaluation failed position=%s", key)
    finally:
        if _pending.get(key) is current:
            _pending.pop(key, None)


async def cancel_pending_analyses() -> None:
    tasks = list(_pending.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
