"""Opponent prep report: their most frequent lines, likely branches, and suggested responses.

Everything is computed from facts already in the database or produced by the engine:

- Lines and branches come from the opponent's own imported games (``Game`` rows with
  ``source = "opponent:<profile id>"``), replayed with python-chess. Only the first
  ``max_plies`` plies of each game count.
- Opening names come from the opening taxonomy (``services/opening_taxonomy.py``).
- Suggested responses are Stockfish's best moves in the position where it is the player's
  turn at the end of each frequent line, from ``analyze_board`` (persisted to
  ``engine_analysis``). Alongside each, the report gives how the opponent actually scored
  after that move in their games, when they faced it.

No text is generated: the report is structured data the UI lays out.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

import chess
import chess.pgn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.models import Game, OpponentProfile
from chessmind.services import opening_taxonomy
from chessmind.services.engine import analyze_board

Color = Literal["white", "black"]


@dataclass
class _Node:
    games: int = 0
    wins: int = 0  # for the opponent
    draws: int = 0
    losses: int = 0
    children: dict[str, "_Node"] = field(default_factory=dict)

    def add(self, result: str, opp_color: Color) -> None:
        self.games += 1
        if result == "1/2-1/2":
            self.draws += 1
        elif result in ("1-0", "0-1"):
            if (result == "1-0") == (opp_color == "white"):
                self.wins += 1
            else:
                self.losses += 1

    def score(self) -> dict[str, Any]:
        pct = (self.wins + 0.5 * self.draws) / self.games if self.games else None
        return {"games": self.games, "wins": self.wins, "draws": self.draws, "losses": self.losses,
                "score_pct": round(100 * pct, 1) if pct is not None else None}


def _sorted_children(node: _Node) -> list[tuple[str, _Node]]:
    return sorted(node.children.items(), key=lambda kv: (-kv[1].games, kv[0]))


def _board_after(sans: list[str]) -> chess.Board:
    b = chess.Board()
    for s in sans:
        b.push_san(s)
    return b


def _deepest_opening(sans: list[str]) -> dict[str, Any] | None:
    b, found = chess.Board(), None
    for s in sans:
        b.push_san(s)
        e = opening_taxonomy.lookup(b)
        if e is not None:
            found = e.to_json()
    return found


async def _load_tree(session: AsyncSession, profile_id: UUID, max_plies: int) -> dict[Color, _Node]:
    rows = (
        await session.execute(select(Game.pgn, Game.result, Game.provenance_json)
                              .where(Game.source == f"opponent:{profile_id}"))
    ).all()
    roots: dict[Color, _Node] = {"white": _Node(), "black": _Node()}
    for pgn, result, prov in rows:
        color: Color | None = (prov or {}).get("opponent_color")
        if color not in roots:
            continue
        game = chess.pgn.read_game(io.StringIO(pgn))
        if game is None:
            continue
        node = roots[color]
        node.add(result, color)
        board = game.board()
        for ply, move in enumerate(game.mainline_moves()):
            if ply >= max_plies:
                break
            san = board.san(move)
            board.push(move)
            node = node.children.setdefault(san, _Node())
            node.add(result, color)
    return roots


def _frequent_lines(root: _Node, min_games: int, max_lines: int, max_plies: int) -> list[tuple[list[str], _Node]]:
    """Maximal paths (up to ``max_plies``) whose every move was played in at least ``min_games`` games."""
    out: list[tuple[list[str], _Node]] = []

    def walk(path: list[str], node: _Node) -> None:
        kids = [] if len(path) >= max_plies else [(s, n) for s, n in _sorted_children(node) if n.games >= min_games]
        if not kids:
            if path:
                out.append((path, node))
            return
        for s, n in kids:
            walk([*path, s], n)

    walk([], root)
    out.sort(key=lambda pn: (-pn[1].games, -len(pn[0]), pn[0]))
    return out[:max_lines]


def _branches(root: _Node, min_games: int, opp_color: Color, max_items: int, max_plies: int) -> list[dict[str, Any]]:
    """Positions inside the frequent tree where two or more continuations each recur."""
    found: list[tuple[list[str], _Node]] = []

    def walk(path: list[str], node: _Node) -> None:
        kids = [] if len(path) >= max_plies else [(s, n) for s, n in _sorted_children(node) if n.games >= min_games]
        if len(kids) >= 2:
            found.append((path, node))
        for s, n in kids:
            walk([*path, s], n)

    walk([], root)
    found.sort(key=lambda pn: (-pn[1].games, len(pn[0]), pn[0]))
    items = []
    for path, node in found[:max_items]:
        board = _board_after(path)
        mover: Color = "white" if board.turn == chess.WHITE else "black"
        items.append({
            "after_san": path,
            "fen": board.fen(),
            "to_move": "opponent" if mover == opp_color else "you",
            "games": node.games,
            "opening": _deepest_opening(path),
            "options": [
                {"san": s, "games": n.games, "share": round(n.games / node.games, 3), **n.score()}
                for s, n in _sorted_children(node) if n.games >= min_games
            ],
        })
    return items


async def _suggestion(
    session: AsyncSession, path: list[str], node: _Node, opp_color: Color, depth: int,
) -> dict[str, Any] | None:
    """Stockfish's answer where it is the player's move at (or one opponent move after) the line's end."""
    path = list(path)
    board = _board_after(path)
    mover: Color = "white" if board.turn == chess.WHITE else "black"
    if mover == opp_color:
        kids = _sorted_children(node)
        if not kids:
            return None
        san, node = kids[0]  # their most frequent next move, however rare
        board.push_san(san)
        path.append(san)
    if board.is_game_over():
        return None
    analysis = await analyze_board(session, board=board, depth=depth, multipv=3)
    you: Color = "black" if opp_color == "white" else "white"
    sign = 1 if you == "white" else -1
    candidates = []
    for line in analysis.results:
        if not line.get("pv_san"):
            continue
        first = line["pv_san"][0]
        board.parse_san(first)  # python-chess check: every suggested move is legal here
        faced = node.children.get(first)
        candidates.append({
            "san": first,
            "line_rank": line["line_rank"],
            "cp_white": line.get("cp"),
            "mate_white": line.get("mate"),
            "cp_for_you": sign * line["cp"] if line.get("cp") is not None else None,
            "pv_san": line["pv_san"][:6],
            "opponent_faced_it": faced.score() if faced else None,
        })
    return {
        "after_san": path,
        "fen": board.fen(),
        "engine": {"version": analysis.engine_version, "depth": analysis.depth,
                   "multipv": analysis.multipv, "perspective": "white", "cached": analysis.cached},
        "best": candidates[0] if candidates else None,
        "alternatives": candidates[1:],
        # What their opponents actually played here and how the opponent scored against it.
        "their_record_here": [
            {"san": s, **n.score()} for s, n in _sorted_children(node)[:5]
        ],
    }


