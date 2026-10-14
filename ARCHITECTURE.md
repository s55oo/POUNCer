# POUNCer — Architecture & Developer Guide

POUNCer is a small, **standard-library-only** Windows desktop tool that follows
your N1MM Logger+ radio frequency and shows live Reverse Beacon Network (RBN)
spots for the band you are on — filtered to a chosen skimmer (default
`S53WW`) and to calls you still need. Clicking/tapping a spot QSYs the radio in
N1MM and types the callsign into the Entry Window **without transmitting**.

- Runtime: Python 3.10+ (developed on 3.14), `tkinter` GUI, no third-party deps.
- Target: Windows (uses `ctypes` for window focus + `SendInput` keystrokes).
- Distributed as a single `dist\POUNCer.exe` (PyInstaller, `--onefile --noconsole`).

---

## 1. Feature summary

| Area | Behaviour |
| --- | --- |
| Spot source | Telnet to `telnet.reversebeacon.net:7000` (CW/RTTY) and/or `:7001` (FT8/FT4), logging in with your callsign. |
| Spot filter | Keep only spots whose spotting node name starts with `[rbn] spotter` (e.g. `S53WW`), and only modes in `follow_modes`. |
| Band following | Reads N1MM `<RadioInfo>` UDP broadcasts (port 12060); shows only spots on the band N1MM is currently on. |
| Action on click/tap | `radio_setfrequency` to N1MM (UDP 13064) + simulated keystrokes to enter the call. Never sends a TX command. |
| Worked filter | Reads N1MM's open SQLite log (`.s3db`) and hides calls already worked on the same band + mode (current contest only). |
| TX / log buttons | Optional `[ui] fkeys` buttons (main window, phone page): F1-F12 transmit the N1MM message; `Enter` logs the QSO. |
| Views | Main sortable table and a **phone view** (local web server), also opened in the PC's browser as the touch view (**Open in browser**). |
| Sorting | Click column headings; default SNR descending (strongest first). |

---

## 2. Data flow

```
                        ┌───────────────────────────────────────────────┐
   RBN telnet feed      │                    POUNCerApp (Tk thread)     │
   :7000 / :7001        │                                               │
        │               │   spot_queue ─► _drain_spots ─► self.spots{}  │
        ▼               │                                  │            │
 ┌─────────────┐        │                                  ▼            │
 │ RBNClient   │        │            _visible_spots()  (band+mode+worked)│
 │ (thread)    │        │                     │                         │
 │ parse_spot  │        │        ┌────────────┼─────────────┐           │
 └─────────────┘        │        ▼            ▼             ▼           │
                        │   Tree table                 Web payload      │
                        │                                  │            │
                        │                                  ▼            │
                        │                             WebServer (thread) │
                        │                                  ▲            │
                        │   _act_on_spot ◄── _drain_web ───┘ web_queue  │
                        └───────┬───────────────────────────────────────┘
                                │ QSY (UDP 13064) + SendInput keystrokes
                                ▼
                        ┌───────────────┐        ┌────────────────────┐
                        │ N1MM Logger+  │───────►│ RadioInfo UDP      │
                        │  (Entry Wnd)  │ 12060  │ ─► RadioListener   │
                        └───────────────┘        │    (thread)        │
                                                 └────────────────────┘
```

All spot selection funnels through one place — `PouncerApp._visible_spots()` —
so the table and the phone view always show the same set.

---

## 3. Threading model

The GUI is single-threaded (Tk). Background work never touches Tk directly; it
communicates through thread-safe `queue.Queue`s drained by the 500 ms tick.

| Thread | Owner | Purpose | Hands off to GUI via |
| --- | --- | --- | --- |
| Main / Tk | `PouncerApp` | All UI, `_tick` every `REFRESH_MS` (500 ms) | — |
| `n1mm-rx` | `RadioListener` | Receive `RadioInfo` UDP → `RadioState` | `RadioState` (locked snapshot) |
| `pouncer-rbn` | `RBNClient` (one per port) | Read RBN telnet → queue | `spot_queue` |
| `pouncer-web` | `WebServer` | Serve HTTP | `_web_cache` (read) / `_web_queue` (write) |

`_tick()` order each 500 ms: drain status → drain spots → ensure RBN clients →
`worked.maybe_reload()` → prune → update labels → rebuild table if dirty →
drain web actions → refresh the cached web payload.

- **Web → GUI:** the HTTP handler pushes a tagged tuple onto `_web_queue`:
  `("qsy", call, freq, mode)`, `("key", key)` or `("reconnect",)`; the tick pops it and calls
  `_act_on_spot` / `_press_key`. This is why the web handler never runs Tk code.
- **Web ← GUI:** the tick builds the JSON payload on the Tk thread and caches it;
  the handler just returns the cached dict (no Tk access from the HTTP thread).

---

## 4. Module reference

