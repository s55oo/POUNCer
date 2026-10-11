# POUNCer — Changelog

## Unreleased

- **Footer** on the phone page with four buttons (none of them transmit):
  - **−25 Hz / +25 Hz**: move the VFO via N1MM (`radio_setfrequency`, same as
    a spot click). Steps from the last target it set, so repeated taps don't
    drift on N1MM's 10 Hz RadioInfo resolution (3 x +25 = exactly +75 Hz);
    after tuning by hand it steps from the radio again. Step size:
    `[ui] tune_step_hz = 25`. New `GET /api/tune?hz=±N`,
    `n1mm.next_tune_khz`.
  - **WPM − / WPM +**: press PgDn / PgUp in the N1MM Entry Window = N1MM's
    CW speed down / up (by the step in N1MM's Configurer). `/api/key` now
    also takes `PgUp` / `PgDn`; `win32.send_cw_speed`.
  The key column and the list keep clear of the footer.

- Phone page: the station you tap keeps its **yellow border** until you tap
  another one (or it leaves the list - worked, aged out, removed); before, the
  border vanished after 0.9 s. It stays yellow when it is also the tuned-to
  station (blue glow around it).

## v0.2.0 — 2026-10-11 — first GitHub release

- Code published at **https://github.com/s55oo/POUNCer** (AGPL-3.0), first
  commit `07f46ae`. Scanned before publishing: no passwords, tokens or keys
  (config holds only the callsign, skimmer and RBN host).
- First Actions build (run 38150237295) passed: tests, bootloader compiled
  from source, build, SHA-256, Defender scan, artifact.
- The workflow now also publishes a **GitHub Release** (POUNCer.exe +
  config.ini, SHA-256 in the notes) when a `v*` tag is pushed, or when
  *Run workflow* is given a **release** tag (the workflow then creates the
  tag itself). Release ritual: bump `__version__` + `pyproject.toml`,
  CHANGELOG, commit, push, then tag or *Run workflow* with the tag.
- README: new **Download** section (Releases page).
- Version 0.2.0.

## 2026-10-11 — log detection, reconnect, QSO counts, phone layout

Work done after taking the project over from the earlier OpenCode sessions.
Direction: moving towards a **web-only app** (server + `config.ini`); the Tk
GUI is kept for now while we observe which files are really needed.

### Already in the code when taken over (now documented)

- **Sort buttons** (Time / Freq / SNR) in the main window header and on the
  touch board. They share the sort state with the table's column headings;
  the touch board now follows the chosen sort instead of always SNR.

### N1MM log detection — "open a log" (`worked.py`, `win32.py`, `web.py`, `app.py`)

- POUNCer now knows whether N1MM actually has a log open. States
  (`WorkedLog.log_state`): `ok`, `n1mm_not_running`, `no_ini`, `no_log`,
  `log_missing` (plus `unknown` before the first check).
- **N1MM running check:** N1MM leaves `DXLog Name=` in its ini after it exits,
  so the ini alone can't tell. New `win32.is_n1mm_running()` scans the running
  processes for `N1MM Logger.net.exe` (stdlib `ctypes`,
  `CreateToolhelp32Snapshot`). Returns None on non-Windows → check skipped.
- New `worked.inspect_active()` reports *why* no log was found (no ini / ini
  names no log / file missing). `read_active()` keeps its old behaviour.
- While no log is open: the worked set is cleared (nothing is hidden), spots
  still show, and the ini is re-checked every 2 s (`RETRY_SECONDS`) so the
  warning clears quickly once a log is opened.
- Fixed: after a log disappeared, the status could flip back to "N calls"
  using the stale path. Closing now resets the path, signature and worked set.
- **Phone page:** orange banner with the reason, e.g. *"No log open in N1MM -
  open one with File > Open Log"* or *"N1MM Logger+ is not running - start it
  and open a log"*.
- **Main window:** the RBN line shows `LOG: <message>` instead of the worked
  status when no log is open.

- **Fix (after first Windows test):** the process check looked for
  `N1MM Logger.net.exe`, but the real exe is `N1MMLogger.net.exe` (no space,
  confirmed in `C:\HAM\N1MM Logger+`), so POUNCer wrongly said "N1MM is not
  running". Names are now compared without spaces. An empty/failed process
  scan now means "can't tell" (no warning), and a RadioInfo packet from N1MM
  within the last 30 s (`RADIO_ALIVE_SECONDS`) counts as proof N1MM is running.
  New `tests/test_win32.py`.

### Reconnect

- `WorkedLog.request_recheck()` forces an immediate ini re-read and full log
  reload (even if the file signature looks unchanged).
- Phone page: **Reconnect** button in the banner and an always-visible
  **↻ Log** button. New endpoint `GET /api/reconnect` → `("reconnect",)` on the
  web queue → `PouncerApp._reconnect_log()`.
- Main window: **Reconnect log** button.

### QSO counts per band

- The worked-log read now also counts QSOs per band for the open contest
  (`WorkedLog.qso_count`, `band_qsos()` in band-plan order; dupes included).
- `/api/spots` has a new `qsos: {total, bands: [{band, count}]}` field.
- Phone status line: `12 needed spots · QSOs 40m 6 · 20m 15 · total 21 ·
  S55OO_26 #8 CQWWCW` — the band you are on is highlighted yellow.
- Main window RBN line: `QSOs: 40m 6 20m 15 (total 21)`.

### Phone page layout (`web.py`)

- Sort buttons moved into the **top header row**, next to the band/mode title
  (the `POUNCer` word is hidden on phones to make room).
- Status line moved under the header row: one line, 11 px, ends with "…" if
  too long; `.s3db` dropped from the log name, contest `#nr` hidden on phones.
- **Compact tiles on phones** (< 620 px wide): half height (46 px), callsign
  left and freq / mode / SNR / time right on one line — about 10 callsigns fit
  on an iPhone 16 instead of 5. Wider screens keep two columns of big tiles.
- Spot details (freq, mode, SNR, time) one font size smaller (13 px).
- Header shows the radio's **exact frequency in kHz** (e.g. `14025.5 kHz CW`)
  instead of the band (`20m CW`); falls back to the band when N1MM sends no
  RadioInfo. `/api/spots` has a new `freq` field. On phones the reconnect
  button shrinks to just **↻** so the header stays on one line.
- **Band picker:** tap the frequency in the header (▾) to get big
  160 / 80 / 40 / 20 / 15 / 10 m buttons (current band green). Choosing one
  QSYs the radio via N1MM (`radio_setfrequency`, never transmits) to the last
  frequency used on that band this session, or a CW default
  (1820 / 3520 / 7020 / 14020 / 21020 / 28020 kHz). New `GET /api/band?band=`
  (contest bands only, else 400), `bands.CONTEST_BANDS` /
  `DEFAULT_BAND_KHZ`, `PouncerApp._qsy_band` + per-band frequency memory.
  On phones the header shows just the frequency (no "kHz", no mode).
- **Key buttons** (F2, F3, MyCall, Enter) are all **green**, turn **red** while
  pressed and stay red for 0.8 s after a tap. Same on the Tk touch board.
- Key buttons 30% bigger on the phone page (21 px text, 12 px vertical
  padding); they share the row equally (max 180 px each) so F2 / F3 / MyCall /
  Enter fit on one line on an iPhone 16.
- **Key column on the right:** the F2 / F3 / MyCall / Enter keys moved out of
  the header into a column on the right side of the spot list, F2 at the top
  going down. Each key is exactly two spot rows tall (phone: 98 px = 2 × 46 px
  + gap; wide: 2 × 84 px + gap) and lines up with the rows; the column is
  sticky (stays visible while the list scrolls) and shrinks only if the window
  is too short for all keys. On phones the spot details now sit on two short
  lines (`freq mode` / `SNR time`) so the narrower tiles still fit; 12 calls
  still fit on an iPhone 16.

### EU / DX marking (`cty.py`, `app.py`, `web.py`)

- Every spot's callsign is looked up in **N1MM's own country file**
  (`Documents\N1MM Logger+\SupportFiles\wl_cty.dat`, kept current by N1MM;
  override with `[rbn] cty =`). Our continent comes from `[rbn] callsign`
  (S55OO → EU); a spot on any other continent is **DX**.
- Phone page: **DX callsigns are yellow**, EU callsigns white (an orange
  continent badge was tried first and replaced). Tap-and-hold / hover shows
  the country. Long portable calls shrink so tiles stay one row high.
- Main window: new **Cont** column (sortable); DX rows get a light orange
  background. Touch board: DX callsigns in yellow.
- `/api/spots` spots have new `cont`, `country`, `dx` fields; payload has
  `home_cont`.
- New `pouncer/cty.py` (stdlib): CTY parser with longest-prefix match, exact
  `=CALL` entries, `{CONT}` overrides, portable calls (`EA8/DL1ABC`,
  `DL1ABC/EA8`, `/P`, `/4`; `/MM` = none). N1MM's file has multi-line `#`
  comments containing `;` - they are stripped first. Checked against the real
  `wl_cty.dat`: 45/45 sample calls on the right continent (incl. UA vs UA9,
  TA1 vs TA2, EA8, CT3). New `tests/test_cty.py`.

### Multipliers - MLT button (`worked.py`, `cty.py`, `app.py`, `web.py`)

- POUNCer reads the open contest's multiplier types from N1MM (`Contest`
  table, e.g. CQWWCW = `ZN` + `CountryPrefix`, CQWPX = `WPXPrefix`) and what
  is already worked from the log (`DXLOG.ZN / CountryPrefix / WPXPrefix`,
  open contest only). Zone and country count **per band**, a WPX prefix once
  per contest. IARU contests use the ITU zone.