async def build_prep_report(
    session: AsyncSession,
    *,
    profile: OpponentProfile,
    max_plies: int = 12,
    max_lines: int = 5,
    engine_depth: int = 14,
) -> dict[str, Any]:
    # Two plies past the report depth, so a line that ends with the opponent to move still has
    # their usual next move and the reply to it for the suggestion.
    roots = await _load_tree(session, profile.id, max_plies + 2)
    sides: dict[str, Any] = {}
    for opp_color, root in roots.items():
        you: Color = "black" if opp_color == "white" else "white"
        min_games = max(2, math.ceil(0.05 * root.games))
        lines_out = []
        for path, node in _frequent_lines(root, min_games, max_lines, max_plies):
            lines_out.append({
                "san": path,
                "games": node.games,
                "share": round(node.games / root.games, 3) if root.games else 0.0,
                "opponent_result": node.score(),
                "opening": _deepest_opening(path),
                "suggested_response": await _suggestion(session, path, node, opp_color, engine_depth),
            })
        first_moves = [{"san": s, "share": round(n.games / root.games, 3), **n.score()}
                       for s, n in _sorted_children(root)[:6]] if root.games else []
        sides[f"opponent_as_{opp_color}"] = {
            "opponent_color": opp_color,
            "you_play": you,
            "games": root.games,
            "opponent_result": root.score(),
            "min_games_for_line": min_games,
            "first_moves": first_moves,
            "frequent_lines": lines_out,
            "likely_branches": _branches(root, min_games, opp_color, 6, max_plies),
        }
    return {
        "opponent_profile_id": str(profile.id),
        "display_name": profile.display_name,
        "games_imported": profile.games_imported,
        "max_plies": max_plies,
        "sources": {
            "lines": "opponent's imported games (python-chess replay)",
            "openings": "lichess-org/chess-openings taxonomy (CC0)",
            "suggestions": "Stockfish via engine_analysis (white-centric cp)",
        },
        **sides,
    }
