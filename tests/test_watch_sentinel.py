# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_TESTS_DIR = Path(__file__).resolve().parent
_ROOT = _TESTS_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from vendor_paths import resolve_vendor_dir  # noqa: E402

_vendor = resolve_vendor_dir(_ROOT)
if _vendor is not None and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# watch_sentinel is a standalone script (not an Anki package module).
import watch_sentinel  # noqa: E402
import watch_state as ws  # noqa: E402

_DEAD_PID = 9_999_999


class _FakeMedia:
    def __init__(self, *, playing: bool = False, supported: bool = True) -> None:
        self.supported = supported
        self.is_playing = playing
        self.calls = 0

    def get_now_playing(self) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(
            supported=self.supported,
            is_playing=self.is_playing,
            title="T",
            artist="A",
            error="unsupported" if not self.supported else "",
            display_label=lambda: "T — A",
        )


def _engine(tmp_path: Path, media: _FakeMedia) -> watch_sentinel.SentinelEngine:
    return watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
    )


def _seed_watch_state(tmp_path: Path, **overrides) -> Path:
    path = tmp_path / ws.STATE_FILENAME
    state = ws.default_state()
    state.update({"budget_seconds": 60, "pid": 0, "anki_pid": 0})
    state.update(overrides)
    ws.write_state(path, state)
    return path


def test_edge_triggered_first_observation_does_not_spawn(tmp_path) -> None:
    """Playback already underway at login is a baseline, not a trigger."""
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=True)
    engine = _engine(tmp_path, media)
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        assert spawn.call_count == 0


