# POUNCer — Changelog

## v0.7.8 — 2026-10-13 — NEXT button

- Phone / browser page: **NEXT ▶** in the footer (right) goes to the next
  station of the list, in the order shown (sort + DX / MLT / ⚡): QSY and
  enter its call, exactly like tapping its tile. It starts after the
  station you tapped last; after the last one it starts at the top again.
  No transmit.
- The **NAR / WIDE** filter buttons are removed (NEXT took their place);
  the `filter_*` lines are gone from config.ini.

## v0.7.7 — 2026-10-13 — Restart button restarts at once

- The blue **Restart -> vX** button (update ready) now restarts POUNCer
  right away with the new version - no "are you sure" question.

## v0.7.6 — 2026-10-13 — touch view = the browser page

- The separate Tk **touch view** window is removed. Its place is taken by
  the phone page in the PC's **default browser**: the **Open in browser**
  button (instead of *Touch view*), or `[ui] touch_view = true` to open it
  at startup. One view for phone, tablet and touch screen - with
  everything the page has (run history, ⚡, alerts, filters, CLR ...).
- `[ui] touch_columns / touch_rows / touch_font` are no longer used
  (ignored if still in config.ini).

## v0.7.5 — 2026-10-13 — function keys in one row

- Main window: the function-key buttons (`[ui] fkeys`, e.g. F2 RPRT ...
  ENTER) have their own row, always **one row**: equal buttons sharing the
  window width, narrower when the window is narrow (no wrapping).

## v0.7.4 — 2026-10-13 — auto-update

- **Auto-update** (POUNCer.exe): at start and every 6 hours POUNCer asks
  GitHub for the latest release. If it is newer, POUNCer.exe is downloaded
  in the background, its SHA-256 is checked against the one GitHub
  publishes (asset digest, else the hash in the release notes), and it
  replaces the exe on disk (the running one becomes `POUNCer.exe.old`,
  deleted at the next start). POUNCer **never restarts by itself** - the
  new version runs from the next start, or press the blue **Restart ->
  vX** button next to the lights. A wrong hash or no internet = nothing
  changes. New light **Update**: green = up to date, blue = update ready,
  amber = checking / failed. `[ui] auto_update = true` (false = off).
  `pouncer/update.py`.

## v0.7.3 — 2026-10-13 — connection lights

- Main window, under the MASTER / SLAVE bar: one light per connection -
  **RBN** (or "RBN via master" on a slave), **N1MM** (radio info coming
  in), **Log** (contest log open), **Team** (master / slave and how many
  POUNCers; amber = alone), **Phone** (web view running), **Warm-up**.
  Green = working, amber = waiting / partly, red = not working, grey =
  switched off. `pouncer/lights.py`.

## v0.7.2 — 2026-10-13 — warm-up: show when it was downloaded

- POUNCer.exe main window: `warm-up: 46044 stations known in advance
  (downloaded 2026-10-13 06:52)`.
- After each download POUNCer also writes `cache\warm-up.txt` next to
  `warm.sqlite`: date, stations, contests, years, source, next check.

## v0.7.1 — 2026-10-13 — warm-up list

- **Warm-up**: POUNCer downloads at start (once into `cache/warm.sqlite`,
  refreshed after 30 days) the run history of **46 044 stations** from
  CQWW CW, ARRL DX CW and CQ WPX CW 2021-2026 - submitted logs, and
  reconstructed logs for stations that never sent one. The R% / ~h / ⚡
  marks are there the moment a call is spotted, no per-call lookup wait.
  Calls not in the list are still looked up live. Main window shows
  `warm-up: N stations known in advance`. `[ui] warmup = true` (false =
  off); no download if the file is already there and fresh; harmless
  offline.
- `server/warmlist.py` builds the list from S53ZO's archive (sparse
  checkout); `.github/workflows/build-warmup.yml` (manual) publishes it as
  the `warmup` pre-release.

## v0.7.0 — 2026-10-12 — fade, alert, backup skimmers

- **Fade**: a tile whose station hasn't been re-spotted for 5 min
  (`[ui] fade_minutes`) is dimmed - it has probably stopped calling there.
- **One tile per station and band** (the newest spot) - no doubles from
  drift or several skimmers.
