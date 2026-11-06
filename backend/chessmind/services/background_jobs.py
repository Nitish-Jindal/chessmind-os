"""Bounded in-process import admission, preserving the existing ImportJob rows."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import update

from chessmind.config import get_settings
from chessmind.db import AsyncSessionLocal
from chessmind.models import ImportJob

logger = logging.getLogger("chessmind.background")
_tasks: dict[asyncio.Task[None], UUID] = {}
_lock = asyncio.Lock()


def submit_job(job_id: UUID, run: Callable[[], Awaitable[None]]) -> bool:
    """One running import, at most queue_max waiting; no coroutine on rejection."""
    if len(_tasks) >= get_settings().stockfish_background_queue_max + 1:
        logger.warning("background import queue saturated")
        return False

    async def execute() -> None:
        try:
            # Imports contain bounded, offloaded PGN parsing plus async SQL I/O.
            async with asyncio.timeout(get_settings().background_job_timeout_sec):
                async with _lock:
                    await run()
        except Exception:
            logger.exception("background import runner failed job_id=%s", job_id)
            await _mark_retryable([job_id])

    task = asyncio.create_task(execute(), name="pgn-import")
    _tasks[task] = job_id
    task.add_done_callback(lambda finished: _tasks.pop(finished, None))
    return True


async def cancel_jobs() -> None:
    tasks = list(_tasks)
    job_ids = list(_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if job_ids:
        await _mark_retryable(job_ids)


async def _mark_retryable(job_ids: list[UUID]) -> None:
    try:
        async with asyncio.timeout(2), AsyncSessionLocal() as session:
            await session.execute(
                update(ImportJob)
                .where(
                    ImportJob.id.in_(job_ids),
                    ImportJob.status.in_(["queued", "running"]),
                )
                .values(
                    status="failed",
                    error="Import interrupted; retry the upload.",
                    artifacts_json={"retryable": True, "reason": "interrupted"},
                )
            )
            await session.commit()
    except Exception:
        logger.exception("could not record interrupted import jobs")
