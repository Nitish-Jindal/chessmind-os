"""Canonical FEN rules: strips counters, keeps EP target only when a legal EP capture exists."""

import chess

from chessmind.services.canonical_fen import canonical_key


def test_move_counters_are_stripped():
    board = chess.Board()
    assert len(canonical_key(board).split(" ")) == 4  # <board> <stm> <castling> <ep>


def test_ep_square_only_included_when_legal():
    # 1. e4: white's e2-e4 creates an EP target on e3, but no black pawn can capture.
    board = chess.Board()
    board.push_san("e4")
    key = canonical_key(board)
    # No legal EP from black's point of view, so the EP field is '-'.
    assert key.split(" ")[-1] == "-"


def test_ep_square_included_when_capture_is_legal():
    # Set up a position where EP is legal: 1.e4 d6 2.e5 d5 — white can take ... wait
    # a cleaner one: 1.e4 Nf6 2.e5 d5 — now 3.exd6 is a legal en passant.
    board = chess.Board()
    for san in ["e4", "Nf6", "e5", "d5"]:
        board.push_san(san)
    key = canonical_key(board)
    assert key.split(" ")[-1] == "d6", key


def test_castling_present():
    board = chess.Board()
    key = canonical_key(board)
    assert "KQkq" in key