- Each spot gets a count: **2 = double mult** (e.g. new zone + new country),
  **1 = single**, 0 = none. Zone / country / WPX prefix come from N1MM's
  `wl_cty.dat`; the CQ/ITU zone now also uses N1MM's call-area hints (the
  `#` blocks: W6 -> zone 3, RA9H.. -> 18, VE7 -> 3, VK6 -> 29, ...).
- Phone page: **MLT** button next to DX - doubles first, then singles, then
  the rest, each group still in the chosen Time / Freq / SNR (and DX) order.
  Mult tiles have a magenta bar on the left: thick = double, thin = single.
  Main window: new sortable **Mult** column (`MM` / `M`).
- Checked on the real log (CQWW CW #8): K1ABC on 20m = double, OH2BH and
  RA9HA = single, RV3DEP / S51A = none.
- Contests whose multipliers are other kinds (sections, states, exchange)
  show no mults yet.

### Remove a call from the list (long press)

- Phone page: **hold a callsign tile for 2 s** -> "Remove K1ABC (14015.5) from
  the list?" -> **Remove**. The call stays hidden on that band until POUNCer
  restarts (even when RBN spots it again); the toast offers **Undo** for 5 s.
  A short tap still QSYs; the list doesn't redraw while a tile is held.
  New `GET /api/hide` / `/api/unhide?call=&freq=` -> `_set_removed`.

### Beacons hidden (`rbn.py`, `app.py`, `config.ini`)

- Beacon spots are no longer shown (phone page, table, touch board): RBN type
  `BEACON` or `NCDXF B` (international beacon project), or a callsign ending
  in `/B`. New `rbn.is_beacon()`, filtered in `_visible_spots`. Setting
  `[rbn] hide_beacons = true` (default); `false` shows them again.

### "Tuned to" highlight (`web.py`, `app.py`)

- The spot nearest the radio frequency (within **0.5 kHz**, `ON_FREQ_KHZ`)
  is the station you are on: **light-blue outline** on the phone page,
  light-blue row in the main window table, blue tile on the touch board.
  Only one at a time; it follows the radio as you tune (the table/touch
  board redraw when the RadioInfo frequency changes) and disappears when you
  are between spots.

### DX-on-top button and more keys (`web.py`, `config.py`, `config.ini`)

- New **DX** toggle next to **SNR** on the phone page: when on (yellow), DX
  spots come first and EU after, each group still in the chosen Time / Freq /
  SNR order (stable sort). Remembered in the browser (`pouncer.dxfirst`).
- Keys are now **F2 RPRT, F3 TU, F4 CALL, F5 HisCALL, F7 ?, ENTER**
  (`fkeys = F2:RPRT F3:TU F4:CALL F5:HisCALL F7:? Enter`; F5 and F7 added,
  Enter last) - in `config.ini` and the built-in default. Each key shows the
  key name big and its label underneath in smaller text (phone page); the
  main window shows "F2 RPRT", the touch board "F2" over "RPRT". Six keys fit
  down the right side on an iPhone 16 (two rows each; they shrink a little
  on shorter screens).
- Fix: the phone-only sizes of the header sort buttons were overridden by
  the general rule, so they never applied; they do now (header fits on one
  line at 360-393 px with the extra DX button).

### Phone-view address in the main window (`web.py`, `app.py`)

- New **Phone view** line in the main window:
  `Phone view: http://10.147.17.5:8080 (ZeroTier)  listening on 0.0.0.0:8080`.
  Shows the ZeroTier address when ZeroTier is running, otherwise the LAN
  address; re-checked every minute. If the port is in use it says so.
- `parse_ipconfig` / `adapter_ipv4s` / `pick_web_address` in `web.py`
  (stdlib; works with localised Windows, e.g. Slovenian `Naslov IPv4`).

### Exe flagged by antivirus — build like Callbooker 1.11

- The exe could not be copied to another PC: Windows Defender flags onefile
  PyInstaller exes built with the prebuilt bootloader
  (`Trojan:Win32/Wacatac.B!ml`, false positive).
- New `.github/workflows/build-exe.yml` (copied from `s55oo/N1MM_callbook`):
  clean Windows runner, tests, **bootloader compiled from source**, build,
  SHA-256 + Defender scan, artifact `POUNCer` (exe + config.ini).
- `POUNCer.spec`: `upx=False`; `build.bat` now builds from the spec instead
  of passing options (which regenerated the spec).
- Repo: https://github.com/s55oo/POUNCer - the workflow lives in
  `.github/workflows/build-exe.yml` there (a copy sits in `ci/` in the local
  working folder, because the remote file tools may not write `.github`).

### Tests

- `test_worked.py`: log-open states (missing ini, ini without log, missing
  file, N1MM not running, "can't tell"), reconnect picking up a newly opened
  log, reconnect re-reading an unchanged log, per-band QSO counts.
- `test_web.py`: `/api/reconnect` (handler called; 404 when not wired), page
  contains the banner and reconnect hooks.
- 79 tests, all passing (Linux). Checked against a copy of the real log
  (`S55OO_26.s3db`, contest #8 CQWWCW): log found, 15 QSOs on 20m, latest
  WAL-only QSOs detected as worked.

### Not yet verified on Windows

- `is_n1mm_running()` (process scan) — run the app, close N1MM, the banner
  should appear within ~10 s.
- `dist\POUNCer.exe` has **not** been rebuilt; run `build.bat`.

### Files changed

`pouncer/worked.py`, `pouncer/win32.py`, `pouncer/web.py`, `pouncer/app.py`,
`tests/test_worked.py`, `tests/test_web.py`, new `tests/test_win32.py`,
`POUNCer.spec`, `build.bat`, new `.github/workflows/build-exe.yml`, `README.md`, `ARCHITECTURE.md`,
new `CHANGELOG.md`.

### Web-only migration notes (for later)

- Only `pouncer/app.py` uses tkinter. Its non-GUI half (spot store,
  `_visible_spots` filter, prune, tick loop, RBN client management,
  `_act_on_spot`, `_press_key`, `_reconnect_log`, web payload) should move to an
  engine module; the rest (`_build_ui`, table, touch window, labels) can go.
- Tk-only config keys: `[ui] trigger`, `touch_view`, `touch_columns`,
  `touch_rows`, `touch_font`; `[web] enabled` becomes meaningless.
- The "Also enter callsign" / "Hide worked" toggles exist only in Tk and need
  web equivalents.
- `build.bat` / `POUNCer.spec` use `--noconsole`; a headless server needs a
  console window or a tray icon.
- The server must keep running on the N1MM PC (SendInput keystrokes, UDP to
  localhost, reading the `.s3db`).
