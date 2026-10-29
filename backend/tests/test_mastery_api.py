"""GET /players/{id}/mastery/{position_id} — single-row lookup used by the drill UI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import chess

from chessmind.models import MasteryState, Player
from chessmind.security import create_access_token, hash_password
from chessmind.services.opening_graph import upsert_position


async def _player(db, email: str) -> Player:
    p = Player(
        email=email,
        password_hash=hash_password("passwordpassword"),
        display_name="M",
        preferred_color="either",
        settings_json={},
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def test_mastery_row_lookup_and_next(client, db):
    p = await _player(db, "mrow@example.com")
    other = await _player(db, "mrow2@example.com")
    hdr = {"Authorization": f"Bearer {create_access_token(p.id)}"}
    pos_id = await upsert_position(db, chess.Board())
    db.add(
        MasteryState(
            id=uuid4(), player_id=p.id, position_id=pos_id, state="decayed",
            due_at=datetime.now(timezone.utc) - timedelta(minutes=1), reps=0,
        )
    )
    await db.commit()

    r = await client.get(f"/players/{p.id}/mastery/{pos_id}", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "decayed" and r.json()["position_id"] == str(pos_id)

    r = await client.post(f"/players/{p.id}/mastery/next", headers=hdr)
    assert r.json()["due_now"] is True and r.json()["row"]["position_id"] == str(pos_id)

    r = await client.get(f"/players/{p.id}/mastery/{uuid4()}", headers=hdr)
    assert r.status_code == 404
    r = await client.get(f"/players/{other.id}/mastery/{pos_id}", headers=hdr)
    assert r.status_code == 403
