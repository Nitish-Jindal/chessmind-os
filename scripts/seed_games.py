"""Seed script: compose a repertoire of real opening lines and import them.

Builds ~100+ games covering Italian, Ruy Lopez, Sicilian, and Queen's Gambit
family lines (plus a few extras to clear the 100-game bar), writes a PGN file
for inspection, and feeds every game through the production import pipeline.

Usage:
    python scripts/seed_games.py                 # seeds the default player
    python scripts/seed_games.py --email you@... # seeds the player with that email
    python scripts/seed_games.py --pgn-out seed_games.pgn --dry-run  # write-only

Each entry is a real opening line (not generated nonsense); the result field
is varied so GET /openings/tree surfaces meaningful aggregates.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
from dataclasses import dataclass
from datetime import date

import chess
import chess.pgn

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, os.path.abspath(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from chessmind.db import AsyncSessionLocal  # noqa: E402
from chessmind.models import Player  # noqa: E402
from chessmind.security import hash_password  # noqa: E402
from chessmind.services.pgn_import import import_pgn_sync  # noqa: E402


@dataclass(frozen=True)
class SeedLine:
    opening: str
    eco: str
    sans: tuple[str, ...]
    result: str
    white: str = "Seed-W"
    black: str = "Seed-B"


# --------------------------------------------------------------------------- #
# Repertoire — 100+ real opening lines.
# --------------------------------------------------------------------------- #


def _italian_lines() -> list[SeedLine]:
    base = ("e4", "e5", "Nf3", "Nc6", "Bc4")
    lines: list[SeedLine] = []

    # Giuoco Piano / Pianissimo
    lines += [
        SeedLine("Italian Giuoco Pianissimo", "C50",
                 base + ("Bc5", "d3", "Nf6", "O-O", "d6", "c3", "O-O"), "1/2-1/2"),
        SeedLine("Italian Giuoco Piano", "C50",
                 base + ("Bc5", "c3", "Nf6", "d4", "exd4", "cxd4", "Bb4+"), "1-0"),
        SeedLine("Italian Giuoco Piano", "C50",
                 base + ("Bc5", "c3", "Nf6", "d3", "a6", "Bb3", "d6"), "0-1"),
        SeedLine("Italian Classical", "C53",
                 base + ("Bc5", "c3", "Qe7", "d4", "Bb6"), "1-0"),
    ]
    # Evans Gambit
    lines += [
        SeedLine("Evans Gambit Accepted", "C52",
                 base + ("Bc5", "b4", "Bxb4", "c3", "Ba5", "d4", "exd4", "O-O"), "1-0"),
        SeedLine("Evans Gambit Declined", "C51",
                 base + ("Bc5", "b4", "Bb6", "a4", "a6"), "0-1"),
    ]
    # Two Knights / Fried Liver family
    lines += [
        SeedLine("Two Knights Defense", "C55",
                 base + ("Nf6", "d3", "Bc5", "c3", "d6"), "1/2-1/2"),
        SeedLine("Two Knights Ng5 d5", "C57",
                 base + ("Nf6", "Ng5", "d5", "exd5", "Na5", "Bb5+", "c6"), "0-1"),
        SeedLine("Fried Liver Attack", "C57",
                 base + ("Nf6", "Ng5", "d5", "exd5", "Nxd5", "Nxf7", "Kxf7", "Qf3+"), "1-0"),
        SeedLine("Hungarian Defense", "C50",
                 base + ("Be7", "d4", "d6", "dxe5", "dxe5", "Qxd8+", "Bxd8"), "1/2-1/2"),
    ]
    # Repeat with varied results so edge aggregates have mass
    extras = []
    for _ in range(2):
        for ln in lines:
            extras.append(SeedLine(ln.opening, ln.eco, ln.sans, _rotate_result(ln.result)))
    return lines + extras


def _ruy_lopez_lines() -> list[SeedLine]:
    base = ("e4", "e5", "Nf3", "Nc6", "Bb5")
    lines: list[SeedLine] = []

    # Berlin
    lines += [
        SeedLine("Ruy Lopez Berlin", "C65",
                 base + ("Nf6", "O-O", "Nxe4", "d4", "Nd6", "Bxc6", "dxc6", "dxe5", "Nf5"), "1/2-1/2"),
        SeedLine("Ruy Lopez Berlin Wall", "C67",
                 base + ("Nf6", "O-O", "Nxe4", "d4", "Nd6", "Bxc6", "dxc6", "dxe5", "Nf5", "Qxd8+", "Kxd8"), "1/2-1/2"),
    ]
    # Morphy / Closed
    lines += [
        SeedLine("Ruy Lopez Morphy Closed", "C84",
                 base + ("a6", "Ba4", "Nf6", "O-O", "Be7", "Re1", "b5", "Bb3", "d6", "c3", "O-O"), "1/2-1/2"),
        SeedLine("Ruy Lopez Open", "C80",
                 base + ("a6", "Ba4", "Nf6", "O-O", "Nxe4", "d4", "b5", "Bb3", "d5"), "1-0"),
        SeedLine("Ruy Lopez Exchange", "C68",
                 base + ("a6", "Bxc6", "dxc6", "O-O", "f6", "d4", "exd4", "Nxd4"), "0-1"),
        SeedLine("Ruy Lopez Marshall", "C89",
                 base + ("a6", "Ba4", "Nf6", "O-O", "Be7", "Re1", "b5", "Bb3", "O-O", "c3", "d5"), "1-0"),
        SeedLine("Ruy Lopez Steinitz Deferred", "C79",
                 base + ("a6", "Ba4", "Nf6", "O-O", "d6"), "0-1"),
    ]
    # Schliemann & sidelines
    lines += [
        SeedLine("Ruy Lopez Schliemann", "C63",
                 base + ("f5", "Nc3", "fxe4", "Nxe4", "Nf6"), "0-1"),
        SeedLine("Ruy Lopez Classical", "C64",
                 base + ("Bc5", "c3", "f5"), "1-0"),
        SeedLine("Ruy Lopez Bird", "C61",
                 base + ("Nd4", "Nxd4", "exd4", "O-O"), "1/2-1/2"),
        SeedLine("Ruy Lopez Cozio", "C60",
                 base + ("Nge7", "c3", "a6", "Ba4", "b5"), "0-1"),
    ]
    extras = []
    for _ in range(2):
        for ln in lines:
            extras.append(SeedLine(ln.opening, ln.eco, ln.sans, _rotate_result(ln.result)))
    return lines + extras


def _sicilian_lines() -> list[SeedLine]:
    open_sic = ("e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3")
    lines: list[SeedLine] = []

    lines += [
        SeedLine("Sicilian Najdorf", "B90",
                 open_sic + ("a6",), "1-0"),
        SeedLine("Sicilian Najdorf English Attack", "B90",
                 open_sic + ("a6", "Be3", "e5", "Nb3", "Be6", "f3"), "0-1"),
        SeedLine("Sicilian Najdorf Bg5", "B96",
                 open_sic + ("a6", "Bg5", "e6", "f4", "Be7"), "1-0"),
        SeedLine("Sicilian Dragon", "B70",
                 open_sic + ("g6",), "1/2-1/2"),
        SeedLine("Sicilian Yugoslav Attack", "B78",
                 open_sic + ("g6", "Be3", "Bg7", "f3", "O-O", "Qd2", "Nc6"), "1-0"),
        SeedLine("Sicilian Scheveningen", "B80",
                 open_sic + ("e6",), "1/2-1/2"),
        SeedLine("Sicilian Classical Richter-Rauzer", "B60",
                 open_sic + ("Nc6", "Bg5", "e6", "Qd2", "a6"), "0-1"),
        SeedLine("Sicilian Sveshnikov", "B33",
                 ("e4", "c5", "Nf3", "Nc6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "e5",
                  "Ndb5", "d6", "Bg5", "a6", "Na3", "b5"), "0-1"),
        SeedLine("Sicilian Taimanov", "B40",
                 ("e4", "c5", "Nf3", "e6", "d4", "cxd4", "Nxd4", "Nc6", "Nc3", "Qc7"), "1-0"),
        SeedLine("Sicilian Kan", "B41",
                 ("e4", "c5", "Nf3", "e6", "d4", "cxd4", "Nxd4", "a6"), "1/2-1/2"),
        SeedLine("Sicilian Rossolimo", "B30",
                 ("e4", "c5", "Nf3", "Nc6", "Bb5", "g6", "O-O", "Bg7", "Re1", "e5"), "1-0"),
        SeedLine("Sicilian Alapin", "B22",
                 ("e4", "c5", "c3", "Nf6", "e5", "Nd5", "d4", "cxd4", "Nf3"), "0-1"),
        SeedLine("Sicilian Closed", "B23",
                 ("e4", "c5", "Nc3", "Nc6", "g3", "g6", "Bg2", "Bg7", "d3", "d6"), "1/2-1/2"),
        SeedLine("Sicilian Grand Prix Attack", "B23",
                 ("e4", "c5", "Nc3", "Nc6", "f4", "g6", "Nf3", "Bg7"), "1-0"),
        SeedLine("Sicilian Smith-Morra Gambit", "B21",
                 ("e4", "c5", "d4", "cxd4", "c3", "dxc3", "Nxc3", "Nc6"), "1-0"),
    ]
    extras = []
    for _ in range(2):
        for ln in lines:
            extras.append(SeedLine(ln.opening, ln.eco, ln.sans, _rotate_result(ln.result)))
    return lines + extras


def _queens_gambit_lines() -> list[SeedLine]:
    lines: list[SeedLine] = []

    lines += [
        SeedLine("QGD Orthodox", "D63",
                 ("d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7", "e3", "O-O"), "1/2-1/2"),
        SeedLine("QGD Exchange", "D35",
                 ("d4", "d5", "c4", "e6", "Nc3", "Nf6", "cxd5", "exd5", "Bg5"), "1-0"),
        SeedLine("QGD Lasker", "D56",
                 ("d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7", "e3", "O-O", "Nf3", "h6", "Bh4", "Ne4"), "0-1"),
        SeedLine("QGD Tartakower", "D58",
                 ("d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7", "e3", "O-O", "Nf3", "h6", "Bh4", "b6"), "1-0"),
        SeedLine("QGA Classical", "D27",
                 ("d4", "d5", "c4", "dxc4", "Nf3", "Nf6", "e3", "e6", "Bxc4", "c5"), "1/2-1/2"),
        SeedLine("Slav Defense", "D11",
                 ("d4", "d5", "c4", "c6", "Nf3", "Nf6", "Nc3", "dxc4", "a4"), "0-1"),
        SeedLine("Semi-Slav Meran", "D47",
                 ("d4", "d5", "c4", "c6", "Nf3", "Nf6", "Nc3", "e6", "e3", "Nbd7", "Bd3", "dxc4", "Bxc4"), "1-0"),
        SeedLine("Semi-Slav Moscow", "D43",
                 ("d4", "d5", "c4", "c6", "Nf3", "Nf6", "Nc3", "e6", "Bg5", "h6", "Bxf6", "Qxf6"), "1/2-1/2"),
        SeedLine("Chebanenko Slav", "D15",
                 ("d4", "d5", "c4", "c6", "Nf3", "Nf6", "Nc3", "a6"), "0-1"),
        SeedLine("Tarrasch Defense", "D32",
                 ("d4", "d5", "c4", "e6", "Nc3", "c5", "cxd5", "exd5", "Nf3"), "1-0"),
        SeedLine("Albin Counter-Gambit", "D08",
                 ("d4", "d5", "c4", "e5", "dxe5", "d4", "Nf3", "Nc6"), "0-1"),
        SeedLine("QGD Cambridge Springs", "D52",
                 ("d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Nbd7", "Nf3", "c6", "e3", "Qa5"), "1-0"),
        SeedLine("Catalan Open", "E04",
                 ("d4", "Nf6", "c4", "e6", "g3", "d5", "Bg2", "dxc4"), "1/2-1/2"),
        SeedLine("Catalan Closed", "E07",
                 ("d4", "Nf6", "c4", "e6", "g3", "d5", "Bg2", "Be7", "Nf3", "O-O"), "1-0"),
        SeedLine("Nimzo-Indian Rubinstein", "E41",
                 ("d4", "Nf6", "c4", "e6", "Nc3", "Bb4", "e3", "c5", "Nf3"), "0-1"),
    ]
    extras = []
    for _ in range(2):
        for ln in lines:
            extras.append(SeedLine(ln.opening, ln.eco, ln.sans, _rotate_result(ln.result)))
    return lines + extras


_RESULT_CYCLE = ["1-0", "0-1", "1/2-1/2"]


def _rotate_result(prev: str) -> str:
    i = _RESULT_CYCLE.index(prev) if prev in _RESULT_CYCLE else 0
    return _RESULT_CYCLE[(i + 1) % len(_RESULT_CYCLE)]


def all_lines() -> list[SeedLine]:
    return (
        _italian_lines()
        + _ruy_lopez_lines()
        + _sicilian_lines()
        + _queens_gambit_lines()
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_pgn(line: SeedLine, idx: int) -> str:
    game = chess.pgn.Game()
    game.headers["Event"] = f"ChessMind Seed #{idx:03d}"
    game.headers["Site"] = "chessmind-seed"
    game.headers["Date"] = date.today().strftime("%Y.%m.%d")
    game.headers["Round"] = str(idx)
    game.headers["White"] = line.white
    game.headers["Black"] = line.black
    game.headers["Result"] = line.result
    game.headers["ECO"] = line.eco
    game.headers["Opening"] = line.opening

    board = chess.Board()
    node = game
    for san in line.sans:
        move = board.parse_san(san)
        node = node.add_variation(move)
        board.push(move)

    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter)


def render_all(lines: list[SeedLine]) -> str:
    chunks = [render_pgn(ln, i + 1) for i, ln in enumerate(lines)]
    return "\n\n".join(chunks) + "\n"


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


async def _ensure_player(session: AsyncSession, email: str) -> Player:
    row = (await session.execute(select(Player).where(Player.email == email))).scalar_one_or_none()
    if row is not None:
        return row
    player = Player(
        email=email,
        password_hash=hash_password("seedseedseed"),
        display_name="Seed Player",
        preferred_color="either",
        settings_json={},
    )
    session.add(player)
    await session.commit()
    await session.refresh(player)
    return player


async def main(args: argparse.Namespace) -> int:
    lines = all_lines()
    if args.limit:
        lines = lines[: args.limit]
    pgn_text = render_all(lines)

    if args.pgn_out:
        out = os.path.abspath(args.pgn_out)
        os.makedirs(os.path.dirname(out), exist_ok=True) if os.path.dirname(out) else None
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(pgn_text)
        print(f"wrote {len(lines)} games to {out}")

    if args.dry_run:
        return 0

    async with AsyncSessionLocal() as session:
        player = await _ensure_player(session, args.email)
        stats = await import_pgn_sync(session, player_id=player.id, pgn_text=pgn_text)
        await session.commit()

    print(f"imported {stats.games} games for player={args.email}")
    print(f"positions touched: {len(stats.positions_touched)}")
    print(f"edges created or bumped: {stats.edges_created_or_bumped}")
    print(f"repertoire lines: {stats.repertoire_lines}")
    if stats.errors:
        print(f"errors ({len(stats.errors)}):")
        for e in stats.errors[:10]:
            print(f"  - {e}")
    return 0 if not stats.errors else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--email", default="seed@chessmind.local", help="Seed player's email.")
    p.add_argument("--pgn-out", default=None, help="If set, also write the composed PGN to this file.")
    p.add_argument("--dry-run", action="store_true", help="Compose but don't import.")
    p.add_argument("--limit", type=int, default=0, help="Cap number of games (default: all).")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
