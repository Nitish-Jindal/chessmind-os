"""Day-1 proof driver — hits the live FastAPI through HTTP end-to-end.

Steps performed:
  1. POST /auth/register
  2. POST /auth/login
  3. POST /imports/pgn with the composed 153-game PGN blob
  4. Poll GET /imports/{id} until status in {succeeded, failed, partial}
  5. GET /openings/tree?limit=10 and print the response

Writes a copy of the HTTP response to scripts/data/day1_proof_tree.json
so the artifact survives the ephemeral container.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from uuid import uuid4

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "backend")))
sys.path.insert(0, HERE)  # so `import seed_games` works

from seed_games import all_lines, render_all  # noqa: E402


async def main(args: argparse.Namespace) -> int:
    lines = all_lines()
    pgn = render_all(lines)
    email = args.email or f"day1-{uuid4().hex[:8]}@chessmind.example.com"
    password = "day1proofpassword"

    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as client:
        r = await client.post("/auth/register", json={
            "email": email,
            "password": password,
            "display_name": "Day-1 Proof",
            "preferred_color": "either",
        })
        if r.status_code == 409:
            print(f"player {email} already exists, continuing")
        else:
            r.raise_for_status()
            print(f"registered {email}")

        r = await client.post("/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        tokens = r.json()
        hdr = {"Authorization": f"Bearer {tokens['access_token']}"}
        print("logged in")

        r = await client.post(
            "/imports/pgn",
            headers=hdr,
            json={"pgn": pgn, "source": "day1_proof"},
        )
        r.raise_for_status()
        job = r.json()
        job_id = job["id"]
        print(f"import job queued: {job_id} status={job['status']}")

        started = time.monotonic()
        deadline = started + args.max_wait
        last_status = None
        while time.monotonic() < deadline:
            r = await client.get(f"/imports/{job_id}", headers=hdr)
            r.raise_for_status()
            job = r.json()
            if job["status"] != last_status:
                print(f"  status={job['status']}  progress={job['progress_games']}/{job['total_games']}")
                last_status = job["status"]
            if job["status"] in ("succeeded", "failed", "partial"):
                break
            await asyncio.sleep(1.0)
        else:
            print(f"timed out after {args.max_wait}s", file=sys.stderr)
            return 2

        elapsed = time.monotonic() - started
        print(f"import finished in {elapsed:.1f}s: {json.dumps(job['artifacts_json'], indent=2)}")

        if job["status"] == "failed":
            print(f"import failed: {job.get('error')}", file=sys.stderr)
            return 1

        r = await client.get("/openings/tree", params={"limit": 10}, headers=hdr)
        r.raise_for_status()
        tree = r.json()

    out_dir = os.path.join(HERE, "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "day1_proof_tree.json")
    with open(out_path, "w") as fh:
        json.dump({
            "proof": "Day-1: import 100+ games, GET /openings/tree",
            "email": email,
            "games_submitted": len(lines),
            "import_job": job,
            "openings_tree": tree,
        }, fh, indent=2)
    print(f"wrote proof artifact to {out_path}")

    print()
    print("=== GET /openings/tree?limit=10 ===")
    print(f"root canonical: {tree['root']['canonical_key']}")
    print(f"total positions in graph: {tree['total_positions']}")
    print(f"total edges in graph: {tree['total_edges']}")
    print()
    print(f"{'move':<6} {'games':>6} {'W':>4} {'L':>4} {'D':>4}")
    for e in tree["root"]["edges"]:
        print(f"{e['san']:<6} {e['games_count']:>6} {e['white_wins']:>4} {e['black_wins']:>4} {e['draws']:>4}")

    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--email", default=None)
    p.add_argument("--max-wait", type=int, default=120)
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
