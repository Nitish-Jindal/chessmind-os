"""Live HTTP isolation and subprocess-kill recovery; uses a dedicated real database.

Run with DATABASE_URL pointing at an isolated proof DB migrated with Alembic,
STOCKFISH_PATH set to a real binary, and the backend installed in this interpreter.
Starts one real uvicorn process on 8001. Never prints credentials or tokens.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import chess
import httpx


async def main():
    with tempfile.TemporaryDirectory() as directory:
        log_path = Path(directory) / "api.log"
        with log_path.open("w") as log:
            env = dict(os.environ, STOCKFISH_BACKGROUND_DEPTH="40", LICHESS_EXPLORER_ENABLED="false")
            process = subprocess.Popen([
                sys.executable, "-m", "uvicorn", "chessmind.app:app", "--host", "127.0.0.1",
                "--port", "8001", "--workers", "1",
            ], env=env, stdout=log, stderr=log)
            try:
                async with httpx.AsyncClient(base_url="http://127.0.0.1:8001", timeout=20) as client:
                    async with asyncio.timeout(10):
                        while True:
                            try:
                                if (await client.get('/health')).status_code == 200:
                                    break
                            except httpx.ConnectError:
                                pass
                            await asyncio.sleep(0.01)
                    email = f"http-engine-{uuid4().hex}@chessmind.example.com"
                    registration = await client.post('/auth/register', json={
                        'email': email, 'password': 'proofpassword123', 'display_name': 'HTTP engine proof',
                    })
                    registration.raise_for_status()
                    login = await client.post('/auth/login', json={'email': email, 'password': 'proofpassword123'})
                    client.headers['Authorization'] = f"Bearer {login.json()['access_token']}"
                    created = await client.post('/sessions', json={
                        'mode': 'guided', 'player_color': 'white', 'sampler_mode': 'mainline',
                        'seed': 42, 'opening_request': 'Italian Game',
                    })
                    created.raise_for_status()
                    sid = created.json()['id']
                    background_request = asyncio.create_task(client.post('/analysis/position', json={
                        'fen': created.json()['state_json']['current_fen'], 'depth': 40, 'multipv': 1,
                    }))
                    async with asyncio.timeout(5):
                        while not (match := re.search(r'restart success lane=background pid=(\d+)', log_path.read_text())):
                            await asyncio.sleep(0.01)
                    background_pid = int(match[1])
                    started = time.monotonic()
                    first = await client.post(f'/sessions/{sid}/move', json={'san': 'e4'})
                    latency = time.monotonic() - started
                    first.raise_for_status()
                    assert first.json()['played_uci'] == 'e2e4'
                    assert 'engine finished lane=background' not in log_path.read_text(), 'background finished before move'
                    # The same server stays alive after deliberately killing its interactive engine.
                    interactive_pid = int(re.findall(r'restart success lane=interactive pid=(\d+)', log_path.read_text())[-1])
                    os.kill(interactive_pid, signal.SIGKILL)
                    started = time.monotonic()
                    second = await client.post(f'/sessions/{sid}/move', json={'san': 'Nf3'})
                    recovery_latency = time.monotonic() - started
                    second.raise_for_status()
                    assert second.json()['played_uci'] == 'g1f3'
                    replacement_pid = int(re.findall(r'restart success lane=interactive pid=(\d+)', log_path.read_text())[-1])
                    assert replacement_pid != interactive_pid
                    board = chess.Board(first.json()['current_fen'])
                    board.push_san('Nf3')
                    if opponent := second.json().get('opponent_move'):
                        board.push_uci(opponent['uci'])
                    assert board.fen() == second.json()['current_fen']
                    evidence = {
                        'api_pid': process.pid, 'background_pid': background_pid,
                        'interactive_pid': interactive_pid, 'replacement_pid': replacement_pid,
                        'move_during_background_seconds': latency,
                        'move_after_process_kill_seconds': recovery_latency,
                        'background_search_finished_before_first_move': False,
                        'first_move': first.json(), 'recovery_move': second.json(),
                    }
                    output = Path('scripts/data/engine-isolation')
                    output.mkdir(parents=True, exist_ok=True)
                    (output / 'http.json').write_text(json.dumps(evidence, indent=2))
                    background_response = await background_request
                    evidence['background_status'] = background_response.status_code
                    (output / 'http.json').write_text(json.dumps(evidence, indent=2))
                    print(json.dumps({k: v for k, v in evidence.items() if not k.endswith('move')}, indent=2))
            finally:
                process.terminate()
                await asyncio.to_thread(process.wait, 15)
                Path('scripts/data/engine-isolation/http-server.log').write_text(log_path.read_text())


if __name__ == '__main__':
    asyncio.run(main())
