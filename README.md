# POUNCer

A small Windows tool that watches your **N1MM Logger+** radio frequency over the
network and shows you live **Reverse Beacon Network** spots spotted only by a
chosen skimmer (default **S53WW**), for the band you are currently on.

Clicking a spot re-tunes the radio (QSY) in N1MM and, optionally, types the
spotted callsign into the N1MM Entry Window.

## How it works

* **RBN** - a telnet client connects to `telnet.reversebeacon.net:7000`, logs in
  with your callsign (from N1MM unless `[rbn] callsign` is set), and keeps only the spots whose spotting node matches the
  configured `spotter` (e.g. `S53WW`). By default it is **CW only**; the
  `[rbn] follow_modes` option can additionally show RTTY (port 7000) and
  FT8/FT4 (port 7001).
* **N1MM radio info** - N1MM broadcasts an XML `<RadioInfo>` packet on UDP port
  **12060**. POUNCer reads the frequency/mode from it and uses the band to
  filter the spots it displays.
* **N1MM control** - clicking a spot sends XML to UDP port **13064**:
  * `<radio_setfrequency>` to QSY the radio.
* **Callsign entry** - the callsign is typed with simulated keystrokes
  (`SendInput`) into the focused N1MM Entry Window (Alt+W to wipe, then the
  characters). No N1MM transmit command is ever sent, so the callsign is never
  sent over the air. The `[n1mm] focus_n1mm` option brings the Entry Window to
  the foreground first so the keystrokes land in the callsign box.
* **EU / DX** - each spotted call is looked up in N1MM's own country file
  (`SupportFiles\wl_cty.dat`). Stations outside your continent (EU for S5)
  are DX: their callsign is **yellow** on the phone page and touch board
  (EU calls white), plus a **Cont** column and light-orange rows in the main
  window.
* **Worked calls** - N1MM logs are SQLite (`.s3db`) files, and each file can
  hold many contests. POUNCer opens the database N1MM currently has open
  (read-only, auto-detected from `N1MM Logger.ini`) and considers **only the
  contest N1MM has open** (`Contest NR=` in the ini), so QSOs from other
  contests are not treated as worked. Spots whose callsign is already worked on
  the **same band and mode** are hidden. The status line also shows how many
  QSOs the open contest has per band (the band you are on is highlighted) and
  the total. The log is re-read every few seconds -
  and whenever you switch contests - so changes take effect quickly.
* **No log open** - if N1MM Logger+ is not running, its ini names no log, or the
  named `.s3db` is missing, the phone page shows an orange **"open a log in
  N1MM"** banner (and the main window says so in its Log line). Spots are still
  shown, but nothing can be hidden as worked. POUNCer looks again every 2 s, so
  the banner clears on its own once you open a log; the **Reconnect** button
  (banner, the **Log** button next to Sort, or *Reconnect log* in the main
  window) re-reads which log N1MM has open immediately.
* **Touch view** - an optional second screen sized for a 1080p display with
  large tap targets (one big button per spot). It lists the needed spots
  strongest-SNR first in at most two columns, filled top-to-bottom (strongest
  at the top-left). Tapping a button QSYs and enters the call exactly like
  clicking a table row. Open it with the **Touch view** button (or set
  `[ui] touch_view = true` to open it at startup); Esc closes it.
* **Phone view (web)** - a small local web server (stdlib only) serves the same
  needed spots as big tap tiles for a phone/browser. Tapping a tile QSYs and
  enters the call on this PC (same as the touch view). Open
  `http://<pc-ip>:8080` - the exact URL is shown on the **Phone view** line of
  the main window: the ZeroTier address if ZeroTier is running, otherwise the
  LAN address, plus the port it listens on.
  Binds to all interfaces, so it works over your LAN and over ZeroTier/VPN for
  remote access. LAN/VPN only, no password. **Sort: Time / Freq / SNR** buttons
  in the top header row reorder the tiles (the choice is remembered in the
  browser); the **DX** button next to them puts DX spots on top, still in that
  order, and **MLT** puts new multipliers on top (double, then single; magenta
  bar on the tile - thick = double). The station your radio is tuned to
  (within 0.5 kHz) has a light-blue outline. **Hold a tile for 2 s** to remove
  that call from the list (with Undo); the station you tapped keeps a yellow
  border until you tap another. The **footer** has **WPM − / WPM +** on the
  left (N1MM PgDn / PgUp = CW speed) and **−25 Hz / +25 Hz** in the middle
  (move the VFO via N1MM; step = `[ui] tune_step_hz`), and **NAR / WIDE** on
  the right (RX filter: by CAT through N1MM, `[ui] filter_narrow_cat` /
  `filter_wide_cat`, shipped for a FLEX-6000 - or, with those empty, via
  N1MM's Alt+' wide/narrow toggle, like the Bandmap's
  Wide/Narrow button; the width POUNCer set is lit amber - it starts as
  narrow, `[ui] filter_start` - hold 1 s to toggle
  the radio without changing the label if they differ); none of them transmit.
  **Run history:** each tile shows `R81%` = how often that station ran (called
  CQ) in its past logs of the same contest, from S53ZO's public contest-log
  archive; **⚡** = runs under 30% - a CQ from it won't last, work it now
  (`[ui] run_history`, cached in a `cache` folder). The **⚡** header button
  sorts the rarely-running stations to the top. A new call is known in about
  a second (8 looked up at a time; instant once cached).
  Simplest: put the ready-made **`runlist.sqlite`** next to `config.ini`
  (`[ui] run_list`) - the whole CQWW list loads at start, no server needed.
  Or with the optional **run list server** (`server/`, Docker, e.g. on ZeroTier;
  `[ui] run_server`) the whole CQWW list is loaded at start, so the marks
  are there the moment a call is spotted. See `server/README.md`.
  Confirmations ("Wide filter", "CW faster", Undo after a remove) pop up at
  the top, just under the header, for 5 s (tap to close). On a phone the tiles are compact one-line rows (callsign left,
  details right) so about 10 fit on screen; wider screens get two columns of
  big tiles. The TX/log key buttons sit in a column on the right of the list
  (F2 at the top, each key two rows tall); they are green and turn red when
  pressed.
  The header shows the radio frequency; **tap it to change band** (160, 80,
  40, 20, 15, 10 m) - the radio goes to the last frequency you used on that
  band, or a CW default.
