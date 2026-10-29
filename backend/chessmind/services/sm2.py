"""SuperMemo 2 scheduler.

Standard SM-2:
- Quality q in [0..5] drives the next interval.
- If q < 3: reset reps to 0, interval 1 day.
- Else: interval becomes 1, 6, prev * EF; EF is updated by
  EF' = EF + (0.1 - (5-q)*(0.08 + (5-q)*0.02)); clamped to >= 1.3.

We map our move classifications to quality:
  best        -> 5
  playable    -> 4
  inaccuracy  -> 3
  mistake     -> 2
  blunder     -> 1
  skipped     -> 0

A ``MasteryState`` row carries ease_factor, interval_days, reps, due_at.
``apply_review`` returns the updated SM-2 numbers; callers persist them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from chessmind.services.labeler import Classification


@dataclass
class SM2State:
    ease_factor: float = 2.5
    interval_days: float = 0.0
    reps: int = 0


@dataclass
class SM2Result:
    ease_factor: float
    interval_days: float
    reps: int
    due_at: datetime
    quality: int


_CLASSIFICATION_QUALITY = {
    Classification.BEST: 5,
    Classification.PLAYABLE: 4,
    Classification.INACCURACY: 3,
    Classification.MISTAKE: 2,
    Classification.BLUNDER: 1,
}


def classification_to_quality(c: Classification) -> int:
    return _CLASSIFICATION_QUALITY[c]


def apply_review(
    prev: SM2State,
    quality: int,
    *,
    now: datetime | None = None,
) -> SM2Result:
    """Apply a single review and return the new SM-2 state.

    quality must be in [0..5]. See module docstring for the mapping.
    """
    if not 0 <= quality <= 5:
        raise ValueError("quality must be between 0 and 5")
    now = now or datetime.now(timezone.utc)

    if quality < 3:
        reps = 0
        # Immediate re-review on failure (ARCHITECTURE §3.5 / Day-2 corrective
        # coaching flow): classic SM-2 defers to tomorrow, but a mistake made
        # right now needs to be retrained today — the next_drill endpoint picks
        # it back up on the next request.
        interval = 0.0
        ease = prev.ease_factor  # EF unchanged on failure in classic SM-2.
    else:
        reps = prev.reps + 1
        if reps == 1:
            interval = 1.0
        elif reps == 2:
            interval = 6.0
        else:
            interval = max(1.0, prev.interval_days * prev.ease_factor)
        delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
        ease = max(1.3, prev.ease_factor + delta)

    due_at = now + timedelta(days=interval)
    return SM2Result(
        ease_factor=round(ease, 4),
        interval_days=round(interval, 4),
        reps=reps,
        due_at=due_at,
        quality=quality,
    )


def mastery_state_label(reps: int, ease: float, interval: float, *, tested: bool = False) -> str:
    """Classify a MasteryState row into {untested|emerging|competent|mastered|decayed}.

    ``tested`` lets callers distinguish rows that have never been reviewed
    (``untested``) from rows that were reviewed and failed back to zero reps
    (``decayed`` / ``emerging``).
    """
    if reps == 0:
        if not tested:
            return "untested"
        # A failed review resets reps to 0 but the position has been seen.
        return "decayed" if ease < 2.3 else "emerging"
    if reps < 3:
        return "emerging"
    if interval >= 30 and ease >= 2.3:
        return "mastered"
    if interval >= 7 and ease >= 2.0:
        return "competent"
    return "emerging"
