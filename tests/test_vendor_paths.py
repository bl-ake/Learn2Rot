# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_ROOT = _TESTS_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from vendor_paths import VENDOR_TAGS, resolve_vendor_dir, vendor_tag


def test_vendor_tags_match_packaging() -> None:
    assert VENDOR_TAGS == (
        "macosx_arm64",
        "macosx_x86_64",
        "win_amd64",
    )


def test_resolve_prefers_tagged_tree(tmp_path: Path, monkeypatch) -> None:
    vendor = tmp_path / "vendor"
    tag = vendor_tag()
    tagged = vendor / tag
    tagged.mkdir(parents=True)
    (tagged / "pymunk").mkdir()
    (tagged / "_cffi_backend.fake").write_text("x", encoding="utf-8")
    # Flat decoy that must lose to the tagged tree.
    (vendor / "pymunk").mkdir()
    assert resolve_vendor_dir(tmp_path) == tagged


def test_resolve_falls_back_to_flat_vendor(tmp_path: Path) -> None:
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "pymunk").mkdir()
    assert resolve_vendor_dir(tmp_path) == vendor


def test_resolve_missing_vendor(tmp_path: Path) -> None:
    assert resolve_vendor_dir(tmp_path) is None
