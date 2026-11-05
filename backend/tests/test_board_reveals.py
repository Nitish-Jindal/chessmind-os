"""Unlimited manual board reveals stay separate from hints and recall scores."""

import pytest
from sqlalchemy import select

from chessmind.models import Player, TrainingAttempt
from chessmind.security import create_access_token, hash_password


async def _player(db, email: str) -> dict[str, str]:
    player = Player(
        email=email,
        password_hash=hash_password("manual-reveal-test-password"),
        display_name="Reveal tester",
        preferred_color="white",
        settings_json={
            # Legacy settings must not restore the old quota or penalty.
            "blindfold_free_reveals_per_session": 2,
            "blindfold_reveal_penalty": 0.5,
        },
    )
    db.add(player)
    await db.commit()
    await db.refresh(player)
    return {"Authorization": f"Bearer {create_access_token(player.id)}"}


@pytest.mark.parametrize("quiet", [False, True], ids=["coaching", "quiet"])
async def test_blindfold_reveals_are_unlimited_and_do_not_affect_hints_or_score(client, db, quiet):
    headers = await _player(db, f"manual-reveal-{quiet}@example.com")
    created = await client.post(
        "/sessions",
        json={"mode": "blindfold", "player_color": "white", "sampler_mode": "mainline", "seed": 913, "quiet": quiet},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    current_fen = created.json()["state_json"]["current_fen"]

    # Five explicit reveal requests exceed the old two-reveal budget.
    for count in range(1, 6):
        response = await client.post(f"/sessions/{session_id}/reveal", json={}, headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["reveals_used"] == count
        assert result["reveals_budget"] is None
        assert result["was_free"] is True
        assert result["hints_used"] == 0
        assert result["current_fen"] == current_fen

    legal = await client.get(f"/sessions/{session_id}/legal", headers=headers)
    assert legal.status_code == 200, legal.text
    assert legal.json()["reveals_used"] == 5
    assert legal.json()["reveals_budget"] is None
    assert legal.json()["hints_used"] == 0

    # A real legal move exercises the normal Stockfish-backed move path. Reveals
    # remain absent from the attempt's hint and visualization scoring fields.
    move = await client.post(f"/sessions/{session_id}/move", json={"san": "e4"}, headers=headers)
    assert move.status_code == 200, move.text
    attempt = await db.scalar(select(TrainingAttempt).where(TrainingAttempt.id == move.json()["attempt_id"]))
    assert attempt is not None
    assert attempt.hints_used == 0
    assert attempt.visualization_score == 1.0