- **Alert** on the phone when a rare CQ (⚡) that is also a new multiplier
  appears on the band: beep (+ vibrate where the phone can; iPhone has no
  web vibration) and a pop-up "⚡ rare CQ + new mult: M0KTZ 7003.1". Once
  per station and band; not for what is listed when the page opens. On /
  off in the band menu (saved per phone). Sound needs one tap on the page
  first (browser rule).
- **Backup skimmers**: if the chosen skimmer (S53WW) sends nothing for 5 min
  (`[rbn] backup_after_minutes`), spots from `[rbn] backup_spotters` (call
  prefixes) or - if empty - every skimmer on your continent are used until
  it spots again. Main window: `BACKUP: S53WW quiet - using skimmers in EU`;
  phone: "⚠ backup skimmers" in the status line. In a team the master
  decides and passes the spots on. `rbn.SpotterFilter`.
- Help page and README updated; `/api/spots` gives `age`, `fade_s`,
  `backup`.

## v0.6.2 — 2026-10-12 — team over ZeroTier too

- The team announcement now goes out of **every network adapter** (LAN,
  ZeroTier, ...): one broadcast socket bound to each local IPv4 (Windows
  sends a broadcast only out of the adapter the socket is bound to; the
  list is re-read every 60 s, e.g. ZeroTier coming up later). Before, only
  the main adapter was used, so POUNCers on ZeroTier didn't see each other.
- POUNCers that know each other also talk **directly** (unicast), and spots
  / run history go only directly (no broadcast flood).
- New `[team] peers = 10.147.17.5, 10.147.17.8`: extra POUNCer IPs for
  networks that drop broadcasts.

## v0.6.1 — 2026-10-12 — MASTER / SLAVE in the Windows window

- The main window shows the team role at the top in a coloured bar:
  **MASTER** (green: this one talks to RBN for the team), **SLAVE** (blue:
  spots come from the master at <IP>), **ALONE** (grey: no other POUNCer on
  the network) or "Team off". The window title says `POUNCer 0.6.1 -
  MASTER` / `- SLAVE` too.

## v0.6.0 — 2026-10-12 — POUNCer team (several on one network)

- **Team mode** (`pouncer/team.py`, `[team] enabled = true`, UDP port
  12079): POUNCers on the same network (e.g. one per band / PC) find each
  other by UDP broadcast every 2 s - nothing to configure.
- **Master / slaves**: the one running longest - with a working RBN
  connection - is the master. Only the master connects to RBN; it passes
  its (S53WW-filtered) spots to the slaves, which don't connect at all.
  Master gone (6 s without a sign) or its RBN down over a minute -> the
  next longest-running one takes over. A new POUNCer waits 3 s before
  connecting to RBN so it doesn't log in just to hand over.
- **Shared learning**: every run-history result one learns is sent to all;
  a POUNCer that joins later fetches everything known from the master
  (`GET /api/runhist`). Each call is downloaded from GitHub only once for
  the whole station. The cq-minutes come from the same spots everywhere.
- Shown as `team: master (3 POUNCers)` / `team: slave of 192.168.1.10` in
  the main window's RBN line, and **M** / **S** at the start of the phone's
  status line (help page explains it).
- `RunHistory.merge` / `snapshot` / `on_learn`, `WebServer.set_runhist_provider`.
  Tests: election, spot relay only from the master, sharing, joining
  later, failover - two real teams on one machine.

## v0.5.12 — 2026-10-12 — "~7h"

- Hours on the air shown as an approximation: `~7h` (tiles, tooltip, help,
  README).

## v0.5.11 — 2026-10-12 — N1MM's own {WIPE} before every call

- Tapping a spot now first sends N1MM its own **`{WIPE}`** command (RadioCmd
  packet on the UDP control port, like NAR / WIDE) so N1MM wipes the Entry
  Window itself, whatever has the keyboard; Alt+W stays as a backup. Then
  0.2 s wait, the callsign box is emptied directly, and the clicked call is
  typed.

## v0.5.10 — 2026-10-12 — callsign box always clean

