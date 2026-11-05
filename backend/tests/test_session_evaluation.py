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
    from chessmind.services.session_evaluation import wait_for_position_analysis

    headers = await _player(db, "session-evaluation@example.com")
    created = await client.post(
        "/sessions",
        json={"mode": "guided", "player_color": "white", "sampler_mode": "mainline", "seed": 21},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    current_fen = created.json()["state_json"]["current_fen"]
    await wait_for_position_analysis(current_fen)

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


async def test_session_evaluation_reports_missing_analysis_without_fabricating_values(client, db, monkeypatch):
    from chessmind.routers import sessions as sessions_router

    monkeypatch.setattr(sessions_router, "schedule_position_analysis", lambda _fen: None)
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
    assert result["analysis_pending"] is False
    assert result["eval_cp_white"] is None
    assert result["mate_white"] is None
    assert result["depth"] is None
    assert result["nodes"] is None
    assert result["engine_version"] is None


async def test_background_session_analysis_reuses_saved_cache_without_stockfish(db, monkeypatch):
    import chess

    from chessmind.models import Position
    from chessmind.services.canonical_fen import canonical_key
    from chessmind.services.engine import analyze_board
    from chessmind.services import session_evaluation

    board = chess.Board()
    seeded = await analyze_board(db, board=board, depth=10, multipv=3)
    await db.commit()
    before = await db.scalar(select(func.count()).select_from(EngineAnalysis))

    async def fail_if_cache_is_ignored(*_args, **_kwargs):
        raise AssertionError("background analysis must reuse any persisted Stockfish row")

    monkeypatch.setattr(session_evaluation, "analyze_board", fail_if_cache_is_ignored)
    session_evaluation.schedule_position_analysis(board.fen())
    await session_evaluation.wait_for_position_analysis(board.fen())

    after = await db.scalar(select(func.count()).select_from(EngineAnalysis))
    position = (
        await db.execute(select(Position).where(Position.canonical_key == canonical_key(board)))
    ).scalar_one()
    assert position.id == seeded.position_id
    assert after == before


async def test_session_evaluation_transitions_from_pending_to_real_stockfish_result(
    client, db, monkeypatch
):
    import asyncio

    from chessmind.services import session_evaluation

    headers = await _player(db, "session-evaluation-pending@example.com")
    entered = asyncio.Event()
    release = asyncio.Event()
    real_analyze = session_evaluation.analyze_board

    async def wait_then_analyze(session, *, board, lane="background"):
        entered.set()
        await release.wait()
        return await real_analyze(session, board=board, lane=lane)

    monkeypatch.setattr(session_evaluation, "analyze_board", wait_then_analyze)
    created = await client.post(
        "/sessions",
        json={"mode": "guided", "player_color": "white", "sampler_mode": "mainline", "seed": 24},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    current_fen = created.json()["state_json"]["current_fen"]
    await asyncio.wait_for(entered.wait(), timeout=5)

    try:
        pending = await client.get(f"/sessions/{session_id}/evaluation", headers=headers)
        assert pending.status_code == 200, pending.text
        assert pending.json()["analysis_available"] is False
        assert pending.json()["analysis_pending"] is True
    finally:
        release.set()
    await session_evaluation.wait_for_position_analysis(current_fen)
    complete = await client.get(f"/sessions/{session_id}/evaluation", headers=headers)
    assert complete.status_code == 200, complete.text
    assert complete.json()["current_fen"] == current_fen
    assert complete.json()["analysis_available"] is True
    assert complete.json()["analysis_pending"] is False
    assert complete.json()["depth"] >= 10
    assert complete.json()["nodes"] > 0
    assert complete.json()["perspective"] == "white"


async def test_session_evaluation_is_persisted_after_opponent_reply(client, db):
    from chessmind.services.session_evaluation import wait_for_position_analysis

    headers = await _player(db, "session-evaluation-opponent-reply@example.com")
    created = await client.post(
        "/sessions",
        json={"mode": "guided", "player_color": "white", "sampler_mode": "mainline", "seed": 25},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    initial_fen = created.json()["state_json"]["current_fen"]
    await wait_for_position_analysis(initial_fen)

    moved = await client.post(
        f"/sessions/{created.json()['id']}/move", json={"san": "e4"}, headers=headers
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["opponent_move"] is not None
    assert body["current_fen"]
    await wait_for_position_analysis(body["current_fen"])

    evaluation = await client.get(
        f"/sessions/{created.json()['id']}/evaluation", headers=headers
    )
    assert evaluation.status_code == 200, evaluation.text
    result = evaluation.json()
    assert result["current_fen"] == body["current_fen"]
    assert result["analysis_available"] is True
    assert result["analysis_pending"] is False
    assert result["depth"] >= 10
    assert result["nodes"] > 0


async def test_session_evaluation_is_persisted_after_opponent_opening(client, db):
    from chessmind.services.session_evaluation import wait_for_position_analysis

    headers = await _player(db, "session-evaluation-opponent-opening@example.com")
    created = await client.post(
        "/sessions",
        json={"mode": "guided", "player_color": "black", "sampler_mode": "mainline", "seed": 26},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    current_fen = created.json()["state_json"]["current_fen"]
    assert created.json()["state_json"]["move_history_san"]
    await wait_for_position_analysis(current_fen)

    evaluation = await client.get(f"/sessions/{session_id}/evaluation", headers=headers)
    assert evaluation.status_code == 200, evaluation.text
    result = evaluation.json()
    assert result["current_fen"] == current_fen
    assert result["analysis_available"] is True
    assert result["analysis_pending"] is False
    assert result["depth"] >= 10
    assert result["nodes"] > 0


async def test_failed_background_analysis_does_not_fail_move_or_opponent_response(
    client, db, monkeypatch
):
    from chessmind.services import session_evaluation

    headers = await _player(db, "session-evaluation-background-failure@example.com")

    async def fail_background_analysis(*_args, **_kwargs):
        raise RuntimeError("simulated background Stockfish failure")

    monkeypatch.setattr(session_evaluation, "analyze_board", fail_background_analysis)
    created = await client.post(
        "/sessions",
        json={"mode": "guided", "player_color": "white", "sampler_mode": "mainline", "seed": 27},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    from chessmind.services.session_evaluation import wait_for_position_analysis

    await wait_for_position_analysis(created.json()["state_json"]["current_fen"])
    moved = await client.post(
        f"/sessions/{created.json()['id']}/move", json={"san": "e4"}, headers=headers
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["played_san"] == "e4"
    assert body["classification"]
    assert body["opponent_move"]["san"]
    await wait_for_position_analysis(body["current_fen"])


async def test_session_evaluation_uses_latest_classification_from_same_session_position(client, db):
    from chessmind.services.session_evaluation import wait_for_position_analysis

    headers = await _player(db, "session-evaluation-classification@example.com")
    created = await client.post(
        "/sessions",
        json={"mode": "guided", "player_color": "white", "sampler_mode": "mainline", "seed": 23},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    await wait_for_position_analysis(created.json()["state_json"]["current_fen"])

    move_response = await client.post(f"/sessions/{session_id}/move", json={"san": "e4"}, headers=headers)
    assert move_response.status_code == 200, move_response.text
    move = move_response.json()
    session_state = await client.get(f"/sessions/{session_id}", headers=headers)
    current_fen = session_state.json()["state_json"]["current_fen"]
    assert move["current_fen"] == current_fen

    # Make exact-current-position evidence available using real Stockfish; GET evaluation must not search.
    analyzed = await client.post(
        "/analysis/position",
        json={"fen": current_fen, "depth": 8, "multipv": 1},
        headers=headers,
    )
    assert analyzed.status_code == 200, analyzed.text
    result = await client.get(f"/sessions/{session_id}/evaluation", headers=headers)
    assert result.status_code == 200, result.text
    evaluation = result.json()
    assert evaluation["current_fen"] == current_fen
    assert evaluation["latest_move_classification"] == move["classification"]
