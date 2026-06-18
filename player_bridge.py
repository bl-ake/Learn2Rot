# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""YouTube player webview bridge and JS message handling."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from aqt import gui_hooks, mw
from aqt.qt import QUrl, QUrlQuery, QWebEngineFullScreenRequest, QWebEngineSettings
from aqt.utils import qconnect
from aqt.webview import AnkiWebView

from .config import get_config
from .logger import log

if TYPE_CHECKING:
    from .dock import AnkiTubeDock


class PlayerBridge:
    CMD_PREFIX = "ankittube:"

    def __init__(self, addon_module: str, dock: "AnkiTubeDock") -> None:
        self._addon_module = addon_module
        self._dock = dock
        self._player_ready = False
        self._pending_play = False

    @property
    def player_ready(self) -> bool:
        return self._player_ready

    @player_ready.setter
    def player_ready(self, value: bool) -> None:
        self._player_ready = value

    @property
    def pending_play(self) -> bool:
        return self._pending_play

    @pending_play.setter
    def pending_play(self, value: bool) -> None:
        self._pending_play = value

    def register_exports(self) -> None:
        mw.addonManager.setWebExports(self._addon_module, r"web/.*")

    def player_url(self) -> QUrl:
        package = mw.addonManager.addonFromModule(self._addon_module)
        config = get_config(self._addon_module)
        query = QUrlQuery()
        query.addQueryItem(
            "controls",
            "1" if config.get("youtube_show_controls", True) else "0",
        )
        query.addQueryItem(
            "fs",
            "1" if config.get("youtube_show_fullscreen", True) else "0",
        )
        url = QUrl(f"{mw.serverURL()}_addons/{package}/web/player.html")
        url.setQuery(query)
        return url

    def setup_webview(self, web: AnkiWebView) -> None:
        web.set_open_links_externally(False)
        web.set_bridge_command(self.on_bridge_command, self)
        gui_hooks.webview_did_receive_js_message.append(self.on_js_message)

    def setup_player_webview(self, web: AnkiWebView) -> None:
        web.settings().setAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True
        )
        qconnect(
            web.page().fullScreenRequested,
            lambda request, player_web=web: self._on_fullscreen_requested(
                player_web, request
            ),
        )

    def unregister(self) -> None:
        try:
            gui_hooks.webview_did_receive_js_message.remove(self.on_js_message)
        except ValueError:
            pass

    def on_bridge_command(self, cmd: str) -> None:
        log(f"bridge cmd: {cmd!r}")
        if not cmd.startswith(self.CMD_PREFIX):
            return
        self._dispatch(cmd[len(self.CMD_PREFIX) :])

    def on_js_message(
        self, handled: tuple[bool, object], message: str, context
    ) -> tuple:
        if not message.startswith(self.CMD_PREFIX):
            return handled
        log(f"js message hook: {message!r}")
        self._dispatch(message[len(self.CMD_PREFIX) :])
        return (True, None)

    def _dispatch(self, message: str) -> None:
        log(f"player message: {message!r}")
        self.handle_player_message(message)

    def _on_fullscreen_requested(
        self, web: AnkiWebView, request: QWebEngineFullScreenRequest
    ) -> None:
        log(
            "fullscreen requested "
            f"toggle_on={request.toggleOn()} dock={web is self._dock._web}"
        )
        request.accept()
        if request.toggleOn():
            if web is self._dock._web:
                self._dock._open_fullscreen()
            return
        fullscreen = self._dock._fullscreen
        if (
            fullscreen is not None
            and fullscreen.isVisible()
            and web is fullscreen._web
        ):
            self._dock._close_fullscreen()

    def active_web(self) -> AnkiWebView:
        fullscreen = self._dock._fullscreen
        if fullscreen and fullscreen.isVisible():
            return fullscreen._web
        return self._dock._web

    def eval_js(self, script: str) -> None:
        log(f"eval js: {script}")
        self.active_web().eval(script)

    def eval_js_check(self, script: str, label: str) -> None:
        def on_result(result: object) -> None:
            log(f"eval result ({label}): {result!r}")

        self.active_web().evalWithCallback(script, on_result)

    def eval_with_callback(self, script: str, callback: Callable[[object], None]) -> None:
        self.active_web().evalWithCallback(script, callback)

    def pause(self) -> None:
        self.eval_js("window.ankittube.pause();")

    def on_web_load_finished(self, ok: bool) -> None:
        log(f"webview loadFinished ok={ok} url={self.player_url().toString()}")
        if not ok:
            return
        self.eval_js_check("typeof window.ankittube", "ankittube type")
        self.eval_js_check("document.title", "document title")
        self._dock._sync_player_with_queue()

    def handle_player_message(self, message: str) -> None:
        dock = self._dock

        if message == "page_ready":
            return

        if message == "ready":
            self._player_ready = True
            fullscreen = dock._fullscreen
            if fullscreen and fullscreen.isVisible():
                fullscreen._sync_video()
                return
            item = dock._queue.current_item()
            if item:
                dock._load_current_video(autoplay=self._pending_play)
            return

        if message == "player_ready":
            dock._update_duration_from_player()
            if self._pending_play and dock._budget.has_time():
                self.eval_js("window.ankittube.play();")
            return

        if message.startswith("position:"):
            try:
                seconds = float(message.split(":", 1)[1])
            except ValueError:
                return
            item = dock._queue.current_item()
            if item:
                dock._update_position_cache(item.video_id, seconds)
            return

        if message.startswith("state:"):
            try:
                state = int(message.split(":", 1)[1])
            except ValueError:
                return
            if state == 1:
                if not dock._budget.has_time():
                    dock._handle_budget_exhausted()
                    return
                dock._is_playing = True
                if not dock._timer.isActive():
                    dock._timer.start()
                dock._refresh_queue_ui()
            elif state in (0, 2, 5):
                dock._is_playing = False
                dock._timer.stop()
                dock._refresh_queue_ui()
            return

        if message == "ended":
            dock._is_playing = False
            dock._timer.stop()
            was_last = (
                dock._queue.current_index >= 0
                and dock._queue.current_index >= len(dock._queue.items) - 1
            )
            dock._remove_finished_current()
            dock._refresh_queue_ui()
            dock._save_state()
            if not dock._queue.items:
                dock._pause_playback()
                self.eval_js("window.ankittube.clear();")
            elif not was_last and dock._budget.has_time():
                dock._play_current()
            elif dock._budget.has_time():
                dock._pause_playback()
            else:
                dock._handle_budget_exhausted()
            return

        if message.startswith("error"):
            dock._is_playing = False
            dock._timer.stop()
            detail = message.split(":", 1)[-1]
            log(f"playback error: {detail}")
            dock._refresh_queue_ui()

    def load_video(self, video_id: str, start: float, *, autoplay: bool) -> None:
        if autoplay:
            self._pending_play = True
            self.eval_js(
                f"window.ankittube.resumeOrPlay({json.dumps(video_id)});"
            )
        else:
            self._pending_play = False
            self.eval_js(
                f"window.ankittube.loadVideo({json.dumps(video_id)}, {start});"
            )
