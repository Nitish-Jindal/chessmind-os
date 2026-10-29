"""/players/me — profile read / patch."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from chessmind.deps import get_current_player
from chessmind.models import Player
from chessmind.schemas.player import PlayerRead, PlayerUpdate

router = APIRouter(prefix="/players", tags=["players"])


@router.get("/me", response_model=PlayerRead)
async def me(player: Player = Depends(get_current_player)) -> Player:
    return player


@router.patch("/me", response_model=PlayerRead)
async def update_me(
    patch: PlayerUpdate,
    player: Player = Depends(get_current_player),
) -> Player:
    data = patch.model_dump(exclude_unset=True)
    settings_patch = data.pop("settings_json", None)
    for key, value in data.items():
        setattr(player, key, value)
    if settings_patch is not None:
        # merge rather than replace, so partial PATCHes don't wipe prior keys
        merged = dict(player.settings_json or {})
        merged.update(settings_patch)
        player.settings_json = merged
    return player