def test_spawns_when_playback_starts(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = _engine(tmp_path, media)
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False  # baseline: not playing
        assert spawn.call_count == 0
        media.is_playing = True
        assert engine.tick() is False
        assert spawn.call_count == 1
        # Still playing on the next tick — no second helper.
        assert engine.tick() is False
        assert spawn.call_count == 1


def test_does_not_spawn_while_helper_alive(tmp_path) -> None:
    _seed_watch_state(tmp_path, pid=os.getpid())
    media = _FakeMedia(playing=False)
    engine = _engine(tmp_path, media)
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        media.is_playing = True
        assert engine.tick() is False
        assert spawn.call_count == 0
    # The media backend is never queried while a guardian is up.
    assert media.calls == 0


def test_does_not_spawn_while_anki_alive(tmp_path) -> None:
    """Anki owns the helper lifecycle; racing it would double-drain."""
    _seed_watch_state(tmp_path, anki_pid=os.getpid(), anki_alive=True)
    media = _FakeMedia(playing=True)
    engine = _engine(tmp_path, media)
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        assert spawn.call_count == 0


def test_spawns_after_quit_even_while_anki_alive(tmp_path) -> None:
    """Regression: quitting the helper's tray icon while Anki stays open must
    not orphan enforcement. watch_daemon's own poll loop refuses to reattach
    once ``exit`` is set (that's what makes "Quit" stick), so the sentinel
    has to take over reviving playback even though anki_pid is alive."""
    _seed_watch_state(tmp_path, anki_pid=os.getpid(), anki_alive=True)
    media = _FakeMedia(playing=False)
    engine = _engine(tmp_path, media)
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False  # baseline while helper still up
        # User clicks "Quit Anki Media Timer": helper writes exit and dies.
        _seed_watch_state(
            tmp_path, anki_pid=os.getpid(), anki_alive=True, pid=0, exit=True
        )
        assert engine.tick() is False
        assert spawn.call_count == 0  # no new playback yet
        media.is_playing = True
        assert engine.tick() is False
        assert spawn.call_count == 1


def test_defers_to_anki_when_not_exited(tmp_path) -> None:
    """Without an explicit quit, Anki's own on_poll reattach owns crash
    recovery — the sentinel must not race it."""
    _seed_watch_state(tmp_path, anki_pid=os.getpid(), anki_alive=True)
    media = _FakeMedia(playing=True)
    engine = _engine(tmp_path, media)
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        assert spawn.call_count == 0
    assert media.calls == 0


def test_stale_anki_pid_does_not_block(tmp_path) -> None:
    _seed_watch_state(tmp_path, anki_pid=_DEAD_PID, anki_alive=True)
    media = _FakeMedia(playing=False)
    engine = _engine(tmp_path, media)
    with patch.object(watch_sentinel, "pid_is_alive", return_value=False):
        with patch.object(engine, "_spawn_helper") as spawn:
            assert engine.tick() is False
            media.is_playing = True
            assert engine.tick() is False
            assert spawn.call_count == 1


def test_baseline_resets_while_guarded(tmp_path) -> None:
    """Quitting the helper mid-track must not trigger an instant respawn."""
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = _engine(tmp_path, media)
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False  # baseline: not playing
        media.is_playing = True
        # A helper takes over (e.g. Anki opened) ...
        _seed_watch_state(tmp_path, pid=os.getpid())
        assert engine.tick() is False
        # ... then the user quits it while the same track keeps playing.
        _seed_watch_state(tmp_path, pid=_DEAD_PID)
        with patch.object(watch_sentinel, "pid_is_alive", return_value=False):
            assert engine.tick() is False
            assert spawn.call_count == 0
            # Only a fresh start counts.
            media.is_playing = False
            assert engine.tick() is False
            media.is_playing = True
            assert engine.tick() is False
            assert spawn.call_count == 1


def test_missing_state_file_is_left_alone(tmp_path) -> None:
    """No seeded prefs means no budget: spawning would lock out all media."""
    media = _FakeMedia(playing=True)
    engine = _engine(tmp_path, media)
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        assert engine.tick() is False
        assert spawn.call_count == 0
    assert media.calls == 0


def test_exit_flag_quits(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    engine = _engine(tmp_path, _FakeMedia())
    ws.request_sentinel_exit(tmp_path / ws.SENTINEL_STATE_FILENAME)
    assert engine.tick() is True


def test_newer_sentinel_wins(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    engine = _engine(tmp_path, _FakeMedia())
    ws.write_sentinel_state(
        tmp_path / ws.SENTINEL_STATE_FILENAME, {"pid": os.getppid()}
    )
    with patch.object(watch_sentinel, "pid_is_alive", return_value=True):
        assert engine.tick() is True


def test_own_claim_is_not_mistaken_for_a_rival(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    engine = _engine(tmp_path, _FakeMedia())
    engine.claim()
    state = ws.read_sentinel_state(tmp_path / ws.SENTINEL_STATE_FILENAME)
    assert state["pid"] == os.getpid()
    assert state["exit"] is False
    assert engine.tick() is False


def test_unsupported_media_quits(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    engine = _engine(tmp_path, _FakeMedia(supported=False))
    assert engine.tick() is True


def test_poll_interval_follows_state(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    engine = _engine(tmp_path, _FakeMedia())
    ws.write_sentinel_state(
        tmp_path / ws.SENTINEL_STATE_FILENAME, {"poll_ms": 9000}
    )
    engine.tick()
    assert engine.poll_seconds == 9.0


def test_release_only_clears_own_claim(tmp_path) -> None:
    path = tmp_path / ws.SENTINEL_STATE_FILENAME
    engine = _engine(tmp_path, _FakeMedia())
    engine.claim()
    engine.release()
    assert ws.read_sentinel_state(path)["pid"] == 0

    ws.write_sentinel_state(path, {"pid": 4321})
    engine.release()
    assert ws.read_sentinel_state(path)["pid"] == 4321


def test_run_loops_until_exit(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    engine = _engine(tmp_path, _FakeMedia())
    ticks = {"n": 0}

    def fake_tick() -> bool:
        ticks["n"] += 1
        return ticks["n"] >= 3

    sleeps: list[float] = []
    with patch.object(engine, "tick", side_effect=fake_tick):
        watch_sentinel.run(engine, sleep=sleeps.append)
    assert ticks["n"] == 3
    assert sleeps == [1.0, 1.0]


def test_run_survives_a_failing_tick(tmp_path) -> None:
    engine = _engine(tmp_path, _FakeMedia())
    results = [OSError("boom"), False, True]

    def fake_tick():
        value = results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    with patch.object(engine, "tick", side_effect=fake_tick):
        watch_sentinel.run(engine, sleep=lambda _seconds: None)
    assert results == []


def test_reconcile_child_stands_down_for_the_winner(tmp_path) -> None:
    engine = _engine(tmp_path, _FakeMedia())
    child = SimpleNamespace(pid=1234, poll=lambda: None)
    engine._child = child
    terminated: list[bool] = []
    with patch.object(engine, "_terminate_child", side_effect=lambda: terminated.append(True)):
        with patch.object(watch_sentinel, "pid_is_alive", return_value=True):
            engine._reconcile_child({"pid": 5678})
    assert terminated == [True]


def test_reconcile_child_keeps_our_own_helper(tmp_path) -> None:
    engine = _engine(tmp_path, _FakeMedia())
    engine._child = SimpleNamespace(pid=1234, poll=lambda: None)
    with patch.object(engine, "_terminate_child", side_effect=AssertionError):
        with patch.object(watch_sentinel, "pid_is_alive", return_value=True):
            engine._reconcile_child({"pid": 1234})
    assert engine._child is not None


def test_reconcile_child_drops_dead_child(tmp_path) -> None:
    engine = _engine(tmp_path, _FakeMedia())
    engine._child = SimpleNamespace(pid=1234, poll=lambda: 0)
    engine._reconcile_child({"pid": 0})
    assert engine._child is None


def test_default_sentinel_state_path_sits_beside_watch_state() -> None:
    resolved = watch_sentinel._default_sentinel_state_path(
        Path("/profile") / ws.STATE_FILENAME
    )
    assert resolved == Path("/profile") / ws.SENTINEL_STATE_FILENAME


def test_spawn_helper_launches_a_real_detached_process(tmp_path) -> None:
    """Exercise argv/env/detach for real, with a stand-in for watch_helper."""
    fake_root = tmp_path / "addon"
    fake_root.mkdir()
    marker = tmp_path / "helper_ran.txt"
    (fake_root / "watch_helper.py").write_text(
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        f"open({str(marker)!r}, 'w').write(\n"
        "    '%s\\n%s\\n%s' % (os.getpid(), ' '.join(args), os.environ.get('PYTHONPATH', ''))\n"
        ")\n",
        encoding="utf-8",
    )
    state = _seed_watch_state(tmp_path)
    engine = _engine(tmp_path, _FakeMedia())
    with patch.object(watch_sentinel, "_ROOT", fake_root):
        engine._spawn_helper()
        assert engine._child is not None
        engine._child.wait(timeout=30)

    assert marker.is_file()
    pid_line, args_line, pythonpath = marker.read_text(encoding="utf-8").split("\n")
    assert int(pid_line) != os.getpid()  # separate process, not a thread
    assert args_line == f"--state {state}"
    assert str(fake_root) in pythonpath


def test_spawn_helper_survives_a_missing_helper(tmp_path) -> None:
    engine = _engine(tmp_path, _FakeMedia())
    with patch.object(watch_sentinel, "_ROOT", tmp_path / "nope"):
        engine._spawn_helper()
    assert engine._child is None


def test_spawn_on_start_opens_immediately_without_waiting_for_playback(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        spawn_on_start=True,
    )
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        assert spawn.call_count == 1  # opened immediately, no playback needed


def test_spawn_on_start_only_fires_once(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        spawn_on_start=True,
    )
    with patch.object(engine, "_spawn_helper") as spawn:
        engine.tick()
        engine.tick()
        engine.tick()
        assert spawn.call_count == 1


def test_spawn_on_start_yields_to_an_existing_guardian(tmp_path) -> None:
    """If something's already open (e.g. Anki beat it to the punch), the
    login item should not force a duplicate."""
    _seed_watch_state(tmp_path, pid=os.getpid())
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        spawn_on_start=True,
    )
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        assert spawn.call_count == 0


def test_run_exits_after_oneshot_attempt(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=_FakeMedia(playing=False),
        poll_ms=1000,
        spawn_on_start=True,
        oneshot=True,
    )
    sleeps: list[float] = []
    with patch.object(engine, "_spawn_helper") as spawn:
        watch_sentinel.run(engine, sleep=sleeps.append)
    assert spawn.call_count == 1
    assert sleeps == []  # never slept: exited right after the first tick


def test_run_keeps_watching_when_not_oneshot(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=_FakeMedia(playing=False),
        poll_ms=1000,
        spawn_on_start=True,
        oneshot=False,
    )
    calls = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            ws.request_sentinel_exit(tmp_path / ws.SENTINEL_STATE_FILENAME)

    with patch.object(engine, "_spawn_helper"):
        watch_sentinel.run(engine, sleep=fake_sleep)
    assert calls["n"] == 2  # kept polling instead of exiting after tick 1


def _struct_time(hour: int, minute: int) -> SimpleNamespace:
    return SimpleNamespace(tm_hour=hour, tm_min=minute)


def test_on_hours_none_by_default_never_restricts(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = _engine(tmp_path, media)
    assert engine._in_on_hours() is True


def test_spawn_suppressed_outside_on_hours(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        on_hours_start="09:00",
        on_hours_end="21:00",
        now_provider=lambda: _struct_time(23, 0),
    )
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False  # baseline: not playing
        media.is_playing = True
        assert engine.tick() is False
        assert spawn.call_count == 0  # outside the window: no revival


def test_spawn_allowed_inside_on_hours(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        on_hours_start="09:00",
        on_hours_end="21:00",
        now_provider=lambda: _struct_time(12, 0),
    )
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        media.is_playing = True
        assert engine.tick() is False
        assert spawn.call_count == 1


def test_quit_stays_quit_outside_on_hours_even_as_playback_toggles(tmp_path) -> None:
    """The scenario the feature exists for: outside the window, the user can
    quit the timer and playback restarting later must not bring it back."""
    _seed_watch_state(tmp_path, pid=os.getpid())
    media = _FakeMedia(playing=True)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        on_hours_start="09:00",
        on_hours_end="21:00",
        now_provider=lambda: _struct_time(23, 0),
    )
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False  # baseline: helper alive, guarded
        # User quits the helper's tray icon.
        _seed_watch_state(tmp_path, pid=0, exit=True)
        assert engine.tick() is False
        assert spawn.call_count == 0
        # Track stops and a new one starts — an edge trigger inside hours
        # would normally revive it, but we're outside the window.
        media.is_playing = False
        assert engine.tick() is False
        media.is_playing = True
        assert engine.tick() is False
        assert spawn.call_count == 0


def test_on_hours_wraps_past_midnight(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        on_hours_start="22:00",
        on_hours_end="06:00",
        now_provider=lambda: _struct_time(23, 30),
    )
    assert engine._in_on_hours() is True


def test_on_hours_spawn_on_start_also_gated(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        spawn_on_start=True,
        on_hours_start="09:00",
        on_hours_end="21:00",
        now_provider=lambda: _struct_time(2, 0),
    )
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        assert spawn.call_count == 0


def _struct_time_on(hour: int, minute: int, wday: int) -> SimpleNamespace:
    return SimpleNamespace(tm_hour=hour, tm_min=minute, tm_wday=wday)


def test_on_days_none_does_not_restrict_within_on_hours(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        on_hours_start="09:00",
        on_hours_end="21:00",
        now_provider=lambda: _struct_time_on(12, 0, 6),  # Sunday
    )
    assert engine._in_on_hours() is True


def test_spawn_suppressed_on_an_unchecked_day(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        on_hours_start="09:00",
        on_hours_end="21:00",
        on_days=("mon", "tue", "wed", "thu", "fri"),
        now_provider=lambda: _struct_time_on(12, 0, 5),  # Saturday
    )
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        media.is_playing = True
        assert engine.tick() is False
        assert spawn.call_count == 0


def test_spawn_allowed_on_a_checked_day(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        on_hours_start="09:00",
        on_hours_end="21:00",
        on_days=("mon", "tue", "wed", "thu", "fri"),
        now_provider=lambda: _struct_time_on(12, 0, 2),  # Wednesday
    )
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        media.is_playing = True
        assert engine.tick() is False
        assert spawn.call_count == 1


def test_empty_on_days_selection_blocks_every_day(tmp_path) -> None:
    """Unchecking every day is a valid way to disable revival entirely
    without turning "Persist in background" off."""
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        on_hours_start="00:00",
        on_hours_end="00:00",  # equal bounds: whole day, if any day were on
        on_days=(),
        now_provider=lambda: _struct_time_on(12, 0, 2),
    )
    with patch.object(engine, "_spawn_helper") as spawn:
        assert engine.tick() is False
        media.is_playing = True
        assert engine.tick() is False
        assert spawn.call_count == 0


def test_on_days_ignored_when_on_hours_itself_is_unset(tmp_path) -> None:
    """on_days without on_hours has nothing to attach to — it must not
    restrict spawning on its own."""
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        on_days=(),  # would block every day, but on_hours was never set
        now_provider=lambda: _struct_time_on(12, 0, 5),
    )
    assert engine._in_on_hours() is True


def test_invalid_on_hours_values_disable_the_restriction(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    media = _FakeMedia(playing=False)
    engine = watch_sentinel.SentinelEngine(
        state_path=tmp_path / ws.STATE_FILENAME,
        sentinel_state_path=tmp_path / ws.SENTINEL_STATE_FILENAME,
        media=media,
        poll_ms=1000,
        on_hours_start="nonsense",
        on_hours_end="21:00",
    )
    assert engine._in_on_hours() is True


def test_spawn_helper_uses_the_named_launcher(tmp_path) -> None:
    _seed_watch_state(tmp_path)
    engine = _engine(tmp_path, _FakeMedia())
    with patch.object(
        watch_sentinel.autostart, "ensure_named_launcher", return_value="/named/AnkiMediaTimer"
    ) as ensure_named:
        with patch.object(watch_sentinel.subprocess, "Popen") as popen:
            popen.return_value = SimpleNamespace(pid=999)
            engine._spawn_helper()
    assert ensure_named.call_args.kwargs["name"] == watch_sentinel.autostart.HELPER_PROCESS_NAME
    assert popen.call_args.kwargs["args"][0] == "/named/AnkiMediaTimer"