- Tapping a spot sometimes left several calls in N1MM's callsign box
  (Alt+W wipe not done yet when the call was typed). Now each tap: Alt+W
  (wipes call + exchange), then the focused callsign box is **emptied
  directly** (WM_SETTEXT "" on the focused EDIT control), then the call is
  typed (so N1MM still runs its dupe / mult checks). Status says
  `[box not cleared]` if the box couldn't be found.
  `win32.clear_focused_edit`; a Windows-only test on a real EDIT box runs
  in the build.

- README: "Reading a tile" table (colours, marks, line format); tile layout
  in ARCHITECTURE.

## v0.5.9 — 2026-10-12 — first-heard time on the tiles

- Tile line 2 starts with the **UTC time the station was first spotted on
  that frequency**: `1132Z 22dB cq3m` (from the RBN tracking; a spot not
  tracked yet shows its own spot time). Tooltip: "calling on this
  frequency for 3m (since 1132Z), last spot 1135Z". Help page updated.
- `OnFreqTracker.first_heard`; `/api/spots` gives `first` per spot.

## v0.5.8 — 2026-10-12 — bold frequency, cq3m, fit

- Spot frequency on the tiles in **bold** (white).
- Time on frequency written `cq3m` / `cq1h35` (was `CQ 3m`).
- Mode shown only when it isn't CW (e.g. `RTTY`) - saves room.
- Tiles that still don't fit (long call + every mark) get slightly smaller
  text automatically; under 380 px the details are a bit smaller. Checked
  320-430 px: everything fits from 360 px up.

## v0.5.7 — 2026-10-12 — "17dB"

- Signal strength written without a space: `17dB` (phone tiles, help page,
  touch view).

## v0.5.6 — 2026-10-12 — pop-up next to ENTER

- The pop-up message now appears **level with the ENTER key** (the last
  key on the right), centred over the spot list so it never covers the
  keys; without keys, just above the footer; kept on screen in landscape.
  Positioned from JS (`placeToast`) each time it shows.
- Dark grey with a light border (was the same green as the tiles).

## v0.5.5 — 2026-10-12 — help page

- Phone: **help page** (`/help`) explaining every button, colour and mark:
  top row (frequency / band menu, CLR, sorts, DX, MLT, ⚡, ↻), the status
  line and orange log bar, the tiles (tap, hold, yellow / green / magenta /
  blue, R% / h / * / ⚡ / CQ 3m), the F-keys and ENTER, the bottom row
  (WPM, ±25 Hz, NAR / WIDE) and the pop-ups. Opens from the small **?** at
  the end of the status line and from the band menu; "Back to the spots".

## v0.5.4 — 2026-10-12 — CQ time on frequency (from RBN)

