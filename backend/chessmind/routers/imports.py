"""/imports/pgn — bulk PGN ingestion endpoints."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import get_session
from chessmind.deps import get_current_player
from chessmind.models import ImportJob, OpponentProfile, Player
from chessmind.schemas.imports import ImportJobRead, ImportPgnRequest
from chessmind.services.opponent_import import run_opponent_import_job
from chessmind.services.pgn_import import run_import_job

logger = logging.getLogger("chessmind.import")

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("/pgn", response_model=ImportJobRead, status_code=status.HTTP_202_ACCEPTED)
async def create_pgn_import(
    payload: ImportPgnRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> ImportJob:
    profile_id = None
    if payload.intent == "opponent":
        if payload.opponent_profile_id is None:
            raise HTTPException(422, "intent=opponent requires opponent_profile_id")
        profile = await session.get(OpponentProfile, payload.opponent_profile_id)
        if profile is None or profile.owner_player_id != player.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "opponent not found")
        profile_id = profile.id
    kind = {"repertoire": "player_pgn", "corpus": "corpus_pgn", "opponent": "opponent_pgn"}[payload.intent]

    job = ImportJob(
        player_id=player.id,
        kind=kind,
        opponent_profile_id=profile_id,
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
    runner = run_opponent_import_job if payload.intent == "opponent" else run_import_job
    asyncio.create_task(runner(job_id, payload.pgn))
    return job


@router.get("/{import_id}", response_model=ImportJobRead)
async def get_import(
    import_id: UUID,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> ImportJob:
    job = await session.get(ImportJob, import_id)
    if job is None or job.player_id != player.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import not found")
    return job
