# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def test_is_answer_revlog_accepts_learn_review_relearn() -> None:
    sync_credit = load_addon_module("sync_credit", "sync_credit.py")
    assert sync_credit.is_answer_revlog(revlog_type=0, ease=1, factor=0)
    assert sync_credit.is_answer_revlog(revlog_type=1, ease=3, factor=2500)
    assert sync_credit.is_answer_revlog(revlog_type=2, ease=1, factor=0)


def test_is_answer_revlog_filtered_requires_reschedule() -> None:
    sync_credit = load_addon_module("sync_credit", "sync_credit.py")
    assert sync_credit.is_answer_revlog(revlog_type=3, ease=2, factor=2500)
    assert not sync_credit.is_answer_revlog(revlog_type=3, ease=2, factor=0)


def test_is_answer_revlog_rejects_manual_and_zero_ease() -> None:
    sync_credit = load_addon_module("sync_credit", "sync_credit.py")
    assert not sync_credit.is_answer_revlog(revlog_type=4, ease=0, factor=2500)
    assert not sync_credit.is_answer_revlog(revlog_type=5, ease=0, factor=0)
    assert not sync_credit.is_answer_revlog(revlog_type=1, ease=0, factor=2500)


def test_count_new_answer_reviews_ignores_previous_and_non_answers() -> None:
    sync_credit = load_addon_module("sync_credit", "sync_credit.py")
    previous = {10, 20}
    rows = [
        (10, 1, 3, 2500),  # already local
        (30, 1, 2, 2500),  # new answer
        (31, 4, 0, 2500),  # manual
        (32, 3, 1, 0),  # cram without reschedule
        (33, 0, 1, 0),  # new learn
        (34, 3, 3, 2000),  # filtered with reschedule
    ]
    assert sync_credit.count_new_answer_reviews(previous, rows) == 3