| File | Responsibility |
| --- | --- |
| `pouncer/__init__.py` | Package metadata (`__version__`). |
| `pouncer/__main__.py` | `argparse` CLI (`--config`, `--verbose`), logging setup, launches `PouncerApp`. |
| `pouncer_main.py` | Top-level launcher used as the PyInstaller entry point (keeps package context so relative imports work when frozen). |
| `pouncer/bands.py` | Band plan `BANDS` and conversions: `band_from_khz`, `tens_of_hz_to_khz`, `khz_to_tens_of_hz`. |
| `pouncer/config.py` | `Config` dataclass, `load_config`, `parse_modes`. |
| `pouncer/rbn.py` | `TelnetFilter`, `Spot`, `parse_spot`, `ports_for_modes`, `MODES`, `RBNClient` (thread, auto-reconnect w/ backoff). |
| `pouncer/n1mm.py` | `parse_radio_info`, `RadioState`, `RadioListener` (UDP rx), `N1MMController` (UDP tx: QSY only). |
| `pouncer/cty.py` | `CtyDB` (CTY country file → continent/country), `load_cty` (`[rbn] cty` or N1MM's `SupportFiles\wl_cty.dat`). |
| `pouncer/worked.py` | `WorkedLog` (worked-call set from the active `.s3db`, plus log-open state and `request_recheck`), `inspect_active`, `read_active`, `find_active_db`, `band_label`, `normalize_mode`. |
| `pouncer/win32.py` | `force_foreground`, `wipe_callsign` (Alt+W), `clear_focused_edit` (after N1MM `{WIPE}` via `send_radio_command` + Alt+W; WM_SETTEXT "" on the focused EDIT of the Entry Window's thread - after Alt+W the callsign box), `type_text` (unicode `SendInput`), `send_fkey` (F1-F12 - transmits in N1MM), `send_enter` (logs the QSO), `is_n1mm_running` (process scan). |
| `pouncer/web.py` | `WebServer` + mobile page + `/api/spots`, `/api/qsy`; `local_ipv4s`. Page-side test options (localStorage, set on `/help`): `pouncer.freqfrom` - `sorted(spots, radioKhz)` orders by frequency from the radio up, wrapping; `pouncer.calm` - `arrange()` keeps `calmOrder` (key call + MHz) between full re-sorts (every 15 s, not within 3 s of `lastTouch`; at once when sort / toggles / band / radio change), appends new keys (`.fresh`), and `refresh()` skips redraws within 3 s of a touch. SO2R QSY: radio A via UDP `radio_setfrequency`; radio B (`_types_qsy`, `[n1mm] radio_b_qsy = type`) by `_qsy` typing the kHz + Enter into the focused Entry Window B (UDP radio 2 moved A). After the QSY `_wait_for_qsy` polls `RadioState` until the radio reports the spot frequency (±0.15 kHz, max `qsy_wait_ms`) + `qsy_settle_ms`, then wipe / type; `_check_call` reads the focused box (`win32.focused_edit_text`, WM_GETTEXT) and retypes once if N1MM cleared it. After a tap / tune for the other radio `_return_focus` puts the cursor back: `win32.focus_snapshot` (foreground window, `GetGUIThreadInfo` focus control, `EM_GETSEL`) before, `restore_focus` (`SetForegroundWindow`, `AttachThreadInput` + `SetFocus`, `EM_SETSEL`) after - only if it was another window of N1MM's process (`[n1mm] return_focus`). SO2R header: payload `freqs` {radio: kHz} → A over B; `rbands` {radio: band} → band menu with a row per radio. Band menu = view only: `views` {radio: {band, from}} in localStorage `pouncer.view`; `refresh()` sends `band=`; `_web_spots(radio, band)` marks `_web_views[(radio, band)]` and returns `_web_view_cache` (built in `_tick` via `_build_web_payload(radio, view_band)`, `pending` until then); payload `radio_band` / `view`; the view is dropped when the radio's band differs from `from`. |
| `pouncer/app.py` | `PouncerApp`: GUI, filters, tick loop, table, web wiring, **Open in browser** (`_open_browser`), `_act_on_spot`. |
| `pouncer/runhist.py` | `RunHistory`: background, disk-cached run/S&P share per call from the public contest-log archive (`SH6/logs_XX.sqlite` shard, XX = crc32(CALL)&0xff → submitted logs of the last 6 years (`YEARS_BACK`), same contest+mode, else same mode any contest, up to `MAX_LOGS` = 6, downloaded in parallel (reconstructed only if none, flagged `rec`) from raw.githubusercontent.com → `run_share` + `on_air_hours` (pauses > 30 min = off): QSO on the same kHz (±0.3) as one of the last 2 on that band). `get()` never blocks (None until known); `WORKERS = 8` threads, one download per shard (per-shard lock). Page: ⚡ toggle = sort by `run` ascending, unknown last. |
| `server/warmlist.py` | Warm-up list builder (run once; `.github/workflows/build-warmup.yml`, manual), `--set cw` (CQWW/cw, ARRL/arrl_international_dx_cw_contest, CQWPX/cw → `warm.sqlite`) or `--set ssb` (CQWW/ph, ARRL/arrl_international_dx_phone_contest, CQWPX/ph, mode `PH` → `warm-ssb.sqlite`): partial clone + sparse checkout (+ `RECONSTRUCTED_LOGS/`) for the last N years; per (call, contest, mode) like `_lookup`: newest `MAX_LOGS` submitted logs else reconstructed, `MIN_QSOS` → `warm.sqlite` table `warm(call, contest, mode, run, hours, rec, logs, newest)` + `info`. Published as release assets `warmup/warm.sqlite` and `warmup/warm-ssb.sqlite`. POUNCer: `RunHistory.warm_up()` starts one background `_warm_one` per list (CW, SSB): loads `cache/warm.sqlite` / `cache/warm-ssb.sqlite`, downloads it if missing or > 30 days old (checks the SQLite header), never overwrites live results (`setdefault`); `_set_warm` → `warm_status` "CW n + SSB m stations known in advance (downloaded ...)" (+ "SSB list: ..." if one is missing); after a download writes `cache/warm-up.txt` / `warm-up-ssb.txt`. Keys `CALL|CONTEST|CW` vs `CALL|CONTEST|PH` keep CW and SSB apart (spot mode SSB/USB/LSB → PH). `run_share` skips band-only frequencies (`BAND_ONLY_KHZ`: logs without CAT). `[ui] warmup`. |
| `server/runlist.py` | Run list server (Docker, `server/README.md`): `build` = partial clone + sparse checkout of `<CONTEST>/{cw,ph,...}/<last N years>` of the archive, `run_share` per log → `runlist.sqlite` (call, contest, mode, year, run, qsos); `serve` = `/runs`, `/run`, `/status` on 8077. POUNCer's `RunHistory(run_list=...)` loads a local `runlist.sqlite` at start (newest year per call/contest/mode; `[ui] run_list`, default next to config.ini), and `RunHistory(server=...)` loads `/runs` once per (contest, mode); workers wait for it (≤15 s) before per-call GitHub lookups. |
| own call | `WorkedLog.station_call` = `SELECT Call FROM Station` of the open log (Change Your Station Data) → `PouncerApp.own_call()` (fallback `[rbn] callsign` / ini) → `home_cont` property (cty continent, re-evaluated when the call changes) and `_visible_spots` drops `same_station(dxcall, own)` (exact, or same longest part: S55OO/P, 9A/S55OO). |
| CQ time | `rbn.OnFreqTracker` (in `PouncerApp._on_freq`): per (call, band) [freq, first, last]; a spot within 0.5 kHz and 20 min of the last = same stay, else a new start; beacons ignored; `cq_min` in `/api/spots`. |
| backup skimmers / fade / alert | `rbn.SpotterFilter` (given to `RBNClient` instead of the prefix): primary prefix always; after `quiet_s` without a primary spot also `backups` or skimmers whose continent (cty) = home continent; switches back on the next primary spot. `_visible_spots` keeps one spot per (call, band), newest. Payload `age` per spot + `fade_s` → `.stale`; page `checkAlerts()` beeps (WebAudio, unlocked on first tap) / vibrates once per call+band for ⚡ + mult ≥ 1, toggle in the band sheet (`pouncer.alerts`). |
| `pouncer/lights.py` | Connection lights for the Tk window: pure functions `rbn_light`, `n1mm_light`, `log_light`, `team_light`, `web_light`, `warm_light` → (`ok`/`wait`/`bad`/`off`, label); `COLORS`. App `_update_lights` (each tick, redraws only on change) puts a ● + label per light in a `FlowFrame` under the team bar. |
| `pouncer/update.py` | `Updater` (exe only, `sys.frozen`): `maybe_check()` from `_tick` (start + every 6 h) → thread: GET api.github.com …/releases/latest, `is_newer(tag, __version__)`, asset `POUNCer.exe`, `expected_sha256` (asset `digest`, else the hash in the notes; none = refuse), download, verify, `_install`: write `.new`, rename running exe → `.old` (Windows allows renaming a running exe), `.new` → exe (rolls back on error). `cleanup_old()` at start. Built macOS (non-Windows frozen) = `notify_only`: checks, state `available` (blue light, "vX available on GitHub"), never downloads. `restart()`: `_shutdown`, then Popen the exe with `PYINSTALLER_RESET_ENVIRONMENT=1`, detached, `os._exit`. Light: `lights.update_light`; app adds the **Restart -> vX** button when `ready`. |
| `pouncer/rbn.py` (history) | 2 s after login `RBNClient._ask_history` sends `sh/dx/100 spotter=<primary>-#` (AR-Cluster), and `sh/dx/<history>` if no history line came within 5 s; `parse_history` reads the SH/DX lines (`freq call dd-Mon-yyyy hhmmZ comment <spotter>`), `received` = the spot's own UTC time. Socket timeout 1 s for the timing. `[rbn] history`. |
| SSB (`rbn.py`, `app.py`) | `RBNClient(kind="cluster", modes=("SSB",))` to `[ssb] host:port`: `parse_cluster_spot` (human `DX de` lines, no mode column) + `guess_mode` (comment word, else `SSB_SEGMENTS`), `ContinentFilter` (spotter continent via cty). App `_ensure_ssb_client` (auto: a radio's N1MM mode USB/LSB, team master, 15 min hold), `_effective_modes(radio)` → `_visible_spots` / payload `modes`; `ssb_light`. |
| `pouncer/settings.py` | Settings page: `SETTINGS` (ini key → Config attr, kind, live), `current(cfg)`, `parse`, `set_ini_value` (rewrites one `key = value` line in its section, adds key/section if missing, keeps comments; atomic replace), `save`. App `_save_setting`, `_web_restart` → queue → `updater.restart`. Web `/settings`, `/api/settings`, `/api/settings/set`, `/api/restart`; edits only from 127.0.0.1 unless `[web] remote_settings`. `Config.config_path` = the ini read. |
| `pouncer/summary.py` | Contest summary. `TapLog` (cache/taps.jsonl, 14 days) gets `record(call, khz)` from `_act_on_spot`; `read_qsos(db, contest_nr, mult_types)` reads DXLOG TS/Call/Band/mults read-only; `build(qsos, taps, mult_types)` → QSOs from POUNCer (tap same call core + band, −60 s…+15 min), first-of-mult per band (WPX per contest), per band / per hour, best 60 min, on-time (gaps < 30 min). App `_summary` → `WebServer.set_summary_provider` → `/api/summary`; page `SUMMARY_HTML` at `/summary`. |
| `pouncer/team.py` | `Team`: UDP on 12079 - broadcast out of every adapter (a socket bound to each local IPv4, `local_ipv4s_all`, refreshed every 60 s) + unicast to known and `[team] peers` IPs; spots / rh unicast only; (`{t: hi|spot|rh, id, ...}` JSON), announce every 2 s, peers alive 6 s; `elect()` = earliest `started` among alive + healthy (master with RBN down > 60 s is unhealthy). App: slaves (and anyone in its first 3 s) run no RBN clients; the master's `_drain_spots` sends each own spot to the team; slaves feed received spots through `_team_queue`; `RunHistory.on_learn` → `send_runhist`, received → `merge`; a joining slave GETs the master's `/api/runhist` (`snapshot`). `hi` carries `ver`; `version_mismatch()` lists (ip, ver) of alive peers on another version → amber Team light, orange team bar, ⚠ on the phone (`_version_note`). |
| `pouncer/flow.py` | `flow_positions`: pure flow-layout geometry (items left to right, wrapping onto rows) for `app.FlowFrame`, which holds the main window's sort / option / key / action controls. |

---

## 5. N1MM Logger+ integration

**Setup (once, in N1MM):** `Config → Config Ports… → Broadcast Data` → tick
**Radio**, destination `127.0.0.1:12060`.

### Radio info (receive, UDP 12060)

`RadioListener` binds `listen_ip:listen_port` (default `0.0.0.0:12060`), parses
`<RadioInfo>` XML with `parse_radio_info`, and stores it in `RadioState`
(thread-safe, one entry per radio number).

Useful fields: `Freq` (tens of Hz → kHz via `/100`), `Mode`, `RadioNr`,
`ActiveRadioNr`, `EntryWindowHwnd` (used to focus the window). `RadioState.snapshot()`
returns the active radio's info plus derived `band` and `age`.

### Control (send, UDP 13064)

`N1MMController.set_frequency(khz)` sends:

```xml
<radio_setfrequency>
  <app>POUNCer</app>
  <radionr>1</radionr>
  <frequency>14015.500</frequency>
  <mousebutton>left</mousebutton>
</radio_setfrequency>
```

> **Transmit safety:** POUNCer never sends a transmit command. The original
> design used `<RCmd Cmd="KeyMap …">`, but the `RCmd` payload is *text to send in
> CW/digi*, so it keyed the callsign. That path was removed; callsign entry is
> now done with simulated keystrokes (`win32.type_text`), which matches how the
> TouchPortal N1MM plugin works. `_act_on_spot` only ever calls
> `set_frequency` + optional `wipe_callsign()` + `type_text()`.
> The only path that can transmit is the optional phone F-key buttons (§9.2),
> which are deliberately a separate, confirmed action.

---

### SO2R / SO2V: radio A / B

N1MM sends one `RadioInfo` per radio (`RadioNr` = the radio the packet is
about), each with that radio's own `EntryWindowHwnd` (Entry Window A / B).
`RadioState` keeps all of them; `snapshot(nr)`, `entry_window_hwnd(nr)`,
`active_radio_nr(nr)` pick a radio (falling back to the active one if N1MM
hasn't reported it - SO1R), `radio_numbers()` lists them. QSY:
`set_frequency(khz, radio_nr=n)` sends `<radionr>n</radionr>` and mouse
button `Right` for radio 2 (docs: "Left = Radio 1, Right = Radio 2" in
SO2V/SO2R). App: `self.radio` (main window, `[n1mm] default_radio`, A/B
button) and an optional `radio` on `_act_on_spot`, `_press_key`, `_tune`,
`_qsy_band`, `_current_band`, `_radio_khz`, `_visible_spots`; the phone
sends `?radio=1|2` with `/api/spots|qsy|key|band|tune` (`Handler._call`
adds `radio=` only when given) and `_web_cache` holds one payload per
radio (`radio`, `radios` in it).

## 6. Worked-call detection (`worked.py`)

N1MM stores each log as a SQLite `.s3db` in `Documents\N1MM Logger+`. **One file
can hold many contests**, distinguished by the `DXLOG.ContestNR` column. The
currently open contest is recorded in `N1MM Logger.ini` (`[Configurer]`) as
`DXLog Name=`, `Contest NR=`, `Contest Type=`.

`read_active()` reads that ini to find the active `.s3db` and contest number.
`WorkedLog` builds a set of `(call, band, mode)` from
`SELECT Call, Band, Mode FROM DXLOG WHERE ContestNR = <open>` and answers
`is_worked(call, freq_khz, mode)`. The same query also counts QSOs per band
(`qso_count`, `band_qsos()` in band-plan order; dupes included), shown in the
web status line (current band highlighted) and the main window's RBN line.
`is_worked(call, freq_khz, mode)`:
- band: log `Band` is MHz (e.g. `14.0`) → `band_label` → `20m`;
  spot freq kHz → `band_from_khz`.
- mode: phone modes collapse to `PHONE` (`normalize_mode`).

The log is opened read-only (`file:…?mode=ro`, `PRAGMA query_only=1`; falls back
to a normal connect if read-only fails).

### Is a log open? (`log_open` / `log_state` / `log_message`)

N1MM keeps `DXLog Name=` in the ini after it exits, so the ini alone can't
say whether a log is open. `WorkedLog` therefore also takes an
`n1mm_running` probe (the app passes `win32.is_n1mm_running`, a
`CreateToolhelp32Snapshot` scan for `N1MMLogger.net.exe`, compared without
spaces; `None` = can't tell, ignored). The app first checks RadioInfo: a
packet newer than 30 s (`RADIO_ALIVE_SECONDS`) means N1MM is running. States, checked in this order (auto-detect mode only; an
explicit `[worked] db` only checks that the file exists):

| `log_state` | When | Message shown |
| --- | --- | --- |
| `n1mm_not_running` | probe returns False | N1MM Logger+ is not running - start it and open a log |
| `no_ini` | no `N1MM Logger.ini` found | … set `[n1mm] ini` in config.ini |
| `no_log` | ini has no / empty `DXLog Name=` | No log open in N1MM - open one with File > Open Log |
| `log_missing` | named `.s3db` does not exist | Log X not found - open a log in N1MM |
| `ok` | log loaded | — |

When closed, the worked set is cleared (nothing is hidden) and the ini is
re-checked every `RETRY_SECONDS` (2 s) instead of `refresh_seconds`.
`maybe_reload()` returns True on an open/closed transition so views refresh.
`request_recheck()` (the **Reconnect** button) forces an ini re-read and a
full log reload on the next `maybe_reload()`. `inspect_active()` is the
detailed ini reader; `read_active()` wraps it for the old `None`-or-dict API.

### The WAL gotcha (important)

N1MM runs the database in **WAL mode**, so a newly logged QSO lands in
`<db>-wal` while the main file's size/mtime can stay unchanged. `maybe_reload()`
therefore computes a **change signature over both the `.s3db` and its `-wal`**:

```python
parts = [contest_nr]
for p in (path, path + "-wal"):
    st = os.stat(p); parts += [st.st_mtime_ns, st.st_size]
```

The signature is checked on **every tick** (~0.5 s), so a logged QSO disappears
from the list almost immediately. The heavier `N1MM Logger.ini` re-read (to catch
a contest/log switch) is throttled to `[worked] refresh_seconds`.

---

## 7. RBN integration (`rbn.py`)

- `TelnetFilter` strips IAC sequences and refuses (`DONT`/`WONT`) every option
  the server offers, so the feed works with raw sockets (stdlib `telnetlib` was
  removed in 3.13).
- `parse_spot` parses AR-Cluster lines, e.g.:

  ```
  DX de S53WW-#:  14014.0  K1ABC  CW  13 dB  25 WPM  CQ  1135Z
  ```

  → `Spot(dxcall, freq_khz, mode, snr, wpm, spot_type, spotter, time_utc, received)`.
- `RBNClient` connects, logs in with the callsign (on the `call` prompt or after
  a 3 s deadline), and keeps only spots whose spotter name starts with the
  configured prefix. Reconnects with exponential backoff (2→60 s).
- `ports_for_modes(modes, cw_port, ft8_port)`: CW/RTTY → 7000, FT8/FT4 → 7001.
  The app runs one `RBNClient` per needed port.

---

## 8. User interface

**Main window** (`PouncerApp`): header (N1MM freq/band + RBN status line with
worked status, per-band QSO counts or the "no log" message, then a **Sort: Time /
Freq / SNR** button bar), a controls row with the *"Also enter callsign"*,
*"Hide worked"* checkboxes, **Clear spots**, **Reconnect log** and **Open in
browser**, the `[ui] fkeys` TX/log buttons in one row of their own (grid,
equal columns), then the spot `ttk.Treeview`.
The sort buttons and the column headings share one sort state (`_set_sort`);
picking Time or SNR starts descending (newest / strongest first).

- Clickable column headings: `time, freq, snr, wpm, call, mode, type, spotter`
  (▲/▼ shows sort direction; `snr` defaults to strongest-first).
- Rows with SNR ≥ 20 get the `strong` tag (green).
- Click a row to act: `<ButtonRelease-1>` or `<Double-1>` per `[ui] trigger`.

**Touch view** = the phone page in the default browser: **Open in browser**
(`_open_browser`, `webbrowser.open` of `http://127.0.0.1:<port>/` - or
`[web] host` if bound to one address); opened at startup if `[ui]
touch_view`. (The separate Tk touch window was removed in v0.7.6.)

**Phone view** (`web.py`): a local HTTP server (`ThreadingHTTPServer`) that
serves the same needed spots as tap tiles. Page layout, top to bottom:

1. **Header row** - tappable (▾): opens the **band picker** (160/80/40/20/15/10 m
   buttons, current band green) - then the radio frequency in kHz (payload `freq`; the band if
   unknown) + modes on the left (`POUNCer` prefix hidden under 480 px), **Sort: Time / Freq / SNR** and **↻ Log** (reconnect) on the right.
   Sorting is client-side, kept in `localStorage`; Time = newest first, Freq =
   ascending, SNR = strongest first. The **DX** toggle (`pouncer.dxfirst`)
   moves DX spots ahead of EU ones, keeping the chosen order inside each group.
2. **Status line** (11 px, one line, ellipsis): needed-spot count · QSOs per
   band in the open contest (current band yellow) · total · log name (`.s3db`
   dropped; the `#contest` number hidden on phones).
3. **No-log banner** (orange, only when no log is open) with **Reconnect**.
4. **Spot tiles** (left) - the last tapped tile keeps a yellow border (`sel`,
   client-side `selKey` = call + freq) until another is tapped or it leaves
   the list; the tile within 0.5 kHz of the radio (`freq`) gets a
   light-blue outline ("tuned to"; also a light-blue table row
   via `_on_freq_spot`, `ON_FREQ_KHZ`). under 620 px wide: one column of half-height (46 px)
   tiles, callsign left / details right on two lines (`freq mode`, `SNR
   time`), ~12 fit on an iPhone 16. Wider: two columns of tall (84 px) tiles,
   callsign over details. Details are 13 px.
5. **Key column** (right) - `[ui] fkeys` buttons top-down (F2 first), green;
   red while pressed and for 0.8 s after a tap. Each key is two spot rows tall
   (`calc(2 * var(--row) + var(--gap))`, CSS vars shared with the grid), sticky
   under the header (`--hdr` set from JS), and shrinks only when the window is
   too short for all keys.
6. **Footer** (fixed, bottom) - thirds: **WPM − / WPM +** left (`/api/key`
   PgDn / PgUp → N1MM CW speed), **−/+ `tune_step_hz` Hz** middle
   (`/api/tune`), **NEXT ▶** right (client-side: `shown` = the tiles in
   display order; the one after `selKey`, else the first → `qsy()` like a
   tap). The NAR / WIDE buttons were removed in v0.7.9 (`/api/filter` and
   `_set_filter` are still there, unused by the page). `--ftr` (its height, set from JS) keeps the list
   and the key column clear of it.

| Route | Returns |
| --- | --- |
| `GET /` | Mobile HTML page (polls `/api/spots` every 1 s; layout above). |
| `GET /api/spots` | JSON `{band, freq, home_cont, tune_step, filter, modes, worked, qsos:{total, bands:[{band,count}]}, log:{open,state,message,name}, fkeys:[{key,label}], spots:[{call,freq,mode,snr,wpm,time,cont,country,dx,mult}]}`. `mult` = new multipliers on that band (0/1/2) from `WorkedLog.new_mults` (contest's `Contest.MultiplierNName` → `ZN` / `CountryPrefix` per band, `WPXPrefix` per contest). `dx` = continent known and ≠ `home_cont` (from `[rbn] callsign`). When `log.open` is false (and state ≠ `unknown`) the page shows an orange banner with `log.message` and a **Reconnect** button. |
| `GET /api/qsy?call=&freq=&mode=` | Enqueues a QSY action; returns `{"ok": …}`. |
| `GET /api/key?key=F4` | Enqueues a keystroke (`F1`-`F12`, `Enter`, or `PgUp`/`PgDn` = CW speed, else 400); returns `{"ok": …}`. |
| `GET /api/hide?call=&freq=` / `/api/unhide` | Long-press remove: enqueues `("hide", call, freq, bool)` → `_set_removed()`; `(call, band)` in `_removed` is filtered out of `_visible_spots` until restart. |
| `GET /api/tune?hz=25` | Footer −/+: enqueues `("tune", hz)` → `_tune()` → `set_frequency(next_tune_khz(...))` (steps from the last own target if recent / still matching, else from RadioInfo). |
| `GET /api/filter?w=narrow\|wide[&fix=1]` | (No button since v0.7.9.) Was the footer NAR/WIDE: enqueues `("filter", w, fix)` → `_set_filter()`. With `[ui] filter_narrow_cat` / `filter_wide_cat` set (shipped: Flex `ZZFI05;` / `ZZFI02;`), it sends `N1MMController.send_radio_command(cat_macro(cat, nr), nr, EntryWindowHwnd)` = `<RadioCmd><RadioNr>…</RadioNr><FocusEntry>hwnd</FocusEntry><RadioCommand>{CATnASC cmd}</RadioCommand></RadioCmd>` to UDP 13064 (undocumented N1MM packet; absolute setting, sent on every tap). Otherwise (both empty): N1MM only has a toggle (Alt+', `win32.send_filter_toggle`, VK_OEM_7 so any keyboard layout works), so the app remembers `_filter_width` (starts at `[ui] filter_start`, default `narrow`; empty = unknown) and sends the toggle only when it changes; `fix=1` (1 s hold) toggles the radio but keeps the remembered width, to resync after the Bandmap button was used. Never transmits. |
| `GET /api/band?band=40m` | Band picker: enqueues `("band", band)` → `_qsy_band()` - QSY to the last frequency used on that band (remembered each tick from RadioInfo) or `DEFAULT_BAND_KHZ`. Only `CONTEST_BANDS` (160/80/40/20/15/10 m), else 400. Never transmits. |
| tile line 2 | `first` (UTC first heard on that frequency, `OnFreqTracker.first_heard`, else the spot time) + SNR as `22dB` + `cq<min>`; line 1 = bold freq (`.fq`), mode only if not CW, `R<run>%`, `<hours>h`, `*`; a tile wider than the screen gets `.tight` (smaller text). Green tile = SNR ≥ 20. |
| `GET /help` | Help page (`HELP_HTML`, version filled in): every button, colour and mark. Linked from the `?` after the status line and from the band menu. |
| `GET /api/clear[?undo=1]` | Header CLR: enqueues `("clear", undo)` → `_clear_spots()` (keeps `_cleared` for Undo) / `_unclear_spots()`. |
| `GET /api/reconnect` | Enqueues `("reconnect",)` → `_reconnect_log()` (re-read which log N1MM has open). |

The page sorts client-side (instant, works while polling). The server feed is
capped at `WEB_MAX_SPOTS` (60) strongest-SNR spots.

**TX / log buttons:** `[ui] fkeys` (default `F2:RPRT F3:TU F4:CALL F5:HisCALL F7:? Enter`) renders the
same key bar in the main window and the phone page. Tapping one
fetches `/api/key`, the request is pushed onto `_web_queue` as `("key", key)` and
drained on the Tk tick like a QSY: `_press_key` focuses the Entry Window and
calls `win32.send_fkey(n)` (`SendInput` of `VK_F1+n-1`) for F-keys, or
`win32.send_enter()` (VK_RETURN) for `Enter`. N1MM then sends its configured
function-key message (F4 is normally `{MYCALL}`) or logs the QSO (Enter).
**F-keys transmit.** N1MM's documented hook for this is `EntryWindowHwnd`, which
exists precisely "to allow external software to send commands to a specific
EntryWindow (i.e. using AHK to send F1 to EW2 etc.)". Keys are sent even if
focusing fails (best-effort focus; the status line flags it).

Binds `[web] host` (default `0.0.0.0`) so it is reachable over the LAN and over
VPN/ZeroTier. The main window has a **Phone view** line, e.g.
`Phone view: http://10.147.17.5:8080 (ZeroTier)  listening on 0.0.0.0:8080`.
`web.pick_web_address()` picks the address: a ZeroTier adapter's IPv4 first
(adapters from `ipconfig`, parsed by `parse_ipconfig`; run with
`CREATE_NO_WINDOW`), else the LAN address of the default route, else
`localhost`; a specific `[web] host` is shown as-is. Re-checked every 60 s, so
ZeroTier coming up later is picked up. If the port can't be bound the line
says so. It is
LAN/VPN-only and unauthenticated.

---

## 9. Configuration reference (`config.ini`)

### `[rbn]`
| Key | Default | Meaning |
| --- | --- | --- |
| `host` | `telnet.reversebeacon.net` | RBN telnet host. |
| `port` | `7000` | CW/RTTY port. |
| `callsign` | *(empty)* | Login callsign sent to RBN (also gives the home continent for EU/DX). Commented out by default: then `worked.n1mm_station_call()` reads it from `N1MM Logger.ini` - `[Packet] Logon Callsign`, else `[EntryWindow] Operator` (`DEFAULT` ignored). None found: RBN login is skipped with a status message. |
| `spotter` | `S53WW` | Keep only spots whose spotter starts with this. |
| `follow_modes` | `CW` | Comma list of `CW, RTTY, FT8, FT4` (default CW only). |
| `ft8_port` | `7001` | FT8/FT4 port. |
| `cty` | *(blank)* | Country file for EU/DX; blank = N1MM's `SupportFiles\wl_cty.dat`. |
| `hide_beacons` | `true` | Drop beacon spots (`rbn.is_beacon`: type BEACON / NCDXF, or `.../B`). |

### `[n1mm]`

| Key | Default | Meaning |
| --- | --- | --- |
| `listen_ip` / `listen_port` | `0.0.0.0` / `12060` | RadioInfo UDP receive. |
| `control_ip` / `control_port` | `127.0.0.1` / `13064` | Control UDP send. |
| `radionr` | `1` | Radio to QSY. |
| `set_callsign` | `true` | Also type the callsign into the Entry Window. |
| `wipe_before_call` | `true` | Send Alt+W (wipe) before typing. |
| `focus_n1mm` | `true` | Bring the Entry Window to the foreground first. |
| `ini` | *(blank)* | Path to `N1MM Logger.ini`; blank = auto-detect. |

### `[worked]`

| Key | Default | Meaning |
| --- | --- | --- |
| `db` | *(blank)* | Explicit `.s3db`; blank = the log N1MM has open. |
| `hide` | `true` | Hide calls worked on the same band + mode. |
| `refresh_seconds` | `10` | How often to re-read the ini (contest/log switch). QSO changes are noticed every tick. |

### `[ui]`

| Key | Default | Meaning |
| --- | --- | --- |
| `sort` | `snr` | Initial sort column. |
| `max_age_minutes` | `10` | Drop spots older than this. |
| `trigger` | `single` | `single` or `double` click to act. |
| `touch_view` | `false` | Open the phone page in the default browser at startup. |
| `fkeys` | `F2:RPRT F3:TU F4:CALL F5:HisCALL F7:? Enter` | TX/log buttons (main/phone): `KEY[:label]` list, keys `F1`-`F12` (transmit) or `Enter` (logs); empty = no buttons. |

### `[web]`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Serve the phone/browser view. |
| `host` | `0.0.0.0` | Bind address (all interfaces, incl. ZeroTier). |
| `port` | `8080` | HTTP port. |

---

## 9.1 `_act_on_spot` (the action, shared by all views)

1. If `focus_n1mm` and we know `EntryWindowHwnd`: `force_foreground(hwnd)` then
   sleep 0.15 s.
2. `N1MMController.set_frequency(spot.freq_khz)` (QSY).
3. If `set_callsign` and the window is focused (or focusing is off):
   `wipe_callsign()` (Alt+W) then `type_text(spot.dxcall)`.
4. Update the status line. Never transmits.

## 9.2 `_press_key` (the TX / log buttons)

Reached from a main-window button or the phone page (via
`/api/key` -> `_web_queue` -> tick). Used by all three views.

1. If `focus_n1mm` and we know `EntryWindowHwnd`: `force_foreground(hwnd)` then
   sleep 0.15 s (best-effort; the key is still sent if focusing fails, and the
   status line notes it).
2. Dispatch: `F1`-`F12` -> `win32.send_fkey(n)` (N1MM runs that function-key
   message, e.g. F4 = `{MYCALL}` - **this transmits**); `Enter` ->
   `win32.send_enter()` (N1MM logs the QSO - no transmission).
3. Update the status line.

Same thread-safety rules as 9.1: reached only from the Tk thread (button command
or the tick draining a web request).

---

Keyboard: with `[ui] forward_fkeys` (default true) the root `bind_all`s
`<F1>`-`<F12>` (and `<Return>` / `<KP_Enter>` when `Enter` is in `fkeys`)
to `_forward_key(key)` → `_press_key(key)`, returning `"break"`.

## 10. Build & run

```
run.bat                 # python -m pouncer --config config.ini
build.bat               # PyInstaller -> dist\POUNCer.exe (+ copies config.ini)
```

Both `build.bat` and the GitHub Actions workflow build from **`POUNCer.spec`**
(onefile, windowed, `upx=False`); never pass options on the PyInstaller command
line, that would regenerate the spec. Because PyInstaller executes the entry
script as a bare `__main__`, the launcher `pouncer_main.py` imports
`pouncer.__main__` so the package-relative imports still resolve when frozen.

**Antivirus false positive.** A onefile exe built with PyInstaller's
*prebuilt* bootloader gets flagged by Windows Defender as
`Trojan:Win32/Wacatac.B!ml` (machine-learning false positive) and can't be
copied to another PC. Fix, same as Callbooker 1.11 (`s55oo/N1MM_callbook`):

- **Release exe = the GitHub Actions build** (`.github/workflows/build-exe.yml`,
  run from the Actions tab or by pushing a `v*` tag): clean `windows-latest`
  runner, runs the tests, compiles the bootloader from source
  (`pip install --no-binary pyinstaller --no-deps pyinstaller==6.22.2`, MSVC on
  the runner), builds the spec, prints the SHA-256 and a Defender scan, and
  uploads `POUNCer.exe` + `config.ini` as the `POUNCer` artifact. On a `v*`
  tag it also creates the **GitHub Release** with both files and the SHA-256.
- **Release ritual:** bump `__version__` (`pouncer/__init__.py`) and
  `pyproject.toml`, add a CHANGELOG section, commit and push, then either
  push a tag (`git tag v0.2.0 && git push origin v0.2.0`) or - Actions tab →
  *Build POUNCer.exe* → *Run workflow* with **release** = `v0.2.0` (the
  workflow creates the tag). It builds, tests and publishes
  https://github.com/s55oo/POUNCer/releases/tag/v0.2.0.
  A build without a release: Actions tab → *Build POUNCer.exe* → *Run
  workflow* (download from the run's Artifacts).
- **Repo:** https://github.com/s55oo/POUNCer (AGPL-3.0). Only source goes in
  - `build/`, `dist/`, `__pycache__/` are git-ignored; `config.ini` holds no
  secrets (RBN login is the callsign only).
- `upx=False` in the spec (packing is another common trigger).
- A local `build.bat` exe is for quick tests only, unless PyInstaller was
  installed with `--no-binary pyinstaller` (needs Visual Studio Build Tools).
- Still unsigned; SignPath Foundation (free for OSS) is the next step if it is
  ever flagged again.

Install (optional): `pip install .` exposes a `pouncer` console script
(`pyproject.toml`, no runtime dependencies).

---

## 11. Testing

```
python -m unittest discover -s tests -v
```

| Test file | Covers |
| --- | --- |
| `test_bands.py` | Band lookup / conversions. |
| `test_n1mm.py` | `RadioInfo` parsing, `RadioState`, control XML. |
| `test_rbn.py` | `parse_spot`, `TelnetFilter`, `ports_for_modes`. |
| `test_worked.py` | Active-log resolution, band/mode normalisation, contest filter, reload (incl. WAL-only writes), log-open states (no ini / no log / missing file / N1MM not running), reconnect. |
| `test_config.py` | `parse_modes`, `parse_fkeys`, defaults, file parsing. |
| `test_web.py` | HTTP endpoints (spots/qsy/key/reconnect), handler marshalling, error containment. |
| `test_cty.py` | CTY parsing (N1MM `#` comments), longest prefix, exact calls, `{CONT}` overrides, portable calls. |
| `test_win32.py` | N1MM process-name matching (process list faked). |
| `test_runhist.py` | Shard name, contest mapping, log choice, run share (incl. two stations interleaved), background lookup + disk cache (fake downloads), warm-up download once + offline harmless, separate SSB list (same call, different run % on CW and SSB), SSB list missing keeps CW, band-only frequencies not a run. |
| `test_warmlist.py` | `server/warmlist.py` sets (SSB = phone folders, mode PH, `warm-ssb.sqlite`), measuring SSB logs, band-only log left out. |
| `test_runlist_server.py` | Run list server: `CALLSIGN:` header, HTTP endpoints, newest year per call. |
| `test_team.py` | Election, discovery, master-only spot relay, sharing, joiner sync over HTTP, failover - two `Team`s on localhost ports; `version_mismatch`. |
| `test_flow.py` | Flow-layout geometry (wrapping, wide items, group gaps). |
| `test_tk_layout.py` | Real Tk window (Windows build only, skipped without Tk): controls wrap instead of clipping, text labels wrap to the width. |

The RBN and email/network paths are tested with fakes; the GUI itself is not
unit-tested (verified manually / by smoke runs).

---

## 12. Design notes & gotchas

- **Transmitting is opt-in.** Spot clicks never transmit. The only paths that
  can key the radio are the explicit `[ui] fkeys` F-key buttons
  (`_press_key` -> `send_fkey`); the `Enter` button only logs. There is no
  `RCmd` send method in the code anymore.
- **WAL:** worked-log freshness depends on watching `<db>-wal`, not just the db.
- **Frozen imports:** keep `pouncer_main.py` as the PyInstaller entry point.
- **Single source of truth:** every view (table/web) derives from
  `_visible_spots()`.
- **Stdlib only:** no `watchdog`, no web framework, no `telnetlib`.
