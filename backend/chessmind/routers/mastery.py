"""/players/{id}/mastery — dashboard + next-drill picker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import get_session
from chessmind.deps import get_current_player
from chessmind.models import MasteryState, Player, Position
from chessmind.schemas.mastery import (
    MasteryByState,
    MasteryDashboard,
    MasteryRow,
    NextDrillResponse,
)

router = APIRouter(prefix="/players", tags=["mastery"])


def _ensure_self(player_id: UUID, requester: Player) -> None:
    if player_id != requester.id and requester.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot access another player's mastery")


async def _row(session: AsyncSession, m: MasteryState) -> MasteryRow:
    pos = await session.get(Position, m.position_id)
    return MasteryRow(
        position_id=m.position_id,
        canonical_key=pos.canonical_key if pos else "",
        fen=pos.fen if pos else "",
        opening_name=pos.opening_name if pos else None,
        state=m.state,
        recall_score=m.recall_score,
        understanding_score=m.understanding_score,
        visualization_score=m.visualization_score,
        speed_score=m.speed_score,
        due_at=m.due_at,
        last_tested_at=m.last_tested_at,
        ease_factor=m.ease_factor,
        interval_days=m.interval_days,
        reps=m.reps,
    )


@router.get("/{player_id}/mastery", response_model=MasteryDashboard)
async def get_mastery(
    player_id: UUID,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> MasteryDashboard:
    _ensure_self(player_id, player)

    counts_rows = await session.execute(
        select(MasteryState.state, func.count(MasteryState.id))
        .where(MasteryState.player_id == player_id)
        .group_by(MasteryState.state)
    )
    counts = MasteryByState()
    for state_name, n in counts_rows.all():
        setattr(counts, state_name, n)

    now = datetime.now(timezone.utc)
    due_now = (
        await session.execute(
            select(func.count(MasteryState.id)).where(
                MasteryState.player_id == player_id,
                MasteryState.due_at <= now,
            )
        )
    ).scalar_one()
    due_24h = (
        await session.execute(
            select(func.count(MasteryState.id)).where(
                MasteryState.player_id == player_id,
                MasteryState.due_at <= now + timedelta(hours=24),
            )
        )
    ).scalar_one()

    weakest_rows = (
        await session.execute(
            select(MasteryState)
            .where(MasteryState.player_id == player_id, MasteryState.reps > 0)
            .order_by(MasteryState.recall_score.asc(), MasteryState.due_at.asc())
            .limit(10)
        )
    ).scalars().all()
    due_soon_rows = (
        await session.execute(
            select(MasteryState)
            .where(MasteryState.player_id == player_id)
            .order_by(MasteryState.due_at.asc())
            .limit(10)
        )
    ).scalars().all()
    return MasteryDashboard(
        counts_by_state=counts,
        due_now=due_now,
        due_next_24h=due_24h,
        weakest=[await _row(session, m) for m in weakest_rows],
        due_soon=[await _row(session, m) for m in due_soon_rows],
    )


@router.post("/{player_id}/mastery/next", response_model=NextDrillResponse)
async def next_drill(
    player_id: UUID,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> NextDrillResponse:
    _ensure_self(player_id, player)
    now = datetime.now(timezone.utc)
    row = (
        await session.execute(
            select(MasteryState)
            .where(MasteryState.player_id == player_id, MasteryState.due_at <= now)
            .order_by(MasteryState.due_at.asc(), MasteryState.recall_score.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        upcoming = (
            await session.execute(
                select(MasteryState)
                .where(MasteryState.player_id == player_id)
                .order_by(MasteryState.due_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if upcoming is None:
            return NextDrillResponse(due_now=False, row=None, reason="no mastery rows yet")
        return NextDrillResponse(
            due_now=False, row=await _row(session, upcoming), reason="next due in the future"
        )
    return NextDrillResponse(due_now=True, row=await _row(session, row), reason="due now")
