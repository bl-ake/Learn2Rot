# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AnkiTube add-on entry point."""

from __future__ import annotations

ADDON_MODULE = __name__


def initialize_addon() -> None:
    from aqt import mw

    if not mw:
        return

    from . import hooks
    from .logger import log, set_addon_module

    set_addon_module(ADDON_MODULE)
    hooks.set_addon_module(ADDON_MODULE)
    log("AnkiTube add-on loaded")
    hooks.register_hooks()


try:
    initialize_addon()
except ImportError:
    pass
