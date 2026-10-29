"""Record real Lichess Masters explorer responses as test fixtures.

The build sandbox cannot reach explorer.lichess.ovh, so the fixtures in
backend/tests/fixtures/ are hand-written in the documented response shape.
Run this from any host that can reach Lichess (e.g. a Render shell) to
replace them with recorded responses:

    python scripts/record_explorer_fixture.py
    python scripts/record_explorer_fixture.py --token "$LICHESS_API_TOKEN"

The parser test asserts structure (validated moves, totals = sum of W/D/B,
castling normalized), not specific counts, so recorded data drops in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import chess
import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "backend", "tests", "fixtures")

POSITIONS = {
    "lichess_masters_startpos.json": [],
    "lichess_masters_italian_castle.json": ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"],
}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="https://explorer.lichess.ovh/masters")
    p.add_argument("--token", default=os.environ.get("LICHESS_API_TOKEN", ""))
    args = p.parse_args()
    headers = {"User-Agent": "chessmind-os fixture recorder"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    for name, sans in POSITIONS.items():
        board = chess.Board()
        for san in sans:
            board.push_san(san)
        r = httpx.get(args.url, params={"fen": board.fen(), "moves": 12, "topGames": 0},
                      headers=headers, timeout=15)
        r.raise_for_status()
        body = r.json()
        body["_fixture_note"] = f"RECORDED from {args.url} for FEN {board.fen()}"
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=2)
            fh.write("\n")
        print(f"recorded {name}: {sum(body.get(k, 0) for k in ('white', 'draws', 'black'))} games")
    return 0


if __name__ == "__main__":
    sys.exit(main())
