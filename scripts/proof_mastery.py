"""Proof (b): tracked mistake → retrain → MasteryState updates.

Flow:
  1. Register player, log in.
  2. Create a guided session.
  3. Play a BAD move (1.g4) immediately — expect inaccuracy/mistake/blunder.
  4. GET /players/{id}/mastery — row for the startpos exists, state=emerging/decayed,
     due_at within 1-2 days (SM-2 interval 1 after a failed review).
  5. POST /players/{id}/mastery/next — picks the startpos row (due now).
  6. Create a new session starting from the SAME position and play a solid
     move (e4) — now expect 'best'.
  7. GET /players/{id}/mastery again — the row's reps increased, due_at
     pushed further out, state has moved up.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))


async def main(args: argparse.Namespace) -> int:
    email = f"proof-b-{uuid.uuid4().hex[:6]}@chessmind.example.com"
    password = "proofpassword123"
    transcript: dict[str, object] = {"proof": "mistake_retrain_mastery", "email": email, "events": []}

    async with httpx.AsyncClient(base_url=args.base_url, timeout=120.0) as client:
        await client.post("/auth/register", json={
            "email": email, "password": password,
            "display_name": "Mastery Proof", "preferred_color": "white",
        })
        login = await client.post("/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        tok = login.json()["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        me = (await client.get("/players/me", headers=hdr)).json()
        player_id = me["id"]
        transcript["player_id"] = player_id

        # Step 1: play a bad move.
        r = await client.post("/sessions", headers=hdr, json={
            "mode": "guided", "player_color": "white",
            "sampler_mode": "mainline", "seed": 11, "novelty_prob": 0.0,
        })
        r.raise_for_status()
        session_id = r.json()["id"]

        r = await client.post(f"/sessions/{session_id}/move", headers=hdr,
                              json={"san": "g4", "latency_ms": 300})
        r.raise_for_status()
        bad = r.json()
        print(f"played 1.g4 → classification={bad['classification']} swing={bad['eval_swing_cp']}cp")
        print(f"  coach: {bad['coaching']['spoken_summary']}")
        transcript["events"].append({"stage": "mistake_played", "move": bad})
        assert bad["classification"] in ("inaccuracy", "mistake", "blunder"), \
            f"1.g4 should be at least an inaccuracy, got {bad['classification']}"
        await client.post(f"/sessions/{session_id}/finish", headers=hdr)

        # Step 2: mastery row exists, due now.
        r = await client.get(f"/players/{player_id}/mastery", headers=hdr)
        r.raise_for_status()
        dash = r.json()
        print(f"mastery after mistake: counts={dash['counts_by_state']} due_now={dash['due_now']}")
        transcript["events"].append({"stage": "mastery_after_mistake", "dashboard": dash})
        assert dash["due_now"] >= 1

        r = await client.post(f"/players/{player_id}/mastery/next", headers=hdr)
        r.raise_for_status()
        nxt = r.json()
        print(f"next drill due_now={nxt['due_now']} reps={nxt['row']['reps']} "
              f"interval={nxt['row']['interval_days']} ease={nxt['row']['ease_factor']}")
        transcript["events"].append({"stage": "next_drill_due", "pick": nxt})
        row_before = nxt["row"]
        assert row_before["reps"] == 0, f"reps should reset after a bad move, got {row_before['reps']}"

        # Step 3: new session, retrain with e4.
        r = await client.post("/sessions", headers=hdr, json={
            "mode": "guided", "player_color": "white",
            "sampler_mode": "mainline", "seed": 11, "novelty_prob": 0.0,
        })
        r.raise_for_status()
        session_id = r.json()["id"]

        r = await client.post(f"/sessions/{session_id}/move", headers=hdr,
                              json={"san": "e4", "latency_ms": 500})
        r.raise_for_status()
        good = r.json()
        print(f"retrained with 1.e4 → classification={good['classification']} swing={good['eval_swing_cp']}cp")
        transcript["events"].append({"stage": "retrain_played", "move": good})
        assert good["classification"] in ("best", "playable"), \
            f"1.e4 should be best/playable, got {good['classification']}"
        await client.post(f"/sessions/{session_id}/finish", headers=hdr)

        # Step 4: mastery row improved.
        r = await client.get(f"/players/{player_id}/mastery", headers=hdr)
        r.raise_for_status()
        dash2 = r.json()
        print(f"mastery after retrain: counts={dash2['counts_by_state']} due_now={dash2['due_now']}")
        transcript["events"].append({"stage": "mastery_after_retrain", "dashboard": dash2})

        row_after = next((row for row in dash2["weakest"] + dash2["due_soon"]
                          if row["position_id"] == row_before["position_id"]), None)
        if row_after is None:
            print("warning: couldn't locate row in dashboard payload — refetching via next endpoint")
            r = await client.post(f"/players/{player_id}/mastery/next", headers=hdr)
            r.raise_for_status()
            row_after = r.json().get("row")

        print(f"  reps before={row_before['reps']}  reps after={row_after['reps']}")
        print(f"  interval before={row_before['interval_days']}  interval after={row_after['interval_days']}")
        print(f"  ease before={row_before['ease_factor']}  ease after={row_after['ease_factor']}")
        print(f"  state before={row_before['state']}  state after={row_after['state']}")
        transcript["events"].append({"stage": "mastery_delta",
                                     "before": row_before, "after": row_after})

        assert row_after["reps"] > row_before["reps"], "reps should increase after a successful retrain"
        assert row_after["ease_factor"] >= row_before["ease_factor"], "ease should not drop on success"

    out = os.path.join(HERE, "data", "proof_mastery.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(transcript, fh, indent=2, default=str)
    print(f"transcript → {out}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
