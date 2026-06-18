# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def test_normalize_youtube_url_watch_path() -> None:
    utils = load_addon_module("utils", "utils.py")
    assert utils.normalize_youtube_url("/watch?v=abcdefghijk") == (
        "https://www.youtube.com/watch?v=abcdefghijk"
    )


def test_extract_video_id_from_standard_url() -> None:
    utils = load_addon_module("utils", "utils.py")
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert utils.extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_all_video_ids_from_text() -> None:
    utils = load_addon_module("utils", "utils.py")
    text = "https://youtu.be/dQw4w9WgXcQ and dQw4w9WgXcQ"
    ids = utils.extract_all_video_ids(text)
    assert ids == ["dQw4w9WgXcQ"]


def test_format_seconds() -> None:
    utils = load_addon_module("utils", "utils.py")
    assert utils.format_seconds(0) == "0:00"
    assert utils.format_seconds(65) == "1:05"
    assert utils.format_seconds(3661) == "1:01:01"


def test_format_queue_item_label() -> None:
    utils = load_addon_module("utils", "utils.py")
    label = utils.format_queue_item_label("Test", 2, 5, playing_prefix="▶ ")
    assert label == "▶ (2/5) Test"


def test_allocate_queue_card_progress_orders_budget() -> None:
    utils = load_addon_module("utils", "utils.py")
    progress = utils.allocate_queue_card_progress(
        [120, 60],
        available_budget_seconds=30,
        seconds_per_card=15,
        video_ids=["a", "b"],
        playback_positions={},
    )
    assert progress[0].cards_done == 2
    assert progress[0].cards_total == 8
    assert progress[1].cards_done == 0
    assert progress[1].cards_total == 4
