# AnkiTube configuration

AnkiTube stores user preferences and runtime session state in the add-on config JSON.

## User preferences

These keys can be changed in **Tools → AnkiTube → Settings...** or edited here:

| Key | Default | Description |
|-----|---------|-------------|
| `seconds_per_card` | `15` | Watch seconds earned per reviewed card (also one cube) |
| `starting_budget_seconds` | `0` | Initial watch budget for new profiles |
| `max_budget_seconds` | `600` | Maximum banked watch time (10 minutes) |
| `dock_area` | `"right"` | Dock side: `"left"` or `"right"` |
| `show_dock_in_review_only` | `false` | Hide the dock outside review mode |
| `media_mode` | `"system"` | `"system"` (macOS Now Playing / Windows SMTC) or `"youtube"` (legacy embedded player) |
| `auto_resume_on_budget` | `false` | Auto-resume media when budget is restored after exhaustion |
| `show_budget_cubes` | `true` | Show falling budget cubes over the Anki window |
| `cube_bounds_left_pct` | `0` | Left edge of cube drop range as % of window width (0–100) |
| `cube_bounds_right_pct` | `100` | Right edge of cube drop range as % of window width (0–100) |
| `show_overlay_timer` | `true` | Show the **Watch:** countdown in the top-left of the Anki window |
| `system_media_poll_ms` | `500` | How often to poll system media status (200–5000 ms) |
| `youtube_show_controls` | `true` | Show YouTube player controls (legacy mode) |
| `youtube_show_fullscreen` | `true` | Show YouTube fullscreen button (legacy mode) |
| `dock_show_playback_buttons` | `true` | Show Play/Pause (and legacy Next/Fullscreen) controls |
| `show_menubar_watch_time` | `true` | Show the Anki Media Timer icon (countdown) in the macOS menu bar or Windows system tray |
| `quit_with_anki` | `true` | Quit Anki Media Timer when Anki quits (`false` keeps pause enforcement after Anki closes) |
| `debug_logging` | `false` | Write diagnostic log to `ankittube.log` |

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
