"""Real UCI processes only: deadlines, process death, admission and HTTP isolation."""

import asyncio
import os
import signal
import time

import chess
import pytest
from chessmind.config import get_settings
from chessmind.services.engine import (
    EngineBusy,
    EngineUnavailable,
    background_engine_client,
    engine_client,
)


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_timeout_kills_process_and_next_search_recovers(monkeypatch):
    await engine_client.analyze(chess.Board(), 4, 1)
    old = engine_client._engine
    pid = old.transport.get_pid()
    monkeypatch.setattr(get_settings(), "stockfish_interactive_timeout_sec", 0.001)
    started = time.monotonic()
    with pytest.raises(EngineUnavailable):
        await engine_client.analyze(chess.Board(), 128, 1)
    assert time.monotonic() - started < 1
    assert old.returncode.done()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    monkeypatch.setattr(get_settings(), "stockfish_interactive_timeout_sec", 3)
    version, lines, nodes, _ = await engine_client.analyze(chess.Board(), 4, 1)
    assert version and nodes > 0
    assert chess.Move.from_uci(lines[0]["pv_uci"][0]) in chess.Board().legal_moves
    assert engine_client._engine.transport.get_pid() != pid
    print(f"timeout recovery: old_pid={pid}, new_pid={engine_client._engine.transport.get_pid()}")


@pytest.mark.asyncio
async def test_actual_kill_during_analysis_and_recovery():
    task = asyncio.create_task(engine_client.analyze(chess.Board(), 128, 1))
    await engine_client.active.wait()
    old = engine_client._engine
    os.kill(old.transport.get_pid(), signal.SIGKILL)
    with pytest.raises(EngineUnavailable):
        await asyncio.wait_for(task, 1)
    assert old.returncode.done()
    assert (await engine_client.analyze(chess.Board(), 5, 1))[2] > 0
    assert not engine_client._lock.locked()


@pytest.mark.asyncio
async def test_background_capacity_cancellation_and_interactive_admission(monkeypatch):
    bg = background_engine_client
    monkeypatch.setattr(get_settings(), "stockfish_background_queue_max", 2)
    running = asyncio.create_task(bg.analyze(chess.Board(), 128, 1))
    await bg.active.wait()
    old = bg._engine
    os.kill(old.transport.get_pid(), signal.SIGSTOP)
    queued = [asyncio.create_task(bg.analyze(chess.Board(), 4, 1)) for _ in range(2)]
    await wait_until(lambda: bg._waiting == 2)
    with pytest.raises(EngineBusy):
        await bg.analyze(chess.Board(), 4, 1)
    for task in queued:
        task.cancel()
    await asyncio.gather(*queued, return_exceptions=True)
    assert bg._waiting == 0
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    assert old.returncode.done()
    assert not bg._lock.locked()
    assert (await bg.analyze(chess.Board(), 4, 1))[2] > 0

    running = asyncio.create_task(engine_client.analyze(chess.Board(), 128, 1))
    await engine_client.active.wait()
    old = engine_client._engine
    os.kill(old.transport.get_pid(), signal.SIGSTOP)
    monkeypatch.setattr(get_settings(), "stockfish_interactive_queue_timeout_sec", 0.02)
    with pytest.raises(EngineBusy):
        await engine_client.analyze(chess.Board(), 4, 1)
    assert engine_client._engine is old  # admission timeout cannot kill another owner
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    monkeypatch.setattr(get_settings(), "stockfish_interactive_queue_timeout_sec", 1)
    # Real concurrent searches serialize and all PVs stay legal.
    results = await asyncio.gather(
        engine_client.analyze(chess.Board(), 4, 1),
        engine_client.analyze(chess.Board(), 4, 1),
    )
    assert all(
        chess.Move.from_uci(r[1][0]["pv_uci"][0]) in chess.Board().legal_moves for r in results
    )
    assert engine_client._waiting == 0


