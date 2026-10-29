"""Proof (c): 100+ opponent PGNs → opponent-conditioned session.

Composes a 120-game opponent library skewed to a signature opening
(the opponent plays King's Indian Defense as Black almost every time,
and the English with White), imports it via /opponents/{id}/import,
then starts a session in opponent_twin mode and verifies the first
opponent reply is drawn from the opponent tier (not the generic
opening graph).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid

import chess
import chess.pgn
import httpx

HERE = os.path.dirname(os.path.abspath(__file__))

OPPONENT_NAME = "KID_Specialist"


def compose_opponent_pgn(count: int) -> str:
    """Build ``count`` games where OPPONENT plays KID as black and English as white."""
    kid_lines = [
        ("d4", "Nf6", "c4", "g6", "Nc3", "Bg7", "e4", "d6", "Nf3", "O-O"),
        ("d4", "Nf6", "c4", "g6", "Nc3", "Bg7", "e4", "d6", "f3", "O-O"),
        ("d4", "Nf6", "c4", "g6", "g3", "Bg7", "Bg2", "O-O", "Nc3", "d6"),
        ("Nf3", "Nf6", "c4", "g6", "Nc3", "Bg7", "d4", "O-O", "e4", "d6"),
    ]
    english_as_white = [
        ("c4", "e5", "Nc3", "Nf6", "g3", "Bb4", "Bg2", "O-O", "Nf3", "e4"),
        ("c4", "c5", "Nf3", "Nc6", "g3", "g6", "Bg2", "Bg7", "Nc3", "e6"),
    ]
    games: list[str] = []
    results = ["1-0", "0-1", "1/2-1/2"]
    for i in range(count):
        as_black = i % 3 != 0  # 2/3 of games as black with KID
        if as_black:
            white, black = f"Opponent{i}", OPPONENT_NAME
            line = kid_lines[i % len(kid_lines)]
            eco = "E60"
            opening = "King's Indian Defense"
        else:
            white, black = OPPONENT_NAME, f"Opponent{i}"
            line = english_as_white[i % len(english_as_white)]
            eco = "A20"
            opening = "English Opening"
        game = chess.pgn.Game()
        game.headers["Event"] = f"Opp Series {i:03d}"
        game.headers["Site"] = "chessmind-seed"
        game.headers["Date"] = "2026.09.15"
        game.headers["Round"] = str(i + 1)
        game.headers["White"] = white
        game.headers["Black"] = black
        game.headers["Result"] = results[i % 3]
        game.headers["ECO"] = eco
        game.headers["Opening"] = opening
        board = chess.Board()
        node = game
        for san in line:
            move = board.parse_san(san)
            node = node.add_variation(move)
            board.push(move)
        exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
        games.append(game.accept(exporter))
    return "\n\n".join(games) + "\n"


async def main(args: argparse.Namespace) -> int:
    email = f"proof-c-{uuid.uuid4().hex[:6]}@chessmind.example.com"
    password = "proofpassword123"
    transcript: dict[str, object] = {"proof": "opponent_conditioned_session", "email": email}

    pgn = compose_opponent_pgn(args.games)
    print(f"composed {args.games} opponent games")

    async with httpx.AsyncClient(base_url=args.base_url, timeout=180.0) as client:
        await client.post("/auth/register", json={
            "email": email, "password": password,
            "display_name": "Opp Proof", "preferred_color": "white",
        })
        tok = (await client.post("/auth/login", json={"email": email, "password": password})).json()["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}

        r = await client.post("/opponents", headers=hdr, json={
            "display_name": OPPONENT_NAME,
            "aliases": [OPPONENT_NAME.lower()],
        })
        r.raise_for_status()
        opp = r.json()
        opp_id = opp["id"]
        transcript["opponent_profile_id"] = opp_id

        r = await client.post(f"/opponents/{opp_id}/import", headers=hdr,
                              json={"pgn": pgn, "source": "proof"})
        r.raise_for_status()
        job_id = r.json()["id"]

        start = time.monotonic()
        for _ in range(60):
            r = await client.get(f"/imports/{job_id}", headers=hdr)
            r.raise_for_status()
            jb = r.json()
            if jb["status"] in ("succeeded", "failed", "partial"):
                break
            await asyncio.sleep(1.0)
        print(f"import {jb['status']} in {time.monotonic()-start:.1f}s: {jb['artifacts_json']}")
        transcript["import_job"] = jb
        assert jb["status"] in ("succeeded", "partial"), f"import failed: {jb.get('error')}"

        # Opponent tree against startpos: as black (since we play 1.d4 to invite KID).
        board = chess.Board()
        board.push_san("d4")
        r = await client.get(f"/opponents/{opp_id}/tree",
                             params={"fen": board.fen(), "limit": 10}, headers=hdr)
        r.raise_for_status()
        tree = r.json()
        print(f"opponent tree after 1.d4: {len(tree['edges'])} edges")
        for e in tree["edges"]:
            print(f"  {e['move_san']:<4}  games={e['games_count']:>3}  "
                  f"bucket={e['sample_size_bucket']}  conf={e['confidence']}")
        transcript["opponent_tree_after_d4"] = tree
        assert tree["edges"], "opponent tree is empty — materializer did nothing"
        assert any(e["move_san"].lower().startswith("n") and "f6" in e["move_san"].lower()
                   for e in tree["edges"]), "expected Nf6 in opponent's KID repertoire"

        # Session with opponent_twin
        r = await client.post("/sessions", headers=hdr, json={
            "mode": "opponent", "player_color": "white",
            "sampler_mode": "opponent_twin", "seed": 7, "novelty_prob": 0.0,
            "opponent_profile_id": opp_id,
        })
        r.raise_for_status()
        session_id = r.json()["id"]
        r = await client.post(f"/sessions/{session_id}/move", headers=hdr,
                              json={"san": "d4", "latency_ms": 400})
        r.raise_for_status()
        move = r.json()
        opp_move = move.get("opponent_move") or {}
        print(f"opponent played {opp_move.get('san')!r} tier={opp_move.get('source_tier')} "
              f"confidence={opp_move.get('confidence')} sample_size={opp_move.get('sample_size')}")
        transcript["first_opponent_move"] = opp_move
        assert opp_move.get("source_tier") == "opponent_profile", \
            f"expected opponent_profile tier, got {opp_move.get('source_tier')}"
        assert "f6" in (opp_move.get("san") or "").lower() or opp_move.get("san") == "Nf6", \
            f"expected Nf6 from KID specialist, got {opp_move.get('san')}"

    out = os.path.join(HERE, "data", "proof_opponent.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(transcript, fh, indent=2, default=str)
    print(f"transcript → {out}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--games", type=int, default=120)
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
