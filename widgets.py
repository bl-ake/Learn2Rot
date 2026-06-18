# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Drag-and-drop widgets, event filters, and fullscreen player."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from aqt import mw
from aqt.qt import (
    QAbstractSpinBox,
    QApplication,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QEvent,
    QKeyEvent,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QObject,
    QPlainTextEdit,
    Qt,
    QTextEdit,
    QWidget,
)
from aqt.utils import qconnect
from aqt.webview import AnkiWebView, AnkiWebViewKind

from .utils import mime_has_youtube_url

if TYPE_CHECKING:
    from .dock import AnkiTubeDock


class _UrlDropMixin:
    _dock: "AnkiTubeDock"

    def _accept_url_drag(self, event: QDragEnterEvent | QDropEvent) -> bool:
        if mime_has_youtube_url(event.mimeData()):
            event.acceptProposedAction()
            return True
        return False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not self._accept_url_drag(event):
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if not self._accept_url_drag(event):
            super().dragMoveEvent(event)

    def _drop_at_top(self, event: QDropEvent) -> bool:
        height = self.height()
        if height <= 0:
            return False
        return event.position().y() < height / 2

    def dropEvent(self, event: QDropEvent) -> None:
        at_top = self._drop_at_top(event)
        if self._dock.add_urls_from_mime(event.mimeData(), at_top=at_top):
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class AnkiTubePanel(_UrlDropMixin, QWidget):
    def __init__(self, dock: "AnkiTubeDock") -> None:
        super().__init__()
        self._dock = dock
        self.setAcceptDrops(True)


class QueueListWidget(_UrlDropMixin, QListWidget):
    def __init__(self, dock: "AnkiTubeDock") -> None:
        super().__init__()
        self._dock = dock
        self.setAcceptDrops(True)


class MainWindowResizeFilter(QObject):
    def __init__(self, dock: "AnkiTubeDock") -> None:
        super().__init__(dock)
        self._dock = dock

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is mw and event.type() == QEvent.Type.Resize:
            self._dock._schedule_layout_settle()
        return False


class PlayerShortcutFilter(QObject):
    SEEK_SECONDS = 5
    VOLUME_STEP = 5

    def __init__(self, dock: "AnkiTubeDock") -> None:
        super().__init__(dock)
        self._dock = dock

    def _dock_alive(self) -> bool:
        dock = self._dock
        if dock is None:
            return False
        try:
            dock.isVisible()
        except RuntimeError:
            return False
        return True

    def _should_handle(self) -> bool:
        if not self._dock_alive():
            return False
        if not self._dock._current_item():
            return False
        fullscreen = self._dock._fullscreen
        if fullscreen is not None and fullscreen.isVisible():
            return True
        if not self._dock.isVisible():
            return False
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ):
            return False
        return True

    def _is_option_key(self, key: int) -> bool:
        return key in (Qt.Key.Key_Alt, Qt.Key.Key_AltGr)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if not isinstance(event, QKeyEvent):
            return False
        try:
            if not self._should_handle():
                return False

            key_event = event
            key = key_event.key()

            if self._is_option_key(key):
                if event.type() == QEvent.Type.KeyPress:
                    if key_event.isAutoRepeat():
                        return self._dock._hold_paused
                    self._dock.hold_pause_begin()
                    return True
                if self._dock._hold_paused:
                    self._dock.hold_pause_end()
                return True

            if event.type() != QEvent.Type.KeyPress:
                return False
            if key_event.isAutoRepeat():
                return False

            modifiers = key_event.modifiers()
            if modifiers not in (
                Qt.KeyboardModifier.NoModifier,
                Qt.KeyboardModifier.KeypadModifier,
            ):
                return False

            if key == Qt.Key.Key_P:
                self._dock._toggle_playback()
                return True
            if key == Qt.Key.Key_G:
                self._dock._toggle_fullscreen()
                return True
            if key == Qt.Key.Key_C:
                self._dock._toggle_captions()
                return True
            if key == Qt.Key.Key_Left:
                self._dock._seek_relative(-self.SEEK_SECONDS)
                return True
            if key == Qt.Key.Key_Right:
                self._dock._seek_relative(self.SEEK_SECONDS)
                return True
            if key == Qt.Key.Key_Up:
                self._dock._adjust_volume(self.VOLUME_STEP)
                return True
            if key == Qt.Key.Key_Down:
                self._dock._adjust_volume(-self.VOLUME_STEP)
                return True
        except RuntimeError:
            return False

        return False


class FullscreenPlayer(QMainWindow):
    def __init__(self, dock: "AnkiTubeDock") -> None:
        super().__init__(parent=mw, flags=Qt.WindowType.Window)
        self._dock = dock
        self._closing = False
        self._pending_video_id: Optional[str] = None
        self._pending_start_seconds = 0.0
        self._pending_autoplay = False
        self.setWindowTitle("AnkiTube")
        self._web = AnkiWebView(kind=AnkiWebViewKind.EDITOR)
        self._web.requiresCol = False
        self.setCentralWidget(self._web)
        self._web.set_open_links_externally(False)
        self._web.set_bridge_command(self._dock._bridge.on_bridge_command, self)
        self._web.load_url(dock._bridge.player_url())
        dock._bridge.setup_player_webview(self._web)
        qconnect(self._web.loadFinished, self._on_load_finished)

    def prepare_video(self, video_id: str, start_seconds: float, autoplay: bool) -> None:
        self._pending_video_id = video_id
        self._pending_start_seconds = start_seconds
        self._pending_autoplay = autoplay
        self._sync_video()

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._sync_video()

    def _sync_video(self) -> None:
        if not self._pending_video_id:
            return
        video_id = self._pending_video_id
        start = self._pending_start_seconds
        autoplay = self._pending_autoplay
        self._web.eval(
            "window.ankittube.loadVideo("
            f"{json.dumps(video_id)}, {start});"
        )
        if autoplay:
            self._web.eval("window.ankittube.play();")

    def closeEvent(self, event) -> None:
        if self._closing:
            self._dock._fullscreen = None
            super().closeEvent(event)
            return
        event.ignore()
        self._dock._close_fullscreen()