@pytest.mark.asyncio
async def test_move_http_during_real_background_search(client):
    response = await client.post(
        "/auth/register",
        json={
            "email": "lanes@chessmind.example.com",
            "password": "testpassword123",
            "display_name": "Engine lanes",
        },
    )
    assert response.status_code == 201, response.text
    login = await client.post(
        "/auth/login",
        json={
            "email": "lanes@chessmind.example.com",
            "password": "testpassword123",
        },
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = await client.post(
        "/sessions",
        headers=headers,
        json={
            "mode": "guided",
            "player_color": "white",
            "seed": 42,
            "opening_request": "Italian Game",
        },
    )
    assert created.status_code == 201, created.text
    # Drain the automatically scheduled starting-position evaluation first.
    from chessmind.services.session_evaluation import wait_for_pending_analyses

    await wait_for_pending_analyses()
    bg = background_engine_client
    task = asyncio.create_task(bg.analyze(chess.Board(), 128, 1))
    await bg.active.wait()
    # Freeze a real search to make independence reproducible on any CPU speed.
    os.kill(bg._engine.transport.get_pid(), signal.SIGSTOP)
    try:
        started = time.monotonic()
        response = await client.post(
            f"/sessions/{created.json()['id']}/move",
            headers=headers,
            json={"san": "e4", "latency_ms": 100},
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 200, response.text
        assert response.json()["played_uci"] == "e2e4"
        board = chess.Board()
        board.push_san("e4")
        opponent = response.json().get("opponent_move")
        if opponent:
            board.push_uci(opponent["uci"])
        assert response.json()["current_fen"] == board.fen()
        assert elapsed < get_settings().stockfish_interactive_timeout_sec + 1
        assert not task.done()
        assert engine_client._lock is not bg._lock
        assert engine_client._engine.transport.get_pid() != bg._engine.transport.get_pid()
        print(f"interactive HTTP move during stopped background search: {elapsed:.3f}s")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_bounded_import_admission_releases_slots(monkeypatch):
    from uuid import uuid4

    from chessmind.services import background_jobs

    monkeypatch.setattr(get_settings(), "stockfish_background_queue_max", 1)
    bg = background_engine_client
    try:
        assert background_jobs.submit_job(uuid4(), lambda: bg.analyze(chess.Board(), 128, 1))
        await bg.active.wait()
        os.kill(bg._engine.transport.get_pid(), signal.SIGSTOP)
        assert background_jobs.submit_job(uuid4(), lambda: bg.analyze(chess.Board(), 4, 1))
        assert not background_jobs.submit_job(uuid4(), lambda: bg.analyze(chess.Board(), 4, 1))
        tasks = list(background_jobs._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert not background_jobs._tasks
        assert not bg._lock.locked()
        assert background_jobs.submit_job(uuid4(), lambda: bg.analyze(chess.Board(), 4, 1))
        await asyncio.gather(*background_jobs._tasks)
        assert not background_jobs._tasks
    finally:
        tasks = list(background_jobs._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        background_jobs._lock = asyncio.Lock()


@pytest.mark.asyncio
async def test_same_new_fen_does_not_hold_database_key_during_background_search(db):
    from chessmind.db import AsyncSessionLocal
    from chessmind.services.engine import analyze_board

    board = chess.Board()
    board.push_san("e4")
    board.push_san("Nf6")

    async def background():
        async with AsyncSessionLocal() as session:
            await analyze_board(session, board=board, depth=128, multipv=1, lane="background")
            await session.commit()

    bg = background_engine_client
    task = asyncio.create_task(background())
    await bg.active.wait()
    os.kill(bg._engine.transport.get_pid(), signal.SIGSTOP)
    try:
        async with asyncio.timeout(2):
            result = await analyze_board(db, board=board, depth=4, multipv=1)
        assert result.nodes > 0
        assert chess.Move.from_uci(result.results[0]["pv_uci"][0]) in board.legal_moves
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
