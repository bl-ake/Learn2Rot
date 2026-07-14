# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def test_load_dock_visible_defaults_true(mock_mw) -> None:
    persistence_mod = load_addon_module("persistence", "persistence.py")
    budget = MagicMock()
    persistence = persistence_mod.DockPersistence("AnkiTube", budget)
    assert persistence.load_dock_visible() is True


def test_save_state_persists_dock_visible(mock_mw) -> None:
    persistence_mod = load_addon_module("persistence", "persistence.py")
    queue_mod = load_addon_module("queue", "queue.py")
    budget = MagicMock()
    persistence = persistence_mod.DockPersistence("AnkiTube", budget)
    queue = queue_mod.VideoQueue()

    persistence.save_state(
        queue,
        positions={},
        lifetime_earned_seconds=0,
        queue_visible=True,
        dock_visible=False,
    )
    assert persistence.load_dock_visible() is False
