# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Resolve the platform-tagged vendor directory for bundled deps."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

VENDOR_TAGS = (
    "macosx_arm64",
    "macosx_x86_64",
    "win_amd64",
)


def vendor_tag() -> str:
    """Return the vendor subdirectory tag for the current interpreter."""
    if sys.platform == "win32":
        return "win_amd64"
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        if machine in ("arm64", "aarch64"):
            return "macosx_arm64"
        return "macosx_x86_64"
    return sys.platform


def _looks_like_flat_vendor(vendor: Path) -> bool:
    if (vendor / "pymunk").is_dir():
        return True
    return any(vendor.glob("_cffi_backend*"))


def resolve_vendor_dir(root: Path | None = None) -> Path | None:
    """Return the vendor dir for this platform, or None if unavailable.

    Prefers ``vendor/<tag>/``. Falls back to a legacy flat ``vendor/`` tree
    (local Mac/Windows installs that predate tagged layouts).
    """
    base = Path(__file__).resolve().parent if root is None else Path(root)
    vendor = base / "vendor"
    if not vendor.is_dir():
        return None

    tagged = vendor / vendor_tag()
    if tagged.is_dir() and (
        (tagged / "pymunk").is_dir() or any(tagged.glob("_cffi_backend*"))
    ):
        return tagged

    if _looks_like_flat_vendor(vendor):
        return vendor

    # Tag dir exists but is empty / incomplete — still prefer it so callers
    # do not accidentally import the wrong platform's natives from a sibling.
    if tagged.is_dir():
        return tagged
    return None
