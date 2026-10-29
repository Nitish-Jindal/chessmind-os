"""/imports/pgn — bulk PGN ingestion endpoints."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import get_session
from chessmind.deps import get_current_player
from chessmind.models import ImportJob, Player
from chessmind.schemas.imports import ImportJobRead, ImportPgnRequest
from chessmind.services.pgn_import import run_import_job

logger = logging.getLogger("chessmind.import")

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("/pgn", response_model=ImportJobRead, status_code=status.HTTP_202_ACCEPTED)
async def create_pgn_import(
    payload: ImportPgnRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> ImportJob:
    job = ImportJob(
        player_id=player.id,
        kind="player_pgn",
        source=payload.source,
        status="queued",
        progress_games=0,
        total_games=0,
        artifacts_json={},
    )
    session.add(job)
    await session.flush()
    job_id = job.id
    # Flush is enough for the dependency's outer commit; actually commit now
    # so the background task can see the row immediately.
    await session.commit()

    # Fire-and-forget: ARQ worker takes over post-MVP; same ImportJob flow.
    asyncio.create_task(run_import_job(job_id, payload.pgn))
    return job


@router.get("/{import_id}", response_model=ImportJobRead)
async def get_import(
    import_id: UUID,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> ImportJob:
    job = await session.get(ImportJob, import_id)
    if job is None or job.player_id != player.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import not found")
    return job
