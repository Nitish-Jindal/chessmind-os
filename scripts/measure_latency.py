"""Measure client-observed p50/p95 latency for the two engine-backed hot paths.

    POST /sessions/{id}/move      (two depth-limited Stockfish lookups + sampler)
    POST /analysis/position       (MultiPV analysis at the server default depth)

Runs against a live backend over HTTP (real Postgres, real Stockfish); nothing
is stubbed. Each endpoint is measured twice:

  * cold — the position has no engine_analysis row at the requested depth, so
    Stockfish actually searches;
  * warm — the identical requests replayed, served from the engine_analysis
    cache (the primary path per ARCHITECTURE.md §6.2).

Determinism: sessions use fixed seeds and the player's moves are drawn from
the legal list with ``random.Random(seed)``, so a replay retraces the same
positions.

Usage:
    python scripts/measure_latency.py --base-url http://localhost:8000
    python scripts/measure_latency.py --sessions 20 --plies 6 --out scripts/data/latency.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import random
import statistics
import time
import uuid

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))


def pct(samples: list[float], p: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(samples)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(samples: list[float]) -> dict[str, float | int]:
    return {
        "n": len(samples),
        "p50_ms": round(pct(samples, 50), 1),
        "p95_ms": round(pct(samples, 95), 1),
        "mean_ms": round(statistics.fmean(samples), 1),
        "max_ms": round(max(samples), 1),
    }


async def timed(client: httpx.AsyncClient, method: str, url: str, **kw) -> tuple[float, httpx.Response]:
    t0 = time.perf_counter()
    r = await client.request(method, url, **kw)
    elapsed = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    return elapsed, r


async def play_sessions(
    client: httpx.AsyncClient, *, seeds: list[int], plies: int
) -> tuple[list[float], list[str], dict[str, int]]:
    """Play one guided session per seed; return move latencies and visited FENs."""
    samples: list[float] = []
    fens: list[str] = []
    engine_meta: dict[str, int] = {}
    for seed in seeds:
        r = await client.post("/sessions", json={
            "mode": "guided", "player_color": "white", "sampler_mode": "mix", "seed": seed,
        })
        r.raise_for_status()
        sid = r.json()["id"]
        rng = random.Random(seed)
        for _ in range(plies):
            legal = (await client.get(f"/sessions/{sid}/legal")).json()
            if legal["status"] != "active" or legal["next_to_move"] != "player":
                break
            uci = rng.choice(sorted(legal["legal_moves_uci"]))
            ms, resp = await timed(client, "POST", f"/sessions/{sid}/move", json={"uci": uci})
            body = resp.json()
            samples.append(ms)
            fens.append(body["current_fen"])
            engine_meta = {"depth": body["engine_depth"], "multipv": body["engine_multipv"]}
            if body["status"] != "active":
                break
        await client.post(f"/sessions/{sid}/finish")
    return samples, fens, {**engine_meta, "engine_version": body["engine_version"]}


async def analyze_all(
    client: httpx.AsyncClient, fens: list[str], depth: int | None
) -> tuple[list[float], list[int], dict[str, object], int]:
    samples: list[float] = []
    engine_ms: list[int] = []
    meta: dict[str, object] = {}
    cached = 0
    for fen in fens:
        payload: dict[str, object] = {"fen": fen}
        if depth:
            payload["depth"] = depth
        ms, resp = await timed(client, "POST", "/analysis/position", json=payload)
        body = resp.json()
        samples.append(ms)
        engine_ms.append(body["time_ms"])
        cached += int(body["cached"])
        meta = {"depth": body["depth"], "multipv": body["multipv"], "engine_version": body["engine_version"]}
    return samples, engine_ms, meta, cached


async def main(args: argparse.Namespace) -> int:
    email = f"latency-{uuid.uuid4().hex[:8]}@chessmind.example.com"
    password = "latencypassword123"
    async with httpx.AsyncClient(base_url=args.base_url, timeout=300.0) as client:
        r = await client.post("/auth/register", json={
            "email": email, "password": password, "display_name": "Latency", "preferred_color": "white",
        })
        r.raise_for_status()
        r = await client.post("/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        client.headers["authorization"] = f"Bearer {r.json()['access_token']}"

        # Warm the engine subprocess so process spawn is not billed to sample #1.
        await client.post("/analysis/position", json={
            "fen": "8/8/8/8/8/2k5/8/K7 w - - 0 1", "depth": 1, "multipv": 1,
        })

        # Seeds offset by a run nonce so a re-run against the same DB is cold again
        # for the random-walk positions (the seeded opening prefix may be cached).
        base = args.seed_base if args.seed_base is not None else random.SystemRandom().randrange(10**6)
        seeds = [base + i for i in range(args.sessions)]

        move_cold, fens, move_meta = await play_sessions(client, seeds=seeds, plies=args.plies)
        move_warm, _, _ = await play_sessions(client, seeds=seeds, plies=args.plies)

        unique_fens = list(dict.fromkeys(fens))[: args.analysis_positions]
        an_cold, an_engine_cold, an_meta, cold_hits = await analyze_all(client, unique_fens, args.depth)
        an_warm, _, _, warm_hits = await analyze_all(client, unique_fens, args.depth)

    report = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "host": {
            "platform": platform.platform(),
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
        "seed_base": base,
        "sessions": args.sessions,
        "plies_per_session": args.plies,
        "move": {
            "engine": move_meta,
            "cold": summarize(move_cold),
            "warm": summarize(move_warm),
        },
        "analysis": {
            "engine": an_meta,
            "positions": len(unique_fens),
            "cold": {**summarize(an_cold), "cache_hits": cold_hits,
                     "engine_time_p50_ms": pct([float(x) for x in an_engine_cold], 50),
                     "engine_time_p95_ms": pct([float(x) for x in an_engine_cold], 95)},
            "warm": {**summarize(an_warm), "cache_hits": warm_hits},
        },
    }
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--sessions", type=int, default=20)
    p.add_argument("--plies", type=int, default=6, help="Player moves per session.")
    p.add_argument("--analysis-positions", type=int, default=60)
    p.add_argument("--depth", type=int, default=None, help="Analysis depth (default: server default).")
    p.add_argument("--seed-base", type=int, default=None)
    p.add_argument("--out", default=os.path.join(HERE, "data", "latency.json"))
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
