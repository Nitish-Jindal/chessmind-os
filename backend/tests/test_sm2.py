"""SM-2 scheduler: intervals, quality mapping, ease-factor update."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chessmind.services.labeler import Classification
from chessmind.services.sm2 import (
    MAX_INTERVAL_DAYS,
    SM2State,
    apply_review,
    classification_to_quality,
    mastery_state_label,
)


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def test_classification_quality_mapping():
    assert classification_to_quality(Classification.BEST) == 5
    assert classification_to_quality(Classification.PLAYABLE) == 4
    assert classification_to_quality(Classification.INACCURACY) == 3
    assert classification_to_quality(Classification.MISTAKE) == 2
    assert classification_to_quality(Classification.BLUNDER) == 1


def test_first_review_q5_sets_interval_1():
    out = apply_review(SM2State(), quality=5, now=NOW)
    assert out.reps == 1
    assert out.interval_days == 1.0
    assert out.ease_factor > 2.5  # q=5 bumps ease
    assert out.due_at == NOW + timedelta(days=1)


def test_second_review_q5_sets_interval_6():
    state = apply_review(SM2State(), quality=5, now=NOW)
    out = apply_review(SM2State(ease_factor=state.ease_factor, interval_days=state.interval_days, reps=state.reps), quality=5, now=NOW)
    assert out.reps == 2
    assert out.interval_days == 6.0


def test_third_review_scales_by_ease():
    prev = SM2State(ease_factor=2.6, interval_days=6.0, reps=2)
    out = apply_review(prev, quality=5, now=NOW)
    assert out.reps == 3
    # 6 * 2.6 = 15.6, then round
    assert abs(out.interval_days - 15.6) < 0.1


def test_interval_is_capped_over_many_correct_reviews():
    state = SM2State()
    for _ in range(100):
        out = apply_review(state, quality=5, now=NOW)
        state = SM2State(ease_factor=out.ease_factor, interval_days=out.interval_days, reps=out.reps)
    assert out.interval_days == MAX_INTERVAL_DAYS
    assert out.due_at == NOW + timedelta(days=MAX_INTERVAL_DAYS)


def test_blunder_resets_reps_and_interval():
    prev = SM2State(ease_factor=2.6, interval_days=15.6, reps=3)
    out = apply_review(prev, quality=1, now=NOW)  # blunder
    assert out.reps == 0
    # Day-2 flow: failed review is immediately due so the next_drill endpoint
    # picks it right back up.
    assert out.interval_days == 0.0
    assert out.due_at == NOW
    # Ease unchanged on failure (classic SM-2).
    assert out.ease_factor == 2.6


def test_ease_factor_floor():
    prev = SM2State(ease_factor=1.3, interval_days=1.0, reps=1)
    out = apply_review(prev, quality=3, now=NOW)
    # q=3 delta = 0.1 - 2*(0.08 + 2*0.02) = 0.1 - 0.24 = -0.14, clamped
    assert out.ease_factor == 1.3


def test_quality_bounds_validation():
    with pytest.raises(ValueError):
        apply_review(SM2State(), quality=6)
    with pytest.raises(ValueError):
        apply_review(SM2State(), quality=-1)


def test_mastery_state_label_tiers():
    assert mastery_state_label(reps=0, ease=2.5, interval=0.0) == "untested"
    assert mastery_state_label(reps=0, ease=2.5, interval=0.0, tested=True) == "emerging"
    assert mastery_state_label(reps=0, ease=2.0, interval=0.0, tested=True) == "decayed"
    assert mastery_state_label(reps=1, ease=2.5, interval=1.0) == "emerging"
    assert mastery_state_label(reps=5, ease=2.4, interval=10.0) == "competent"
    assert mastery_state_label(reps=6, ease=2.4, interval=45.0) == "mastered"