* **TX / log buttons** - the main window, the touch board and the phone page all
  show buttons for `[ui] fkeys` (default `F2:RPRT F3:TU F4:CALL F5:HisCALL F7:? Enter`). An F-key
  button presses that function key in the focused N1MM Entry Window, so N1MM
  sends its message - e.g. F4 normally sends `{MYCALL}`. Unlike tapping a spot,
  **F-keys transmit over the air**. The `Enter` button logs the current QSO in
  N1MM (no transmission). N1MM's documented way to do this from another program
  is to send the keystroke to the Entry Window (`EntryWindowHwnd`). Reorder or
  disable the buttons with e.g. `fkeys = F1:CQ F4:MyCall Enter` (empty = no
  buttons).

Only the Python standard library is used (tkinter for the GUI). No pip install
is required.

## Download

Get **POUNCer.exe** and **config.ini** from the
[Releases page](https://github.com/s55oo/POUNCer/releases) and put both in the
same folder. Release builds come from GitHub Actions with the PyInstaller
bootloader compiled from source, so Windows Defender doesn't flag them
(locally built exes can be flagged `Trojan:Win32/Wacatac.B!ml` - a false
positive). Each release lists the exe's SHA-256.

## N1MM Logger+ setup

1. Open N1MM Logger+.
2. `Config` -> `Config Ports...` -> **Broadcast Data** tab.
3. Tick **Radio**.
4. In the destination/IP box next to it enter `127.0.0.1:12060`.
5. Click OK. N1MM now broadcasts radio info every ~10 s and on every change.

No setting is needed for the control direction (port 13064 is always listened
to by N1MM).

## Running

```
python -m pouncer --config config.ini
```

or double-click `run.bat`.

Edit `config.ini` and set at least your `callsign` under `[rbn]` and, if
desired, a different `spotter`.

## Configuration

All options live in `config.ini` and are documented inline. Key ones:

| Option | Meaning |
| --- | --- |
| `[rbn] callsign` | Your call, used to log in to RBN. |
| `[rbn] spotter` | Only spots from a node starting with this string are shown. |
| `[rbn] hide_beacons` | Hide beacon spots (RBN type BEACON / NCDXF, or calls ending in `/B`). Default `true`. |
| `[rbn] follow_modes` | Modes to show, comma separated (`CW`, `RTTY`, `FT8`, `FT4`). Default `CW`. |
| `[n1mm] listen_port` | UDP port N1MM broadcasts RadioInfo on (default 12060). |
| `[n1mm] control_port` | UDP port N1MM listens for control on (default 13064). |
| `[n1mm] radionr` | Radio to QSY (1 or 2). |
| `[n1mm] set_callsign` | Also type the callsign into the Entry Window on click. The call is entered with simulated keystrokes only - no transmit command and no Enter/F-key is sent by this action. |
| `[n1mm] focus_n1mm` | Bring the N1MM Entry Window to the foreground on click so the callsign lands in the callsign box. |
| `[n1mm] ini` | Path to `N1MM Logger.ini`. Blank = auto-detect in `Documents\N1MM Logger+`. |
| `[worked] db` | Path to the `.s3db` log used for the worked check. Blank = the log N1MM has open. |
| `[worked] hide` | Hide spots already worked on the same band + mode. |
| `[worked] refresh_seconds` | How often to re-check which log/contest N1MM has open. Newly logged QSOs are picked up almost immediately regardless (the log's WAL file is checked every ~0.5 s tick). |
| `[ui] sort` | Initial sort column (`time`, `freq`, `snr`, `wpm`, `call`, `mode`, `type`, `spotter`). Click any column heading to sort by it; click again to reverse. The up/down arrow in the heading shows the current sort. SNR sorts strongest first. |
| `[ui] trigger` | `single` or `double` click to QSY. |
| `[ui] touch_view` | Open the big-button touch view at startup (`true`/`false`). |
| `[ui] touch_columns` | Touch-view columns (default 2; clamped to a maximum of 2). |
| `[ui] touch_rows` | Touch-view rows per column (default 8). |
| `[ui] touch_font` | Tile font size (points) in the touch view (default 26). |
| `[ui] fkeys` | TX/log buttons in the main window, touch board and phone page: `KEY[:label]` list (default `F2:RPRT F3:TU F4:CALL F5:HisCALL F7:? Enter`). Each F-key **transmits** its N1MM message; `Enter` logs the QSO. Empty = no buttons. |
| `[web] enabled` | Serve the phone/browser view (`true`/`false`, default `true`). |
| `[web] host` | Interface to bind (`0.0.0.0` = all, including ZeroTier). |
| `[web] port` | Port for the phone view (default 8080). |

## Tests

```
python -m unittest discover -s tests -v
```