- Each tile shows **`CQ 3m`**: how long the station has been calling on
  that frequency, learned live from the RBN spots (no internet lookup). A
  re-spot within 0.5 kHz and within 20 min of the last one = still there;
  moved or silent longer = starts again. Shown as `CQ 27m` / `CQ 1h35` in
  place of the spot time (which moves to the tile's tooltip).
- With R% / ⚡ this tells "rare runner, just started" - the best moment.
- `rbn.OnFreqTracker`; `/api/spots` gives `cq_min` per spot.

## v0.5.3 — 2026-10-12 — run history over the last 6 years

- Run history now uses the station's logs of the **last 6 years** of the
  same contest + mode (e.g. CQWW CW 2020-2025, up to 6 logs; was only the
  newest log). Run % is over all their QSOs, hours = average per contest.
  No log of this contest: its **other contests in the same mode** (CW
  contest open in N1MM -> CW contests) of those 6 years. Reconstructed logs
  still only for stations that never sent one.
- A call's logs download in parallel (12 downloads at a time overall).
  Real archive (CQWW CW): S50R R82% 24h (6 logs), K3LR R68% 46h (6),
  9A1A R85% 48h (4), S53M R81% 45h (6), S53CC R77% 15h (1), OH2BH R41% 13h
  (2: 9 % in 2025, more in 2020 - no longer marked), M0KTZ R6%* 3.5h ⚡.
- Cache file `cache/runhist3.json` (older entries were from one log only).

## v0.5.2 — 2026-10-12 — CLR on the phone

- Phone header: **CLR** button in front of Time clears every spot from the
  list (e.g. old spots from before the contest started); new spots come in
  again as RBN sends them. The pop-up offers **Undo** for 5 s (brings the
  cleared spots back; newer spots of the same call win). Same as the main
  window's "Clear spots". New `GET /api/clear[?undo=1]`.

## v0.5.1 — 2026-10-12 — own call from Change Your Station Data, never listed

- POUNCer's own call is now the **open log's station call** (N1MM Config >
  Change Your Station Data = the log's `Station` table), read with the log
  and re-read when another log is opened (e.g. a CR3A log -> CR3A, home
  continent AF). Fallback: `[rbn] callsign`, then N1MM's ini (as before).
- **Your own station is never in the list** (bug seen in the contest: own
  RBN spots showed up). Also `/P`, `9A/S55OO` style variants.
- Home continent (EU/DX colouring) follows that call; the main window's Log
  line shows `station: S55OO (EU)`; `/api/spots` gives `station`.
- `worked.same_station`, `WorkedLog.station_call`.

## v0.5.0 — 2026-10-12 — rare CQ: run % + hours on air, reconstructed logs

- Run history also measures **hours on the air** in the station's last log
  of the contest (time between QSOs; a pause over 30 min = off time). Tiles
  show e.g. `R11%* 4h`.
- **⚡ = rare CQ**: runs under 30 % of its QSOs **or** on the air under 6 h.
  The ⚡ sort puts rare ones first (then lowest run %, then fewest hours),
  unknown last.
- Stations that never sent a log now use the archive's **reconstructed
  logs** (rebuilt from other stations' logs), marked `*`: a minimum - only
  QSOs with stations that sent logs. Example M0KTZ (8 logs, all
  reconstructed): CQWW CW 2025 = 75 QSOs, 11 % run, 4.1 h -> `R11%* 4h ⚡`
  (was no info). Submitted logs are always preferred.
- Real archive check: OH2BH R9% 15h ⚡, S50R R82% 23h, K3LR R65% 48h,
  S53CC R77% 15h, 9A1A R84% 48h.
- Cache file is now `cache/runhist2.json` (old "no info" entries would
  have hidden the reconstructed results). `/api/spots` gives `run`,
  `hours`, `rec` per spot and `rare_hours`.

## v0.4.1 — 2026-10-12 — run list file on Windows (no server needed)

- POUNCer reads a ready-made **`runlist.sqlite`** (built by
  `server/runlist.py`) straight from its folder: `[ui] run_list =
  runlist.sqlite` (default; missing file = ignored). The whole list (CQWW
  CW+SSB 2023-2025, 47 879 logs) loads in 0.16 s at start, so the R% / ⚡
  marks are there at once with no Docker, server or network. Calls not in
  the list still come from GitHub. `RunHistory.load_run_list`.
- The Docker server (`run_server`) stays optional, e.g. for several PCs.

## v0.4.0 — 2026-10-12 — run list server (Docker), live re-sort

- New **run list server** (`server/`: `runlist.py`, `Dockerfile`,
  `docker-compose.yml`, README) for a Docker host on ZeroTier / LAN. First
  start downloads only CQWW CW + SSB logs of the last 3 years from S53ZO's
  archive (partial clone + sparse checkout) and computes every station's
  run %: measured 47 879 logs, 3.3 GB, 3.5 min -> 2.6 MB `runlist.sqlite`,
  12 190 CW calls (OH2BH 9 %, K3LR 65 %, S50R 82 %). Serves
  `/runs?contest=&mode=` (whole list, 130 KB in 0.09 s), `/run?call=`,
  `/status`.
- POUNCer: `[ui] run_server = http://<host>:8077` - loads the whole list for
  N1MM's contest + mode once, so R% / ⚡ show the moment a call is spotted;
  calls not in the list (e.g. S53CC: last CQWW CW log 2020) still come from
  GitHub one by one. Server down = GitHub as before.
- Phone page: the list re-sorts live as run % arrives (every 1 s poll, ⚡
  sort on); it stays still while a tile is touched and 0.4 s after, so a
  re-sort can't swap the tile under your finger.
- `.dockerignore`; tests for the server (HTTP, newest year, header call)
  and for the list mode (no GitHub when the server knows the call).

## v0.3.1 — 2026-10-12 — run history fast, ⚡ sort

- Run history is looked up **8 calls at a time** (was one by one) and from
  the **newest log only** (was 2). Measured for S53CC: index shard 1.27 MB
  in 0.78 s + log 105 KB in 0.44 s + 3 ms to compute = ~1.2 s for a call
  never seen before (~0.45 s once its shard is cached; instant once the
  call is cached). A band with 40 new calls: all known after ~6 s instead
  of ~49 s (simulated with those download times). Two calls in the same
  shard download it once; the cache file is written atomically.
- New **⚡** header button (next to MLT): rarely-running stations on top =
  lowest run % first, unknown last; DX / MLT still group on top of it.
  Saved per phone like the other toggles.
- Header buttons a bit tighter below 400 px so the frequency always shows
  in full (checked at 320-430 px).

## v0.3.0 — 2026-10-12 — run history from the contest-log archive

- Phone tiles show how often a spotted station **runs**: `R81%` = 81% of
  its QSOs in past logs were made calling CQ (same frequency as one of its
  last two QSOs on that band). **⚡** = runs under 30%: a station that
  mostly searches but is spotted calling CQ won't stay long - work it now.
  Hover / long text in the tile title explains it.
- Data: S53ZO's public archive
  (github.com/s53zo/Hamradio-Contest-logs-Archives). The call's `SH6`
  shard (`logs_XX.sqlite`, XX = crc32(CALL) & 0xff, ~1.4 MB) says which logs
  exist; the newest 2 of the same contest + mode (N1MM's contest, e.g.
  CQWWCW -> `CQWW` CW; else any contest in that mode, never reconstructed
  logs) are downloaded and measured. Fewer than 30 QSOs = no info.
- Runs in one background thread, nothing blocks; shards and results are
  kept in a `cache` folder next to config.ini (`runhist.json`), so each call
  is fetched once. No internet = simply no info.
- New `pouncer/runhist.py`; `[ui] run_history = true` (false = off);
  `/api/spots` gives `run` per spot and `rare_run`.

## v0.2.10 — 2026-10-11 — NAR / WIDE set the filter by CAT (Flex)

- NAR / WIDE no longer use N1MM's Alt+' **toggle** (POUNCer could never know
  which width the radio was on, so the buttons kept ending up reversed).
  They now send an absolute CAT command to the radio through N1MM: the
  undocumented N1MM `<RadioCmd>` UDP packet (port 13064, as other N1MM
  control apps use) carrying the macro `{CAT1ASC <cmd>}`. NAR always sets
  narrow, WIDE always sets wide, and every tap is sent.
- New `[ui] filter_narrow_cat` / `filter_wide_cat`. `config.ini` ships with
  FLEX-6000 (SmartSDR CAT) values: `ZZFI05;` (narrow) and `ZZFI02;` (wide)
  = filter preset 05 / 02 of the current mode (00 widest .. 07 narrowest).
  Change the numbers to the presets you like; Kenwood-style radios can use
  e.g. `FW0250;`. Both empty = the old Alt+' toggle behaviour.
- `n1mm.radio_command_xml`, `n1mm.cat_macro`,
  `N1MMController.send_radio_command`, `RadioState.active_radio_nr`;
  `/api/spots` gives `filter_mode` (`cat` / `toggle`).
- Note: N1MM's Bandmap Wide/Narrow indicator follows only its own button,
  not the CAT commands.

## v0.2.9 — 2026-10-11 — Windows window: nothing cut off when narrow

- Main (Tk) window, top part: when the window was made narrower, the end of
  the long status line and the row of buttons were cut off at the right.
  Now:
  - the status is three lines: **N1MM** (frequency, band, mode), **RBN**
    (status, spotter, mode, band, spots, hidden) and **Log** (log name,
    worked, QSOs per band) - the log info used to be on the end of the RBN
    line;
  - every text line (also the phone-view and status lines at the bottom)
    wraps onto more lines instead of being clipped;
  - Sort, the two checkboxes, the F-keys and Clear spots / Reconnect log /
    Touch view flow left to right and move onto a second / third row as
    the window gets narrower (new `pouncer/flow.py` + `app.FlowFrame`);
  - the window can be made narrower: minimum width 420 px (was 720).
- Build: a real-Tk layout test (`tests/test_tk_layout.py`) runs on the
  Windows runner; its result is written into the release notes.

## v0.2.8 — 2026-10-11 — pop-up hides completely

- The pop-up message could stay on screen after hiding (seen on the
  iPhone): it was only faded out (opacity / visibility). Now, after the
  fade, it is taken out of the page completely (`hidden` attribute ->
  `display: none`), both after the 5 s timeout and when tapped; it starts
  hidden too. New `hideToast()` in the page script.

## v0.2.7 — 2026-10-11 — pop-up message at the top

- The pop-up message (toast) now appears at the **top**, just under the
  header (`top: --hdr + 12px`), instead of above the footer - well clear of
  the footer buttons. Dark shadow and a light edge so it stands out over
  the spot tiles. Still 5 s, tap to close.

## v0.2.6 — 2026-10-11 — NAR/WIDE no longer reversed, pop-up higher

- **NAR / WIDE were reversed**: POUNCer started with the filter width
  "unknown", so the first tap always sent N1MM's Alt+' toggle - with the
  radio already narrow, NAR made it wide, and from then on every button did
  the opposite. POUNCer now starts assuming **narrow** (NAR lit), so NAR
  does nothing and WIDE switches to wide. Set by the new
  `[ui] filter_start = narrow` (`wide`, or empty = unknown). Holding a
  button 1 s still toggles the radio without changing the lit button, to
  resync if they ever differ.
- Pop-up message raised one more row: now **three** tile rows above the
  footer (still 5 s, tap to close).

## v0.2.5 — 2026-10-11 — callsign from N1MM, none in config.ini

- `config.ini`: `callsign = S55OO` is now commented out, so the file can be
  given to others as is. With no `[rbn] callsign`, POUNCer uses the call N1MM
  has in `N1MM Logger.ini` (`[Packet] Logon Callsign`, else the Entry Window
  `Operator`) for the RBN login and the home continent. If none is found,
  the status says "no callsign: set [rbn] callsign in config.ini". New
  `worked.n1mm_station_call()`; the built-in default is now empty (was
  S55OO).

## v0.2.4 — 2026-10-11 — pop-up message higher, 5 s

- The pop-up message (toast) now shows two tile rows above the footer, so it
  never covers the footer buttons. Before, a "hidden" toast was only slid
  80 px down - right onto the footer; now it fades out completely.
- Every message stays 5 s (was 1.2 s; Undo was already 5 s); tap it to close
  it sooner.

## v0.2.3 — 2026-10-11 — RX filter NAR / WIDE in the footer

- Right third of the footer: **NAR** and **WIDE** buttons for the radio's
  receive filter, like the Wide/Narrow button on N1MM's Bandmap. N1MM has
  only a toggle for it (**Alt+'** in the Entry Window, using the filter
  codes set in the Bandmap's "Set Transceiver Filter Codes"), so POUNCer
  remembers which width it set and lights that button amber:
  - tap the dark button: Alt+' is sent and that button lights up;
  - tap the lit one: nothing is sent ("Already narrow");
  - **hold 1 s**: Alt+' is sent but the label stays - use it if the radio
    and the lit button differ (after the Bandmap button was used, or at
    start, when neither is lit).
  New `GET /api/filter?w=narrow|wide[&fix=1]`, `win32.send_filter_toggle`
  (virtual key VK_OEM_7, works with a Slovenian keyboard too), `filter` in
  `/api/spots`. Does not transmit.

## v0.2.2 — 2026-10-11 — rounded selection ring, version shown

- The selected (tapped) station's yellow border is now drawn as a rounded
  ring (box-shadow) instead of an outline - older iOS draws outlines with
  square corners. It combines with the magenta mult bar and the blue
  tuned-to glow.
- The version is shown in the page title (browser tab) and the main window
  title, e.g. "POUNCer 0.2.2", so an old copy still running on port 8080 is
  easy to spot.

## v0.2.1 — 2026-10-11 — footer (VFO step, CW speed), sticky selection

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
  Layout: **WPM − / WPM +** on the left, **−25 / +25 Hz** in the middle,
  right side (under the key column) left free. The key column and the list
  keep clear of the footer.

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
