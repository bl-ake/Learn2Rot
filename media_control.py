# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""System-wide Now Playing observation and play/pause control."""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Protocol


_OSASCRIPT_TIMEOUT_SEC = 2.0

_JXA_GET_NOW_PLAYING = """
ObjC.import('Foundation');
function run() {
  const MediaRemote = $.NSBundle.bundleWithPath(
    '/System/Library/PrivateFrameworks/MediaRemote.framework/'
  );
  MediaRemote.load;
  const MRNowPlayingRequest = $.NSClassFromString('MRNowPlayingRequest');
  if (!MRNowPlayingRequest) {
    return JSON.stringify({title: '', artist: '', isPlaying: false, error: 'MRNowPlayingRequest unavailable'});
  }
  let title = '';
  let artist = '';
  let isPlaying = false;
  try {
    const item = MRNowPlayingRequest.localNowPlayingItem;
    if (item) {
      const info = item.nowPlayingInfo;
      if (info) {
        const t = info.valueForKey('kMRMediaRemoteNowPlayingInfoTitle');
        const a = info.valueForKey('kMRMediaRemoteNowPlayingInfoArtist');
        const rate = info.valueForKey('kMRMediaRemoteNowPlayingInfoPlaybackRate');
        if (t) { title = t.js || ''; }
        if (a) { artist = a.js || ''; }
        if (rate !== undefined && rate !== null) {
          const n = Number(rate.js !== undefined ? rate.js : rate);
          isPlaying = !isNaN(n) && n !== 0;
        }
      }
    }
  } catch (e) {
    return JSON.stringify({title: '', artist: '', isPlaying: false, error: String(e)});
  }
  return JSON.stringify({title: title, artist: artist, isPlaying: isPlaying});
}
"""

_JXA_SEND_COMMAND = """
ObjC.import('Foundation');
function run(argv) {
  const command = Number(argv[0] || 1);
  const MediaRemote = $.NSBundle.bundleWithPath(
    '/System/Library/PrivateFrameworks/MediaRemote.framework/'
  );
  MediaRemote.load;
  const MRNowPlayingController = $.NSClassFromString('MRNowPlayingController');
  if (!MRNowPlayingController) {
    return 'error: MRNowPlayingController unavailable';
  }
  const controller = MRNowPlayingController.localRouteController;
  const commandOptions = $.NSDictionary.alloc.init;
  controller.sendCommandOptionsCompletion(command, commandOptions, null);
  return 'ok';
}
"""

_CMD_PLAY = 0
_CMD_PAUSE = 1
_CMD_TOGGLE = 2


@dataclass
class NowPlayingInfo:
    title: str = ""
    artist: str = ""
    is_playing: bool = False
    supported: bool = True
    error: str = ""

    def display_label(self) -> str:
        if not self.supported:
            return self.error or "System media control is only available on macOS"
        if self.error and not self.title and not self.artist:
            return self.error
        title = (self.title or "").strip()
        artist = (self.artist or "").strip()
        if title and artist:
            return f"{title} — {artist}"
        if title:
            return title
        if artist:
            return artist
        if self.is_playing:
            return "Playing"
        return "Nothing playing"


class MediaController(Protocol):
    def get_now_playing(self) -> NowPlayingInfo: ...

    def play(self) -> bool: ...

    def pause(self) -> bool: ...

    def toggle(self) -> bool: ...


OsascriptRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _default_osascript_runner(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=_OSASCRIPT_TIMEOUT_SEC,
        check=False,
    )


class UnsupportedMediaController:
    """Stub for platforms without system Now Playing control."""

    _MESSAGE = "System media control is only available on macOS"

    def get_now_playing(self) -> NowPlayingInfo:
        return NowPlayingInfo(supported=False, error=self._MESSAGE)

    def play(self) -> bool:
        return False

    def pause(self) -> bool:
        return False

    def toggle(self) -> bool:
        return False


class DarwinMediaController:
    """macOS Now Playing via osascript + private MediaRemote classes."""

    def __init__(
        self, runner: Optional[OsascriptRunner] = None
    ) -> None:
        self._runner = runner or _default_osascript_runner
        self._last: NowPlayingInfo = NowPlayingInfo()

    def get_now_playing(self) -> NowPlayingInfo:
        try:
            result = self._runner(
                ["/usr/bin/osascript", "-l", "JavaScript", "-e", _JXA_GET_NOW_PLAYING]
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            info = NowPlayingInfo(
                title=self._last.title,
                artist=self._last.artist,
                is_playing=False,
                error=str(exc),
            )
            self._last = info
            return info

        stdout = (result.stdout or "").strip()
        if result.returncode != 0 or not stdout:
            err = (result.stderr or "").strip() or f"osascript exit {result.returncode}"
            info = NowPlayingInfo(
                title=self._last.title,
                artist=self._last.artist,
                is_playing=False,
                error=err,
            )
            self._last = info
            return info

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            info = NowPlayingInfo(
                title=self._last.title,
                artist=self._last.artist,
                is_playing=False,
                error="invalid now-playing JSON",
            )
            self._last = info
            return info

        info = NowPlayingInfo(
            title=str(payload.get("title") or ""),
            artist=str(payload.get("artist") or ""),
            is_playing=bool(payload.get("isPlaying")),
            error=str(payload.get("error") or ""),
        )
        self._last = info
        return info

    def play(self) -> bool:
        return self._send_command(_CMD_PLAY)

    def pause(self) -> bool:
        return self._send_command(_CMD_PAUSE)

    def toggle(self) -> bool:
        return self._send_command(_CMD_TOGGLE)

    def _send_command(self, command: int) -> bool:
        try:
            result = self._runner(
                [
                    "/usr/bin/osascript",
                    "-l",
                    "JavaScript",
                    "-e",
                    _JXA_SEND_COMMAND,
                    str(command),
                ]
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        out = (result.stdout or "").strip()
        return out == "ok" or not out.startswith("error:")


def create_media_controller(
    *,
    system: Optional[str] = None,
    runner: Optional[OsascriptRunner] = None,
) -> MediaController:
    name = (system or platform.system()).lower()
    if name == "darwin":
        return DarwinMediaController(runner=runner)
    return UnsupportedMediaController()
