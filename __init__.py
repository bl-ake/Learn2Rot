# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Learn2Rot add-on entry point."""

from __future__ import annotations

import sys
from pathlib import Path

ADDON_MODULE = __name__

# Bundled third-party packages (see requirements.txt / vendor/).
_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
if _VENDOR_DIR.is_dir():
    _vendor_path = str(_VENDOR_DIR)
    if _vendor_path not in sys.path:
        sys.path.insert(0, _vendor_path)


def initialize_addon() -> None:
    from aqt import mw

    if not mw:
        return

    from . import hooks
    from .logger import log, set_addon_module

    set_addon_module(ADDON_MODULE)
    hooks.set_addon_module(ADDON_MODULE)
    log("Learn2Rot add-on loaded")
    hooks.register_hooks()


try:
    initialize_addon()
except ImportError:
    pass
