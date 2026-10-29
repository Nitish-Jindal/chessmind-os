"""Grounded coaching: prose is deterministic + traceable to board features/PV."""

from __future__ import annotations

import chess

from chessmind.services.coaching import coach_move
from chessmind.services.labeler import Classification, EvalPoint, label_move


def test_best_move_prose_mentions_top_choice():
    board_before = chess.Board()
    board_after = chess.Board()
    board_after.push_san("e4")

    label = label_move(
        player_color="white",
        before=EvalPoint(cp=30, mate=None),
        after=EvalPoint(cp=25, mate=None),
        played_was_best=True,
    )
    out = coach_move(
        board_before=board_before,
        board_after=board_after,
        player_color="white",
        played_san="e4",
        label=label,
        best_san_list=["e4", "d4", "Nf3"],
        best_pv_san=["e4", "e5", "Nf3", "Nc6"],
        position_id_ref="pos-123",
        engine_analysis_id_ref="eng-456",
    )
    assert out.spoken_summary.startswith("e4 is the top choice.")
    assert out.correction_san is None
    assert "fen:pos-123" in out.evidence_refs
    assert "engine_analysis:eng-456" in out.evidence_refs


def test_blunder_prose_names_correction_and_swing():
    board_before = chess.Board()
    board_after = chess.Board()
    board_after.push_san("f3")  # Bongcloud setup; terrible move

    label = label_move(
        player_color="white",
        before=EvalPoint(cp=30, mate=None),
        after=EvalPoint(cp=-500, mate=None),
    )
    assert label.classification is Classification.BLUNDER
    out = coach_move(
        board_before=board_before,
        board_after=board_after,
        player_color="white",
        played_san="f3",
        label=label,
        best_san_list=["e4", "d4"],
        best_pv_san=["e4", "e5", "Nf3", "Nc6", "Bb5"],
        position_id_ref="pos-7",
        engine_analysis_id_ref="eng-9",
    )
    assert "blunder" in out.spoken_summary
    assert "+5.30" in out.spoken_summary  # swing in pawns, formatted +X.XX
    assert out.correction_san == "e4"
    assert "e4" in out.spoken_summary
    # The PV preview should appear.
    assert "e4 e5 Nf3 Nc6" in out.spoken_summary


def test_hanging_piece_detected_and_mentioned():
    """White queen moves to a square attacked by Black's queen with no defender → hanging."""
    board_before = chess.Board("4k3/8/8/8/3q4/8/4Q3/4K3 w - - 0 1")
    board_after = board_before.copy()
    board_after.push_san("Qe4")  # attacked by Qd4, undefended (king too far)

    label = label_move(
        player_color="white",
        before=EvalPoint(cp=-100, mate=None),
        after=EvalPoint(cp=-900, mate=None),
    )
    assert label.classification is Classification.BLUNDER
    out = coach_move(
        board_before=board_before,
        board_after=board_after,
        player_color="white",
        played_san="Qe4",
        label=label,
        best_san_list=["Qe3"],
        best_pv_san=["Qe3", "Qxe3+", "Kxe3"],
        position_id_ref="p",
        engine_analysis_id_ref="e",
    )
    assert "e4" in out.spoken_summary.lower()
    assert "attacked and under-defended" in out.spoken_summary
    assert out.correction_san == "Qe3"


def test_castling_loss_mentioned():
    # White king on e1 moves (loses castling rights).
    board_before = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
    board_after = board_before.copy()
    board_after.push_san("Ke2")

    label = label_move(
        player_color="white",
        before=EvalPoint(cp=30, mate=None),
        after=EvalPoint(cp=-200, mate=None),
    )
    out = coach_move(
        board_before=board_before,
        board_after=board_after,
        player_color="white",
        played_san="Ke2",
        label=label,
        best_san_list=["Nc3"],
        best_pv_san=["Nc3"],
        position_id_ref="p",
        engine_analysis_id_ref="e",
    )
    assert "kingside" in out.spoken_summary
    assert "queenside" in out.spoken_summary
    # Correction present on non-best moves.
    assert out.correction_san == "Nc3"


def test_evidence_refs_cite_facts_used():
    label = label_move(
        player_color="white",
        before=EvalPoint(cp=0, mate=None),
        after=EvalPoint(cp=0, mate=None),
    )
    out = coach_move(
        board_before=chess.Board(),
        board_after=chess.Board(),
        player_color="white",
        played_san="--",
        label=label,
        best_san_list=["e4"],
        best_pv_san=["e4"],
        position_id_ref="pos:abc",
        engine_analysis_id_ref="eng:xyz",
    )
    assert any(ref.startswith("fen:pos:abc") for ref in out.evidence_refs)
    assert any(ref.startswith("engine_analysis:eng:xyz") for ref in out.evidence_refs)
    assert any(ref.startswith("best_san:e4") for ref in out.evidence_refs)
