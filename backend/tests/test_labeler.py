"""Labeler thresholds, player-relative sign conventions, mate flips."""

from __future__ import annotations

from chessmind.services.labeler import (
    Classification,
    DEFAULT_THRESHOLDS,
    EvalPoint,
    label_move,
)


def test_best_when_within_20cp_white():
    r = label_move(
        player_color="white",
        before=EvalPoint(cp=50, mate=None),
        after=EvalPoint(cp=40, mate=None),
    )
    assert r.classification is Classification.BEST
    assert r.eval_swing_cp == 10
    assert r.player_relative_before == 50
    assert r.player_relative_after == 40


def test_playable_up_to_50cp():
    r = label_move(
        player_color="white",
        before=EvalPoint(cp=100, mate=None),
        after=EvalPoint(cp=60, mate=None),
    )
    assert r.classification is Classification.PLAYABLE


def test_inaccuracy_band():
    r = label_move(
        player_color="white",
        before=EvalPoint(cp=100, mate=None),
        after=EvalPoint(cp=-20, mate=None),
    )
    assert r.classification is Classification.INACCURACY
    assert r.eval_swing_cp == 120


def test_mistake_band():
    r = label_move(
        player_color="white",
        before=EvalPoint(cp=100, mate=None),
        after=EvalPoint(cp=-150, mate=None),
    )
    assert r.classification is Classification.MISTAKE
    assert r.eval_swing_cp == 250


def test_blunder_above_300():
    r = label_move(
        player_color="white",
        before=EvalPoint(cp=200, mate=None),
        after=EvalPoint(cp=-200, mate=None),
    )
    assert r.classification is Classification.BLUNDER
    assert r.eval_swing_cp == 400


def test_player_relative_flipped_for_black():
    # White-centric eval went from -100 to -200 → bad for white, good for black.
    r = label_move(
        player_color="black",
        before=EvalPoint(cp=-100, mate=None),
        after=EvalPoint(cp=-200, mate=None),
    )
    # Player-relative: before = +100 (good for black), after = +200 (even better).
    assert r.player_relative_before == 100
    assert r.player_relative_after == 200
    assert r.eval_swing_cp == 0  # improvement, so swing clamped to 0
    assert r.classification is Classification.BEST


def test_played_was_best_short_circuits_label():
    r = label_move(
        player_color="white",
        before=EvalPoint(cp=100, mate=None),
        after=EvalPoint(cp=40, mate=None),
        played_was_best=True,
    )
    assert r.classification is Classification.BEST


def test_lost_mate_is_blunder():
    r = label_move(
        player_color="white",
        before=EvalPoint(cp=None, mate=3),  # white mates in 3
        after=EvalPoint(cp=50, mate=None),
    )
    assert r.classification is Classification.BLUNDER
    assert r.mate_flip == "lost_mate"


def test_allowed_mate_is_blunder():
    r = label_move(
        player_color="white",
        before=EvalPoint(cp=50, mate=None),
        after=EvalPoint(cp=None, mate=-2),  # black mates in 2 against white
    )
    assert r.classification is Classification.BLUNDER
    assert r.mate_flip == "allowed_mate"


def test_mate_improvement_is_best():
    """White had mate in 5, now mate in 3 — still best."""
    r = label_move(
        player_color="white",
        before=EvalPoint(cp=None, mate=5),
        after=EvalPoint(cp=None, mate=3),
    )
    # Mate stayed; swing capped at 0 because post is better than pre.
    assert r.classification is Classification.BEST
    assert r.mate_flip is None
