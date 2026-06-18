# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AnkiTube hook handlers and menu setup."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from anki.collection import OpChangesAfterUndo

from aqt import gui_hooks, mw, tr
from aqt.qt import QAction, QMenu
from aqt.utils import openLink, qconnect, showInfo

from .config import get_config
from .config_dialog import ConfigDialog
from .logger import clear_log, log, log_path

if TYPE_CHECKING:
    from .budget import BudgetManager
    from .dock import AnkiTubeDock

_dock: Optional["AnkiTubeDock"] = None
_budget: Optional["BudgetManager"] = None
_addon_module: str = ""


def set_addon_module(module: str) -> None:
    global _addon_module
    _addon_module = module


def get_budget() -> "BudgetManager":
    global _budget
    from .budget import BudgetManager

    if _budget is None:
        _budget = BudgetManager(_addon_module, on_change=_on_budget_changed)
        _budget.load()
    return _budget


def get_dock() -> "AnkiTubeDock":
    global _dock
    from .dock import AnkiTubeDock

    if _dock is None:
        _dock = AnkiTubeDock(_addon_module, get_budget())
    return _dock


def _on_budget_changed() -> None:
    if _dock is not None:
        _dock.on_budget_changed()


def show_dock() -> None:
    dock = get_dock()
    dock.show()
    dock.raise_()


def toggle_dock() -> None:
    dock = get_dock()
    dock.setVisible(not dock.isVisible())


def open_settings() -> None:
    dialog = ConfigDialog(_addon_module, get_budget(), parent=mw)
    if dialog.exec() and _dock is not None:
        _dock.apply_settings()


def open_debug_log() -> None:
    path = log_path()
    if not os.path.exists(path):
        showInfo("No AnkiTube debug log exists yet.\n\nEnable debug logging in Settings first.")
        return
    openLink(f"file://{path}")


def clear_debug_log() -> None:
    clear_log()
    log("debug log cleared")
    showInfo("AnkiTube debug log cleared.")


def on_profile_open() -> None:
    get_dock()


def on_profile_close() -> None:
    global _dock, _budget
    if _dock is not None:
        _dock.shutdown()
        _dock.deleteLater()
        _dock = None
    _budget = None


def on_answer_card(reviewer, card, ease) -> None:
    if mw.state != "review":
        return
    get_dock().on_card_answered()


def _is_answer_card_undo(changes: OpChangesAfterUndo) -> bool:
    if changes.operation == tr.actions_answer_card():
        return True
    return "answer card" in changes.operation.lower()


def on_undo(changes: OpChangesAfterUndo) -> None:
    if not _is_answer_card_undo(changes):
        return
    log(f"undo detected for answer card: {changes.operation!r}")
    get_dock().on_review_undo()


def on_state_change(new_state, old_state) -> None:
    config = get_config(_addon_module)
    if not config.get("show_dock_in_review_only", False):
        return
    dock = get_dock()
    dock.setVisible(new_state == "review")


def setup_menu() -> None:
    menu = QMenu("AnkiTube", mw)
    mw.form.menuTools.addMenu(menu)

    show_action = QAction("Show Player", mw)
    qconnect(show_action.triggered, show_dock)
    menu.addAction(show_action)

    toggle_action = QAction("Toggle Player", mw)
    qconnect(toggle_action.triggered, toggle_dock)
    menu.addAction(toggle_action)

    settings_action = QAction("Settings...", mw)
    qconnect(settings_action.triggered, open_settings)
    menu.addAction(settings_action)

    view_log_action = QAction("View Debug Log", mw)
    qconnect(view_log_action.triggered, open_debug_log)
    menu.addAction(view_log_action)

    clear_log_action = QAction("Clear Debug Log", mw)
    qconnect(clear_log_action.triggered, clear_debug_log)
    menu.addAction(clear_log_action)


def register_hooks() -> None:
    gui_hooks.profile_did_open.append(on_profile_open)
    gui_hooks.profile_will_close.append(on_profile_close)
    gui_hooks.reviewer_did_answer_card.append(on_answer_card)
    gui_hooks.state_did_undo.append(on_undo)
    gui_hooks.state_did_change.append(on_state_change)
    gui_hooks.main_window_did_init.append(setup_menu)
