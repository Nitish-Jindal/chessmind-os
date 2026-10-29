"""Registration, login, token refresh."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import get_session
from chessmind.models import Player
from chessmind.schemas.player import (
    PlayerCreate,
    PlayerLogin,
    PlayerRead,
    RefreshRequest,
    TokenPair,
)
from chessmind.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens_for(player_id) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(player_id),
        refresh_token=create_refresh_token(player_id),
    )


@router.post("/register", response_model=PlayerRead, status_code=status.HTTP_201_CREATED)
async def register(payload: PlayerCreate, session: AsyncSession = Depends(get_session)) -> Player:
    player = Player(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        preferred_color=payload.preferred_color,
        settings_json={},
    )
    session.add(player)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from exc
    return player


@router.post("/login", response_model=TokenPair)
async def login(payload: PlayerLogin, session: AsyncSession = Depends(get_session)) -> TokenPair:
    result = await session.execute(select(Player).where(Player.email == payload.email))
    player = result.scalar_one_or_none()
    if player is None or not verify_password(payload.password, player.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return _tokens_for(player.id)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, session: AsyncSession = Depends(get_session)) -> TokenPair:
    try:
        player_id = decode_token(payload.refresh_token, expected_kind="refresh")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    player = await session.get(Player, player_id)
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "player not found")
    return _tokens_for(player.id)
