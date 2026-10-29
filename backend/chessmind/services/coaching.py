"""Grounded coaching prose — assembled from Stockfish PV + python-chess board features.

**No LLM.** The output is a deterministic function of:
- The player's SAN and classification.
- The engine's best SAN and principal variation (SAN list).
- Board features before vs after: material balance, hanging pieces,
  king safety, castling rights, pawn structure deltas.

The resulting ``spoken_summary`` is suitable for TTS. ``evidence_refs``
cite the facts the prose relied on so a reviewer can audit every claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

import chess

from chessmind.services.labeler import Classification, LabelResult


@dataclass(frozen=True)
class CoachingResponse:
    spoken_summary: str
    correction_san: str | None
    evidence_refs: list[str]
    board_features: dict[str, Any]


# --------------------------------------------------------------------------- #
# Board-feature deltas
# --------------------------------------------------------------------------- #


def _material_balance(board: chess.Board) -> int:
    """White-centric material in centipawns using standard values."""
    values = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
              chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}
    total = 0
    for piece_type, cp in values.items():
        total += len(board.pieces(piece_type, chess.WHITE)) * cp
        total -= len(board.pieces(piece_type, chess.BLACK)) * cp
    return total


def hanging_squares(board: chess.Board, color: chess.Color) -> list[int]:
    """Return squares of ``color``'s pieces that are attacked and under-defended."""
    hanging: list[int] = []
    for square in chess.scan_reversed(board.occupied_co[color]):
        piece = board.piece_at(square)
        if piece is None or piece.piece_type == chess.KING:
            continue
        attackers = board.attackers(not color, square)
        defenders = board.attackers(color, square)
        if attackers and len(attackers) > len(defenders):
            hanging.append(square)
    return hanging


def _king_in_check(board: chess.Board, color: chess.Color) -> bool:
    king_sq = board.king(color)
    if king_sq is None:
        return False
    return bool(board.attackers(not color, king_sq))


def _castling_rights(board: chess.Board, color: chess.Color) -> tuple[bool, bool]:
    return (board.has_kingside_castling_rights(color), board.has_queenside_castling_rights(color))


def _board_feature_delta(before: chess.Board, after: chess.Board, player_color: Literal["white", "black"]) -> dict[str, Any]:
    pc_color = chess.WHITE if player_color == "white" else chess.BLACK
    oc_color = not pc_color
    delta_material = _material_balance(after) - _material_balance(before)
    # Player-relative material delta (negative = player lost material).
    if player_color == "black":
        delta_material = -delta_material

    hang_before = hanging_squares(before, pc_color)
    hang_after = hanging_squares(after, pc_color)
    new_hanging = sorted(set(hang_after) - set(hang_before))

    opp_hang_before = hanging_squares(before, oc_color)
    opp_hang_after = hanging_squares(after, oc_color)
    captured_opp_hanging = sorted(set(opp_hang_before) - set(opp_hang_after))

    ks_before, qs_before = _castling_rights(before, pc_color)
    ks_after, qs_after = _castling_rights(after, pc_color)
    lost_kingside = ks_before and not ks_after
    lost_queenside = qs_before and not qs_after

    delivered_check = _king_in_check(after, oc_color)
    walked_into_check = _king_in_check(after, pc_color)

    return {
        "material_delta_player_relative": delta_material,
        "new_hanging_squares": [chess.square_name(s) for s in new_hanging],
        "captured_opp_hanging_squares": [chess.square_name(s) for s in captured_opp_hanging],
        "lost_castling_kingside": lost_kingside,
        "lost_castling_queenside": lost_queenside,
        "delivered_check": delivered_check,
        "walked_into_check": walked_into_check,
    }


# --------------------------------------------------------------------------- #
# Prose assembly
# --------------------------------------------------------------------------- #


def _format_cp(cp: int) -> str:
    pawns = cp / 100.0
    return f"{pawns:+.2f}"


def coach_move(
    *,
    board_before: chess.Board,
    board_after: chess.Board,
    player_color: Literal["white", "black"],
    played_san: str,
    label: LabelResult,
    best_san_list: list[str],  # top MultiPV best moves at the before position
    best_pv_san: list[str],  # SAN list of the engine's best principal variation
    position_id_ref: str,
    engine_analysis_id_ref: str,
) -> CoachingResponse:
    """Return a grounded coaching response for this move.

    Every sentence in ``spoken_summary`` is traceable to one of:
    - The engine PV (``best_pv_san``).
    - The engine's best move (``best_san_list[0]``).
    - A board-feature delta (``features`` dict).
    - The classification label and swing cp.
    """
    features = _board_feature_delta(board_before, board_after, player_color)
    best_san = best_san_list[0] if best_san_list else None
    pv_preview = " ".join(best_pv_san[:4])

    sentences: list[str] = []
    classification = label.classification

    # Opening sentence names the label and the swing.
    if classification == Classification.BEST:
        sentences.append(f"{played_san} is the top choice.")
    elif classification == Classification.PLAYABLE:
        sentences.append(
            f"{played_san} is playable — about {_format_cp(label.eval_swing_cp)} off the top line."
        )
    else:
        sentences.append(
            f"{played_san} is {_label_phrase(classification)} — "
            f"a {_format_cp(label.eval_swing_cp)}-pawn swing against you."
        )

    # Board-feature claims. Every one of these is derived from python-chess,
    # not the LLM.
    if features["new_hanging_squares"]:
        squares = ", ".join(features["new_hanging_squares"])
        sentences.append(f"Your piece on {squares} is now attacked and under-defended.")
    if features["captured_opp_hanging_squares"] and classification in (Classification.BEST, Classification.PLAYABLE):
        squares = ", ".join(features["captured_opp_hanging_squares"])
        sentences.append(f"You picked up material on {squares}.")
    if features["lost_castling_kingside"] or features["lost_castling_queenside"]:
        sides = []
        if features["lost_castling_kingside"]:
            sides.append("kingside")
        if features["lost_castling_queenside"]:
            sides.append("queenside")
        sentences.append(f"You gave up {' and '.join(sides)} castling rights.")
    if features["delivered_check"]:
        sentences.append("Your move delivers check.")
    if features["material_delta_player_relative"] <= -300 and classification in (
        Classification.MISTAKE,
        Classification.BLUNDER,
    ):
        pawns = abs(features["material_delta_player_relative"]) / 100.0
        sentences.append(f"The capture sequence loses about {pawns:.1f} pawns of material.")

    # Correction / stronger alternative.
    correction_san: str | None = None
    if classification != Classification.BEST and best_san and best_san != played_san:
        correction_san = best_san
        if pv_preview:
            sentences.append(f"Better was {best_san}, planning {pv_preview}.")
        else:
            sentences.append(f"Better was {best_san}.")

    # Mate flip
    if label.mate_flip == "lost_mate":
        sentences.append("You had forced mate before this move.")
    elif label.mate_flip == "allowed_mate":
        sentences.append("Your opponent now has a forced mate.")

    evidence: list[str] = [
        f"fen:{position_id_ref}",
        f"engine_analysis:{engine_analysis_id_ref}",
    ]
    if best_san:
        evidence.append(f"best_san:{best_san}")

    return CoachingResponse(
        spoken_summary=" ".join(sentences),
        correction_san=correction_san,
        evidence_refs=evidence,
        board_features=features,
    )


def _label_phrase(c: Classification) -> str:
    return {
        Classification.INACCURACY: "an inaccuracy",
        Classification.MISTAKE: "a mistake",
        Classification.BLUNDER: "a blunder",
    }.get(c, c.value)
