# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Detect answered-card revlogs that arrived via AnkiWeb sync."""

from __future__ import annotations

from typing import Iterable


def is_answer_revlog(*, revlog_type: int, ease: int, factor: int) -> bool:
    """True for reviews that would fire reviewer_did_answer_card.

    Excludes manual/reschedule entries and filtered-deck cram (no reschedule).
    """
    if ease <= 0:
        return False
    if revlog_type in (0, 1, 2):
        return True
    # type 3 = filtered/cram; factor 0 means rescheduling was disabled
    return revlog_type == 3 and factor != 0


def count_new_answer_reviews(
    previous_ids: set[int],
    rows: Iterable[tuple[int, int, int, int]],
) -> int:
    """Count answer-type revlogs whose id was not present before sync.

    Each row is ``(id, type, ease, factor)``.
    """
    count = 0
    for rev_id, revlog_type, ease, factor in rows:
        if rev_id in previous_ids:
            continue
        if is_answer_revlog(
            revlog_type=int(revlog_type),
            ease=int(ease),
            factor=int(factor),
        ):
            count += 1
    return count
