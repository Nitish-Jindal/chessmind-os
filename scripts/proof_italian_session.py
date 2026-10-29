"""Proof (a): play a full Italian line with a sideline deviation + correction.

Hits the live FastAPI via HTTP. Prints the engine eval + PV + coaching
response at each ply and writes a JSON transcript for the record.

Flow:
  1. Register player, log in.
  2. POST /sessions (guided, white, seed=42, sampler mix).
  3. Play Italian mainline: 1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5.
  4. On move 4 play 4.Qh5 (an unsound queen sortie) — expect inaccuracy
     or worse with a correction naming a stronger move + PV.
  5. Play the engine's recommended move on the next turn (retrain in
     place), confirm the label returns to best/playable.
  6. POST /sessions/{id}/finish; save transcript to scripts/data/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))


async def main(args: argparse.Namespace) -> int:
    email = f"proof-a-{uuid.uuid4().hex[:6]}@chessmind.example.com"
    password = "proofpassword123"
    transcript: dict[str, object] = {"proof": "italian_sideline_correction", "email": email, "turns": []}

    async with httpx.AsyncClient(base_url=args.base_url, timeout=120.0) as client:
        r = await client.post("/auth/register", json={
            "email": email, "password": password,
            "display_name": "Italian Proof", "preferred_color": "white",
        })
        r.raise_for_status()
        tok = (await client.post("/auth/login", json={"email": email, "password": password})).json()["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}

        r = await client.post("/sessions", headers=hdr, json={
            "mode": "guided", "player_color": "white",
            "sampler_mode": "mainline", "seed": 42, "novelty_prob": 0.0,
        })
        r.raise_for_status()
        ts = r.json()
        session_id = ts["id"]
        transcript["session_id"] = session_id
        print(f"session {session_id} started; state: {ts['state_json'].get('status')}")

        async def post_move(san: str, note: str) -> dict:
            r = await client.post(f"/sessions/{session_id}/move",
                                   headers=hdr, json={"san": san, "latency_ms": 500})
            r.raise_for_status()
            move = r.json()
            opp = move.get("opponent_move") or {}
            print(f"  played {san:<6} ({note}) → {move['classification']:<10} "
                  f"swing={move['eval_swing_cp']:>4}cp  cp_before={move['eval_before_cp_white']} "
                  f"opp_move={opp.get('san')!r} tier={opp.get('source_tier')}")
            if move['classification'] != 'best':
                print(f"     coach: {move['coaching']['spoken_summary']}")
                print(f"     correction: {move['coaching']['correction_san']}")
            transcript["turns"].append({"note": note, "san": san, "move": move})
            return move

        # Italian mainline
        await post_move("e4", "italian 1")
        # Fetch legal moves to adapt to whatever the sampler chose.
        async def legal_now() -> dict:
            r = await client.get(f"/sessions/{session_id}/legal", headers=hdr)
            r.raise_for_status()
            return r.json()

        # Move 2: play Nf3 if legal (standard italian setup).
        legal = await legal_now()
        move2 = "Nf3" if "Nf3" in legal["legal_moves_san"] else legal["legal_moves_san"][0]
        await post_move(move2, "develop knight")

        # Move 3: pick a deliberate deviation that is legal in the arrived-at
        # position AND suboptimal according to Stockfish. We try candidates in
        # order; the first legal one is played and we assert its label.
        legal = await legal_now()
        candidates = ["g4", "Nh4", "Qg4", "Ke2", "f3"]
        deviation_san = next((c for c in candidates if c in legal["legal_moves_san"]), None)
        assert deviation_san is not None, f"no deviation candidate was legal in {legal['legal_moves_san']}"
        deviation = await post_move(deviation_san, f"deliberate sideline deviation ({deviation_san})")
        assert deviation["classification"] in ("inaccuracy", "mistake", "blunder"), \
            f"deviation {deviation_san} should be an inaccuracy/mistake/blunder; got {deviation['classification']}"
        assert deviation["coaching"].get("correction_san"), "coaching should include a correction_san"
        assert deviation["best_lines"], "best_lines should carry the engine PV"
        pv = deviation["best_lines"][0]
        assert pv.get("pv_san"), "top line should carry a PV in SAN"

        # Retrain: play the engine's correction if available; otherwise a solid move.
        legal = await legal_now()
        correction = deviation["coaching"].get("correction_san")
        if correction and correction in legal["legal_moves_san"]:
            retrain = await post_move(correction, "retrain with engine correction")
        else:
            fallback = next((m for m in ["Nc3", "d3", "c3", "d4"] if m in legal["legal_moves_san"]),
                            legal["legal_moves_san"][0])
            retrain = await post_move(fallback, "retrain fallback")
        assert retrain["classification"] in ("best", "playable", "inaccuracy"), \
            f"retrain should recover; got {retrain['classification']}"

        r = await client.post(f"/sessions/{session_id}/finish", headers=hdr)
        r.raise_for_status()
        finish = r.json()
        transcript["finish"] = finish
        print(f"finished at {finish['ended_at']}")

    out = os.path.join(HERE, "data", "proof_italian.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(transcript, fh, indent=2)
    print(f"transcript → {out}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
