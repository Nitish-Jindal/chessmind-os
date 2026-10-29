"""Canonical FEN normalization for the opening graph.

Two different move orders that reach the same position must map to the
same ``canonical_key`` so transpositions converge on one ``Position`` row.

Normalization rules:

- Keep: board placement, side to move, castling rights.
- EP target: keep only when a legal en-passant capture is actually
  available on this position (``en_passant='legal'`` in python-chess).
  This matches §4.2 of ARCHITECTURE.md.
- Drop: halfmove clock and fullmove number (not part of position identity).
"""

from __future__ import annotations

import chess


def canonical_key(board: chess.Board) -> str:
    """Return the canonical EPD-style key for ``board``.

    ``python-chess`` already does the heavy lifting via ``epd`` with
    ``en_passant='legal'``; EPD omits the move counters by construction.
    """
    return board.epd(en_passant="legal")
