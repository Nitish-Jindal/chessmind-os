"""Session evaluation reads persisted Stockfish evidence without triggering searches."""

from sqlalchemy import func, select

from chessmind.models import EngineAnalysis, Player
from chessmind.security import create_access_token, hash_password


async def _player(db, email: str) -> dict[str, str]:
    player = Player(
        email=email,
        password_hash=hash_password("secure-test-password"),
        display_name="Evaluation tester",
        preferred_color="white",
        settings_json={},
    )
    db.add(player)
    await db.commit()
    await db.refresh(player)
    return {"Authorization": f"Bearer {create_access_token(player.id)}"}


async def test_session_evaluation_reads_real_cached_stockfish_data_without_search(client, db):
    headers = await _player(db, "session-evaluation@example.com")
    created = await client.post(
        "/sessions",
        json={"mode": "guided", "player_color": "white", "sampler_mode": "mainline", "seed": 21},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    current_fen = created.json()["state_json"]["current_fen"]

    # Populate a persisted result through the real Stockfish-backed analysis endpoint.
    analyzed = await client.post(
        "/analysis/position",
        json={"fen": current_fen, "depth": 8, "multipv": 1},
        headers=headers,
    )
    assert analyzed.status_code == 200, analyzed.text
    engine_result = analyzed.json()
    assert engine_result["engine_version"]
    assert engine_result["depth"] >= 8
    assert engine_result["nodes"] > 0
    assert engine_result["perspective"] == "white"
    assert engine_result["results"]
    before_count = await db.scalar(select(func.count()).select_from(EngineAnalysis))

    response = await client.get(f"/sessions/{session_id}/evaluation", headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["session_id"] == session_id
    assert result["current_fen"] == current_fen
    assert result["analysis_available"] is True
    assert result["eval_cp_white"] == engine_result["results"][0]["cp"]
    assert result["mate_white"] == engine_result["results"][0]["mate"]
    assert result["depth"] == engine_result["depth"]
    assert result["nodes"] == engine_result["nodes"]
    assert result["engine_version"] == engine_result["engine_version"]
    assert result["perspective"] == "white"
    after_count = await db.scalar(select(func.count()).select_from(EngineAnalysis))
    assert after_count == before_count


async def test_session_evaluation_reports_missing_analysis_without_fabricating_values(client, db):
    headers = await _player(db, "session-evaluation-empty@example.com")
    created = await client.post(
        "/sessions",
        json={"mode": "guided", "player_color": "white", "sampler_mode": "mainline", "seed": 22},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]

    response = await client.get(f"/sessions/{session_id}/evaluation", headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["analysis_available"] is False
    assert result["eval_cp_white"] is None
    assert result["mate_white"] is None
    assert result["depth"] is None
    assert result["nodes"] is None
    assert result["engine_version"] is None
