from __future__ import annotations

import os

from anki.collection import OpChangesAfterUndo

from aqt import gui_hooks, mw, tr
from aqt.qt import QAction, QMenu
from aqt.utils import openLink, qconnect, showInfo

from .budget import BudgetManager
from .config_dialog import ConfigDialog
from .dock import AnkiTubeDock
from .logger import clear_log, log, log_path, set_addon_module

ADDON_MODULE = __name__
set_addon_module(ADDON_MODULE)
log("AnkiTube add-on loaded")
_dock: AnkiTubeDock | None = None
_budget: BudgetManager | None = None


def _get_budget() -> BudgetManager:
    global _budget
    if _budget is None:
        _budget = BudgetManager(ADDON_MODULE, on_change=_on_budget_changed)
        _budget.load()
    return _budget


def _get_dock() -> AnkiTubeDock:
    global _dock
    if _dock is None:
        _dock = AnkiTubeDock(ADDON_MODULE, _get_budget())
    return _dock


def _on_budget_changed() -> None:
    if _dock is not None:
        _dock.on_budget_changed()


def _show_dock() -> None:
    dock = _get_dock()
    dock.show()
    dock.raise_()


def _toggle_dock() -> None:
    dock = _get_dock()
    dock.setVisible(not dock.isVisible())


def _open_settings() -> None:
    dialog = ConfigDialog(ADDON_MODULE, _get_budget(), parent=mw)
    if dialog.exec() and _dock is not None:
        _dock.apply_settings()


def _open_debug_log() -> None:
    path = log_path()
    if not os.path.exists(path):
        showInfo("No AnkiTube debug log exists yet.\n\nEnable debug logging in Settings first.")
        return
    openLink(f"file://{path}")


def _clear_debug_log() -> None:
    clear_log()
    log("debug log cleared")
    showInfo("AnkiTube debug log cleared.")


def _on_profile_open() -> None:
    _get_dock()


def _on_profile_close() -> None:
    global _dock, _budget
    if _dock is not None:
        _dock.shutdown()
        _dock.deleteLater()
        _dock = None
    _budget = None


def _on_answer_card(reviewer, card, ease) -> None:
    if mw.state != "review":
        return
    _get_dock().on_card_answered()


def _is_answer_card_undo(changes: OpChangesAfterUndo) -> bool:
    if changes.operation == tr.actions_answer_card():
        return True
    return "answer card" in changes.operation.lower()


def _on_undo(changes: OpChangesAfterUndo) -> None:
    if not _is_answer_card_undo(changes):
        return
    log(f"undo detected for answer card: {changes.operation!r}")
    _get_dock().on_review_undo()


def _on_state_change(new_state, old_state) -> None:
    config = mw.addonManager.getConfig(ADDON_MODULE) or {}
    if not config.get("show_dock_in_review_only", False):
        return
    dock = _get_dock()
    dock.setVisible(new_state == "review")


def _setup_menu() -> None:
    menu = QMenu("AnkiTube", mw)
    mw.form.menuTools.addMenu(menu)

    show_action = QAction("Show Player", mw)
    qconnect(show_action.triggered, _show_dock)
    menu.addAction(show_action)

    toggle_action = QAction("Toggle Player", mw)
    qconnect(toggle_action.triggered, _toggle_dock)
    menu.addAction(toggle_action)

    settings_action = QAction("Settings...", mw)
    qconnect(settings_action.triggered, _open_settings)
    menu.addAction(settings_action)

    view_log_action = QAction("View Debug Log", mw)
    qconnect(view_log_action.triggered, _open_debug_log)
    menu.addAction(view_log_action)

    clear_log_action = QAction("Clear Debug Log", mw)
    qconnect(clear_log_action.triggered, _clear_debug_log)
    menu.addAction(clear_log_action)


gui_hooks.profile_did_open.append(_on_profile_open)
gui_hooks.profile_will_close.append(_on_profile_close)
gui_hooks.reviewer_did_answer_card.append(_on_answer_card)
gui_hooks.state_did_undo.append(_on_undo)
gui_hooks.state_did_change.append(_on_state_change)
gui_hooks.main_window_did_init.append(_setup_menu)
