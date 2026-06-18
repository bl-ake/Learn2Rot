# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def test_budget_clamps_to_max(mock_mw) -> None:
    budget_mod = load_addon_module("budget", "budget.py")
    budget = budget_mod.BudgetManager("AnkiTube")
    budget.load()
    budget.seconds = 9999
    assert budget.seconds == 600


def test_budget_add_and_subtract(mock_mw) -> None:
    budget_mod = load_addon_module("budget", "budget.py")
    budget = budget_mod.BudgetManager("AnkiTube")
    budget.load()
    budget.add_seconds(30)
    assert budget.seconds == 30
    budget.subtract_seconds(10)
    assert budget.seconds == 20


def test_budget_consume_second(mock_mw) -> None:
    budget_mod = load_addon_module("budget", "budget.py")
    budget = budget_mod.BudgetManager("AnkiTube")
    budget.load()
    budget.seconds = 1
    assert budget.consume_second() is True
    assert budget.seconds == 0
    assert budget.consume_second() is False
