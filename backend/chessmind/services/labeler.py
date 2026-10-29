"""Classify a player's move from the Stockfish eval swing (White-centric).

Returns the Day-3 ``classification`` label plus the player-relative numbers
the UI surfaces. White-centric cp values are converted to player-relative
by multiplying by +1 for white and -1 for black.

Thresholds match Lichess's standard bands and are intentionally
configurable so a session can tighten or loosen them (e.g. tournament
mode penalises inaccuracies harder).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class Classification(str, Enum):
    BEST = "best"
    PLAYABLE = "playable"
    INACCURACY = "inaccuracy"
    MISTAKE = "mistake"
    BLUNDER = "blunder"


@dataclass(frozen=True)
class Thresholds:
    best_max_cpl: int = 20
    playable_max_cpl: int = 50
    inaccuracy_max_cpl: int = 150
    mistake_max_cpl: int = 300


DEFAULT_THRESHOLDS = Thresholds()


# A conventional "+infinity" cap for cp conversions of mate scores.
_MATE_CP = 100_000


@dataclass(frozen=True)
class EvalPoint:
    """A single evaluation sample from Stockfish, White-centric."""

    cp: int | None
    mate: int | None  # +N: White mates in N; -N: Black mates in N. 0 = immediate mate (shouldn't occur).

    @property
    def is_mate(self) -> bool:
        return self.mate is not None

    def to_cp_capped(self) -> int:
        """White-centric cp value; mate for white → +MATE_CP, mate for black → -MATE_CP."""
        if self.mate is not None:
            if self.mate > 0:
                return _MATE_CP - self.mate  # closer mates are "better"
            return -(_MATE_CP + self.mate)  # mate is negative → black mates
        return int(self.cp or 0)


@dataclass
class LabelResult:
    classification: Classification
    eval_before_cp: int | None  # White-centric; None only if both are mate.
    eval_after_cp: int | None
    eval_swing_cp: int  # Player-relative: positive = worse for player.
    player_relative_before: int
    player_relative_after: int
    mate_flip: Literal["lost_mate", "allowed_mate", None]


def label_move(
    *,
    player_color: Literal["white", "black"],
    before: EvalPoint,
    after: EvalPoint,
    played_was_best: bool = False,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> LabelResult:
    """Classify a move given White-centric evals before/after.

    If the played move exactly matches the engine's best move, we short-circuit
    to ``best`` so a tiny cp fluctuation doesn't downgrade it.
    """
    sign = 1 if player_color == "white" else -1

    before_cp_white = before.to_cp_capped()
    after_cp_white = after.to_cp_capped()
    pre_player = sign * before_cp_white
    post_player = sign * after_cp_white
    swing = pre_player - post_player  # positive = worse for player

    # Mate-flip handling: lost a winning mate, or allowed a losing mate.
    mate_flip: Literal["lost_mate", "allowed_mate", None] = None
    player_had_mate = _player_had_mate(before, player_color)
    player_now_lost = _player_lost_mate(after, player_color)
    opponent_now_has_mate = _opponent_has_mate(after, player_color)
    if player_had_mate and player_now_lost:
        mate_flip = "lost_mate"
    elif not _opponent_has_mate(before, player_color) and opponent_now_has_mate:
        mate_flip = "allowed_mate"

    if played_was_best and mate_flip is None:
        classification = Classification.BEST
    elif mate_flip in ("lost_mate", "allowed_mate"):
        classification = Classification.BLUNDER
    else:
        swing_pos = max(0, swing)
        if swing_pos <= thresholds.best_max_cpl:
            classification = Classification.BEST
        elif swing_pos <= thresholds.playable_max_cpl:
            classification = Classification.PLAYABLE
        elif swing_pos <= thresholds.inaccuracy_max_cpl:
            classification = Classification.INACCURACY
        elif swing_pos <= thresholds.mistake_max_cpl:
            classification = Classification.MISTAKE
        else:
            classification = Classification.BLUNDER

    return LabelResult(
        classification=classification,
        eval_before_cp=before.cp if before.cp is not None else before_cp_white,
        eval_after_cp=after.cp if after.cp is not None else after_cp_white,
        eval_swing_cp=max(0, swing),
        player_relative_before=pre_player,
        player_relative_after=post_player,
        mate_flip=mate_flip,
    )


def _player_had_mate(point: EvalPoint, color: Literal["white", "black"]) -> bool:
    if point.mate is None:
        return False
    return (color == "white" and point.mate > 0) or (color == "black" and point.mate < 0)


def _opponent_has_mate(point: EvalPoint, color: Literal["white", "black"]) -> bool:
    if point.mate is None:
        return False
    return (color == "white" and point.mate < 0) or (color == "black" and point.mate > 0)


def _player_lost_mate(point: EvalPoint, color: Literal["white", "black"]) -> bool:
    return not _player_had_mate(point, color)
