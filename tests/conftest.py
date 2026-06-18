# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Pytest fixtures for AnkiTube tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import addon_loader  # noqa: E402,F401 — installs Anki mocks


@pytest.fixture
def mock_mw() -> MagicMock:
    return sys.modules["aqt"].mw
