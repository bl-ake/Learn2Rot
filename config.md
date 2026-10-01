# Learn2Rot configuration

Learn2Rot stores user preferences and runtime session state in the add-on config JSON.

Two files in your Anki profile folder are shared with the background processes:
`learn2rot_watch_state.json` (budget, prefs and helper PID) and
`learn2rot_sentinel_state.json` (login watcher PID and poll interval). The login
watcher appends what it does to `learn2rot_sentinel.log` in the same folder.

## User preferences

These keys can be changed in **Tools → Learn2Rot → Settings...** or edited here:

| Key | Default | Description |
|-----|---------|-------------|
| `seconds_per_card` | `15` | Watch seconds earned per reviewed card (also one cube) |
| `starting_budget_seconds` | `0` | Initial watch budget for new profiles |
| `max_budget_seconds` | `0` | Maximum banked watch time (`0` = unlimited) |
| `dock_area` | `"right"` | Dock side: `"left"` or `"right"` |
                        ·| `show_dock_in_review_only` | `false` | Hide the dock outside review mode |
| `media_mode` | `"system"` | `"system"` (macOS Now Playing / Windows SMTC) or `"youtube"` (legacy embedded player) |
| `auto_resume_on_budget` | `false` | Auto-resume media when budget is restored after exhaustion |
| `show_budget_cubes` | `false` | Show falling budget cubes over the Anki window |
| `cube_bounds_left_pct` | `0` | Left edge of cube drop range as % of window width (0–100) |
| `cube_bounds_right_pct` | `100` | Right edge of cube drop range as % of window width (0–100) |
| `show_overlay_timer` | `true` | Show the **Watch:** countdown in the top-left of the Anki window |
| `system_media_poll_ms` | `500` | How often to poll system media status (200–5000 ms) |
| `youtube_show_controls` | `true` | Show YouTube player controls (legacy mode) |·
| `youtube_show_fullscreen` | `true` | Show YouTube fullscreen button (legacy mode) |
| `dock_show_playback_buttons` | `true` | Show Play/Pause (and legacy Next/Fullscreen) controls |
| `show_menubar_watch_time` | `true` | Show the Anki Media Timer icon (countdown) in the macOS menu bar or Windows system tray |
| `quit_with_anki` | `true` | Quit Anki Media Timer when Anki quits (`false` ··keeps pause enforcement after Anki closes) |
| `require_cards_due` | `false` | If budget runs out while no cards are due, back off instead of locking (no pausing, icon hidden) rather than pausing normally; resumes normal lockout-at-zero once cards are due again. Banked time still counts down as usual before it hits zero. Requires Anki to be running to check — see below |
| `open_timer_at_login` | `false` | Open Anki Media Timer immediately when you log in, instead of waiting for media to start. Forced `true` whenever `sentinel_at_login` is `true` |
| `sentinel_at_login` | `false` | Keep the login watcher running instead of exiting after login; it (re)opens Anki Media Timer whenever media starts, even after you quit it or if Anki was never opened |
| `sentinel_poll_ms` | `3000` | How often the login watcher checks for playback (1000–60000 ms) |
| `sentinel_on_hours_enabled` | `false` | Outside the daily window below (or on an unchecked day), a running Anki Media Timer backs off (no pausing, icon hidden, budget untouched — same as `require_cards_due` backing off) and resumes on its own once the window reopens; also gates revival via `sentinel_at_login`, so quitting the timer outside the window stays quit instead of coming back the next time media plays |
| `sentinel_on_hours_start` | `"09:00"` | Local time (`HH:MM`, 24h) the on-hours window opens |
| `sentinel_on_hours_end` | `"21:00"` | Local time (`HH:MM`, 24h) the on-hours window closes; a value `<=` start spans midnight |
| `sentinel_on_days` | every day | Weekdays (`"mon"`–`"sun"`) the on-hours window applies on. Day checks use the calendar day the moment falls on, so an overnight window can cut off at midnight if the next day isn't also listed. An empty list backs off/disables revival on every day without disabling `sentinel_on_hours_enabled` or `sentinel_at_login` themselves |
| `debug_logging` | `false` | Write diagnostic log to `learn2rot.log` |

## Runtime state (auto-managed)

| Key | Description |
|-----|-------------|
| `budget_seconds` | Current watch-time balance |
| `queue` | Saved video queue (legacy YouTube mode) |
| `current_index` | Index of the current queue item |
| `positions` | Playback positions per video ID |
| `lifetime_earned_seconds` | Total seconds earned from reviews |
| `dock_panel_sizes` | Splitter sizes for queue vs player |
| `queue_visible` | Whether the queue list is visible |
| `dock_visible` | Whether the dock panel is open |
| `dock_width` | Saved dock width in pixels |

`cards_due` and `cards_due_at` in `learn2rot_watch_state.json` are runtime state too, but live outside `config.json`: they're pushed by Anki (only Anki can query the collection) and read by the standalone helper, so `require_cards_due` above can gate on them without either process needing the other running. `cards_due_at` is Anki's prediction of when the next card becomes due (day rollover, or a learning card's timer) — while Anki is closed, the helper trusts a recorded `cards_due: false` only until `cards_due_at` passes, rather than indefinitely.
