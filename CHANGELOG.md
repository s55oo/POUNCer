# POUNCer — Changelog

## v0.7.59 — 2026-10-16 — band chips coloured by DX share, clock via NTP

- **Band open chips coloured by the DX share** (window and the phone's
  band-menu dots): orange = the stations heard are (almost) all from your
  own continent, through yellow, to green = (almost) all DX - in proportion
  to the % of DX among all different stations heard on the band. The chip
  shows the %: **20m: 34 DX 85%** (also with only a few DX: **15m: 2 DX
  100%**); **80m: EU only** = no DX at all. Grey = nothing heard.
- The window's **Radio A / B** switch (and the one in the compact window)
  only shows while N1MM reports two radios / VFOs (SO2R / SO2V) - with one
  radio there is nothing to switch. Like the phone's A / B key.
- **Click a band chip** in the window: the list shows that band's spots
  (▶ on the chip, "▶ list: 20m" in the top bar) - the radio stays where it
  is. Click a spot there and the radio QSYs to it; the list then follows
  the radio again. Click the chip again (or the radio's own band) to go
  back. Like the phone's band menu.

- The check line's PC clock now comes from an **NTP time server**
  (pool.ntp.org, else time.windows.com; UDP 123, milliseconds) instead of a
  web server's `Date` header, which could be seconds old when the answer
  came from a cache (a correct clock read "Clock -24 s"). The web header is
  only a fallback, now corrected by `Age` and with more slack.
- The text says **PC 24 s fast** / **PC 3 s slow** instead of "-24 s".

## Docs — 2026-10-16

- **docs/NETWORK.md** brought up to v0.7.58: N1MM App Info (open log and
  contest) on UDP 12060, the CW speed read and the Ctrl+K CW keyboard
  (Windows messages), the master's band-open counts in the team message,
  the PC clock check against GitHub, and autostart (local programs, no
  network). The **network diagram** (SVG + PNG) shows them too.

## v0.7.58 — 2026-10-16 — band open chips say what they mean

- The window's band-open chips now read **20m: 34 DX** (open: 34 different
  DX stations heard), **80m: EU only** (only your own continent) and
  **160m: –** (nothing heard), after the title *Band open (DX heard,
  10 min):* - before it was just "20 34".

## v0.7.57 — 2026-10-16 — autostart: the same program more than once

- The same program listed several times (e.g. 4 copies of a log router as
  programs 1-4) now starts every copy. Only programs that were already
  running **before** POUNCer started its autostart list are skipped, so a
  second POUNCer start doesn't add 4 more copies.

## v0.7.56 — 2026-10-16 — autostart your other programs

- **Settings > Autostart**: up to 4 programs (.exe, .bat, .cmd, shortcut;
  full path, **Browse...**; arguments after a quoted path), each with its
  own delay - *start after N seconds* (program 1 after POUNCer's start, the
  others after the one before).
- **autostart folder** next to config.ini (made at the first start, with a
  README): everything in it starts after programs 1-4, in name order,
  `folder_delay` (3) seconds apart.
- An .exe that already runs is not started again; a POUNCer restart
  (update, File > Restart) starts nothing again. *File > Start autostart
  programs now* and *File > Open the autostart folder*. The status bar says
  what was started (or not found).
- `[autostart]` in config.ini: `enabled`, `app1`-`app4`, `app1_delay`-
  `app4_delay`, `folder`, `folder_delay`.

## v0.7.55 — 2026-10-16 — new look for the main window

(v0.7.54 had the same new look but still called itself 0.7.53 inside, so
the update check kept offering it - use 0.7.55.)

- **Dark top bar** with the radio frequency large (band, mode, CW speed and,
  in SO2R / SO2V, the other radio's frequency next to it), the **READY /
  CHECK / NOT READY** badge (click: show the check items) and the team
  badge on the right - three rows less than before (team bar, check line,
  N1MM line).
- The check items only show while something is wrong (or after a click
  on the badge).
- Connection lights and *Band open* chips in **one row**.
- N1MM / RBN / Log lines smaller and grey, values in dark bold.
- Flat theme (ttk "clam", Segoe UI 10): the active **sort key filled
  blue**, Radio A/B as an accent key, F-keys bold, **ENTER green**.
- Spot list: taller rows (26 px), light **zebra stripes**, bold grey
  headings, Cont / Mult / Mode / Type centred; same colours as before
  (new mult red bold, worked grey, strong green, tuned-to blue, DX orange).
- A **status bar** at the bottom: the last action and the phone page URL.

## v0.7.53 — 2026-10-16 — no false "Log" warning in the check line

- The check line warned *CHECK: Log ... enable App Info* whenever the open
  contest came from N1MM's ini - also with App Info ticked, when POUNCer
  was started after N1MM had opened the contest (N1MM sends App Info only
  at its start and when a contest is opened or switched). Now it warns
  only when the ini really looks out of date: the QSOs of the last 24
  hours are in **another** contest than the ini names. A freshly opened,
  still empty contest is fine.

## v0.7.52 — 2026-10-16 — counts only the contest open in N1MM

- QSOs per band, worked and mults now always come from the **contest open
  in N1MM**, never from the whole database (which holds many logs).
  POUNCer follows N1MM's **AppInfo** broadcast (database + contest, sent
  at start and on every contest open / switch) and the contest number in
  logged-QSO packets - at once, no waiting. Tick **App Info** in N1MM's
  *Config > Config Ports > Broadcast Data* (127.0.0.1:12060, like Radio).
- Without it the ini is used as before - but N1MM saves its ini only now
  and then, so after opening another contest it could still name the old
  one (that's where e.g. "40m 1000" from last year's contest came from);
  the check line then says *CHECK: Log ... enable App Info*. And if the ini
  names no contest, the contest of the newest QSO is taken - no longer
  every contest in the file.
- The Log line says where the contest came from: *(from N1MM)*, *(from N1MM
  Logger.ini)* or *(newest QSO's contest)*.
- Phone help page (?): how the QSO counts follow the open contest and the
  App Info setting.

## v0.7.51 — 2026-10-16 — band open lights

- **Band open?** A row of band lights in the window (*Band open: 160 80
  40 34 20 12 15 10*) and a dot on every band in the phone's band menu,
  from what the skimmers on **your continent** hear - the whole RBN feed
  (and the HTTP backup / SSB cluster), not only the skimmers of the list -
  in the last 10 minutes:
  - **green + number** = open: that many different DX stations (other
    continents) heard (5 or more),
  - **amber** = spots, but (almost) only your own continent,
  - **grey** = nothing heard.
  The band the radio is on has a frame. A team slave shows the master's
  counts (sent with the team's "hi" message). `[ui] band_lights` (on;
  Settings).

## v0.7.50 — 2026-10-16 — "already tried" marks, pre-contest check line

- **Already tried**: a station you tapped (called) gets a mark after its
  call - in the window `K1ABC   ↻3m`, on the phone a small blue **↻3m** -
  minutes since your last tap on that band; **×2** = tapped twice. So you
  don't keep calling the same pile-up. Shown for 30 minutes after the last
  tap; taps are kept over a restart. Settings / `[ui] mark_tried`
  (on / off) and `tried_minutes`.
- **Pre-contest check line** at the top of the window: **READY** (green) /
  **CHECK** (amber) / **NOT READY** (red) with ✓ / ⚠ / ✗ for N1MM radio
  info, the log, the station call, RBN (also via team or HTTP backup), the
  country file (⚠ older than 60 days), Super Check Partial (⚠ older than
  30 days), the phone page and the **PC clock** (compared with a web
  server's clock every 30 minutes: ⚠ more than 5 s off, ✗ more than a
  minute). The phone shows **⚠ cty** etc. in its top line only when
  something is wrong - tap it for the details. `[ui] check_line` (on).
- **Values in bold** in the window's info lines (the grey part): radio
  frequency, band, mode and CW speed; RBN state, spotter, mode, band,
  spot / hidden counts, team role and IP, SCP and warm-up counts; log
  name, station call, QSOs per band; the phone URL and listening
  address:port. The lines can still be selected and copied.

## v0.7.49 — 2026-10-16 — phone: minimum SNR on the SNR key (hold 1.3 s)

- No extra ≥dB button in the phone's top row any more (too little space):
  **hold the SNR sort key 1.3 s** (it fills teal) for the minimum-SNR sheet
  (all / 3 / 6 / 10 / 13 / 15 / 20 / 25, −1 / +1). A short tap still sorts
  by SNR. When a minimum is set the key shows it small under "SNR" (≥10).

## v0.7.48 — 2026-10-16 — minimum SNR (window and phone)

- **Min SNR** box next to the sort buttons in the window (arrows, mouse
  wheel or type + Enter; **default 0 = off, all spots**): spots weaker than that are not listed -
  in the window **and** on the phone (one setting, saved in config.ini as
  `[ui] min_snr`). The RBN line shows how many weak spots are hidden.
- On the phone a **≥dB** button next to SNR (teal **≥10** when on): tap it
  for all / 3 / 6 / 10 / 13 / 15 / 20 / 25 dB or −1 / +1. The top line
  shows e.g. "12 needed spots (+5 weak)".
- New multipliers are always listed, also when weaker
  (`[ui] min_snr_keep_mults = true`); spots without an SNR (SSB cluster
  spots) are always listed. Both in Settings too.

## v0.7.47 — 2026-10-16 — phone list: only changed tiles are updated

- The phone page no longer throws all tiles away and draws them again every
  second. Each tile (call + MHz) is kept; only what changed is updated - a
  new SNR or time changes that tile's text, a new station adds one tile, a
  gone one removes it, a re-sort only moves tiles. With an unchanged list
  nothing on the page is touched at all (was: 60 tiles rebuilt per second).
- Faster and smoother in a busy contest, no flicker, and the tile under
  your finger stays the same button; a tap always uses the spot's newest
  data (e.g. a slightly moved frequency).

## v0.7.46 — 2026-10-15 — same as v0.7.45, complete release

- v0.7.45 with the macOS builds too (the macOS tests hung on Tk objects
  freed in the web server's thread - fixed in the tests). No app change.

## v0.7.45 — 2026-10-15 — CW keyboard on the phone (to test)

- **Hold ENTER on the phone for 1.3 s** (the key fills up yellow): a **CW
  keyboard** opens at the top of the page. POUNCer presses **Ctrl+K** in
  the radio's Entry Window (A / B) and types what you type, letter by
  letter, into N1MM's CW window, so N1MM sends it as CW (**transmits**).
  Backspace removes letters N1MM hasn't sent yet.
- **DONE** (or Enter on the phone keyboard) = Enter in N1MM: the rest is
  sent and its CW window closes. **STOP (Esc)** = Esc: stops sending at once.
- Safe: POUNCer types only when N1MM's CW window has the keyboard - if
  Ctrl+K didn't open it, nothing is typed (no letters in the callsign box)
  and the phone says so. If you click elsewhere meanwhile, the CW window
  is brought back before the next letters.
- Typed letters go out every 80 ms while the keyboard is open.
- Off with `[web] cw_keyboard = false` (Settings: *Phone CW keyboard*);
  then ENTER is only ENTER.

## v0.7.44 — 2026-10-15 — same as v0.7.43, complete release

- v0.7.43's release got only the Windows exe (a macOS-only test fix
  followed); this one has the exe and both macOS builds. No other change.

## v0.7.43 — 2026-10-15 — N1MM's CW speed shown (to test)

- **CW speed (WPM) from N1MM**: N1MM doesn't broadcast its keyer speed, so
  POUNCer reads it from the speed box of each Entry Window (A and B) once
  a second (Windows `WM_GETTEXT`, nothing sent to N1MM). Shown in the main
  window's N1MM line (`CW 28 WPM`, SO2R / SO2V: `CW A 28 / B 30 WPM`), in
  the compact bar, and on the phone between **WPM −** and **WPM +**.
- **Popup when the speed changes** - from POUNCer's or the phone's WPM − / +,
  PgUp / PgDn in N1MM or anything else: a short **CW 30 WPM ▲** over the
  POUNCer window (1.5 s) and on the phone. The window popup can never take
  the keyboard from N1MM (not activated, clicks go through it).
- Finding the box: the only spin box with a number (5 - 99) in the Entry
  Window; if that isn't clear POUNCer learns it at the first WPM + / −.
  What it found goes to `cache\cwspeed.txt` (with all of the Entry
  Window's controls if nothing was found) - please send it if the speed
  doesn't show.
- New options `[n1mm] cw_speed` and `cw_speed_popup` (both on; Settings).

## v0.7.42 — 2026-10-15 — compact window (to test)

- **View > Compact window (beside N1MM)**: the main window becomes narrow
  (330 × 430) and stays on top next to N1MM: one line with the **Radio A /
  B** button and both frequencies, then the spot list with only frequency,
  call (⚡ / ?) and mult; header, lights, sort row, F-keys and status lines
  hidden. Clicks, arrow keys and Space as always. Untick = the full window
  back at its old size. **View > Always on top** on its own too.
- Compact on / off, its own size and position, and on-top are remembered
  between starts (`cache\window.json`).

## Docs — 2026-10-15

- **docs/NETWORK.md**: every connection POUNCer opens or listens on - N1MM
  (UDP 12060 / 13064, files, Windows messages), RBN telnet, the telnet
  backup, RBN over HTTP, the SSB DX cluster, team (UDP 12079), the phone
  page API (HTTP 8080) and the downloads - with ports, message formats and
  examples; firewall summary. **Network diagram** (SVG + PNG) there and in
  the README.
- README: the configuration table now lists **every** config.ini option,
  by section, with its default.

## v0.7.41 — 2026-10-15 — HTTP RBN backup, spot list colours and keys, busted calls, SCP updates

- **RBN over HTTP** as a second backup (`[rbn] http_backup`, S53ZO's Azure
  feed `/cors/rbn/live`): starts after a minute without a working telnet
  feed (not logged in, or no lines for 2 minutes), stops a minute after
  telnet works again. No login, so no clash with the SSB cluster; first
  asks for the last 10 minutes, then only what is new (cursor), so a
  dropout is caught up. Same skimmer filter; light "RBN via HTTP".
- **Spot list in the exe**: new mult = bold red, worked = grey, **⚡** = rare
  CQ, **?** = not in SCP. **Arrow keys** move, **Space** taps the selected
  spot (QSY + call); the selection stays while the list updates.
- **Busted-call marks**: a call not in Super Check Partial gets a **?**
  (main window and phone tile, explained in the tooltip and help page).
- **SCP updates from the web**: MASTER.SCP is downloaded from
  supercheckpartial.com when the copy in `cache` is missing or older than a
  week (`[scp] refresh_days`); a broken download never replaces a good list;
  until then N1MM's own MASTER.SCP is used. `[scp] update_n1mm = true` also
  refreshes N1MM's copy. On the Settings page / window.

## v0.7.40 — 2026-10-15 — run % per band, hours from logs without CAT, window memory

- **Run % per band.** A station that runs on 20 m but only S&Ps on 80 m now
  shows its **80 m** run % on an 80 m spot (and so gets the ⚡). Used when
  its logs have at least 20 measurable QSOs on that band, else the overall
  run % as before. Warm-up lists (CW and SSB) rebuilt with a per-band
  column; live lookups do the same. Tooltip: "runs 12% of QSOs on 80m".
  A warm-up list in the cache from before (no per-band data) is downloaded
  again at once - no need to delete `warm.sqlite` by hand.
  Lists built 2026-10-15: **CW 46 044** stations (7 713 of them hours
  only), **SSB 60 917** (14 401 hours only); ~5 100 CW and ~5 500 SSB
  stations run 40+ points more on one band than another.
- **Hours from logs without CAT.** Logs that give only the band edge as
  frequency (many SSB logs) used to be dropped completely; now they still
  give the **hours on the air** (~h), only the R% stays empty. The ⚡ sort
  puts such stations after the ones with a known run %.
- **The main window remembers** its size and position (maximised too),
  column widths and sort between starts (`cache\window.json`), moved back
  onto the screen if that monitor is gone. Saved when you close POUNCer or
  it restarts (menu, settings, update).

## v0.7.39 — 2026-10-14 — menu bar and settings window in the exe

- **Menu bar** in the main window instead of the long row of buttons:
  **File** (open the config folder, Reconnect log, Restart POUNCer, Exit),
  **View** (Also enter callsign, Hide worked, Clear spots, Contest summary,
  Phone page in the browser), **Settings...**, **Help** (what every button
  does, check for a new version, POUNCer on GitHub, About). Sort, A / B and
  the F-keys stay as buttons; the spot list gets the room.
- **Settings window** (Settings...): the same settings as the browser's
  Settings page, on tabs - Tap -> N1MM, SO2R / SO2V, RBN, SSB, Program.
  Tick boxes and lists save at once, numbers and text with Enter or when
  leaving the box; "Saved - in use now" / "used after a restart" (↻) under
  each, **Restart POUNCer** button when needed. Works without the phone view
  too. Both use the same list and the same save, so they always agree.

## v0.7.38 — 2026-10-14 — after radio B, focus back to A (option)

- New setting **After radio B: focus back to A** (Settings page, SO2R /
  SO2V; `[n1mm] focus_back_to_a`, default off): every tap or -/+ for radio
  B - QSY and call into Entry Window B as before - ends by bringing Entry
  Window A to the front, so the keyboard (and N1MM's focus) is on A again,
  wherever the cursor was before. Off = "Cursor back where it was" as
  before. Used at once, no restart. Status line: "[focus back on A]".

## v0.7.37 — 2026-10-14 — radio B: udp-left is the default

- Tested on S55OO's SO2V: **udp-left** (N1MM's UDP QSY for radio 2 with
  mouse button Left, nothing typed) moves VFO B and the call lands
  reliably - it is now the default for `[n1mm] radio_b_qsy`. `type` and
  `udp` stay as fallbacks on the Settings page.

## v0.7.36 — 2026-10-14 — radio B QSY option "udp-left" (to test)

- New choice for **How radio B is QSYed** (Settings page, or `[n1mm]
  radio_b_qsy`): **udp-left** = N1MM's UDP QSY command for radio 2 with
  mouse button **Left**, nothing typed - the way S53ZO's SP_Helper tunes
  radio 2. Our "udp" sent mouse button Right (as N1MM's docs say), which
  moved VFO A on S55OO's SO2V. The **call** still goes into Entry Window B
  as now: after N1MM reports the new frequency, in one go, checked, and kept
  in the box if N1MM's QSY wipe comes late (now for every radio B mode).
- Choices: `type` (default, frequency typed + Enter), `udp-left`, `udp`
  (Right). Switch on the Settings page - used at once, no restart - and keep
  the one that works best.

## v0.7.35 — 2026-10-14 — separate run history for SSB contests

- **SSB warm-up list.** Run % and hours on the air for SSB contests now come
  from SSB logs only: a second list, `warm-ssb.sqlite`, built from **CQWW
  SSB, ARRL DX SSB and CQ WPX SSB** (last 5 years, submitted +
  reconstructed logs), downloaded at start next to the CW one. A station
  that runs all weekend on CW but only S&Ps on SSB is marked by its SSB
  habits on an SSB spot. Live lookups were already per mode.
- **Logs without CAT no longer look like a 100% runner**: many (mostly SSB)
  logs give only the band edge (28000, 14000 ...) as frequency; those QSOs
  are left out of the run %. Applies to the CW list too (rebuilt), and the
  per-call cache starts fresh (`runhist4.json`).
- Lists built 2026-10-14: **CW 39 404 stations** (was 46 044 - the rest had
  only band-only logs), **SSB 47 933 stations**, from 117 372 submitted +
  40 326 reconstructed SSB logs (CW: 100 146 + 32 844), 2021-2026.
- Main window: "Warm-up: CW n + SSB m stations known in advance".

## v0.7.34 — 2026-10-13 — radio B frequency typed key by key again

- The radio B tap code was the same from v0.7.24 to v0.7.31; the one change
  around the time B worked best was v0.7.24, which put B's **frequency**
  into N1MM's box in one go (WM_SETTEXT) instead of typing it. B's
  frequency is now **typed key by key again, as up to v0.7.23** (clear the
  box, type e.g. 14025,3, Enter, 0.3 s). Only the **call** still goes in in
  one go with the check (no "PHB9CBR/P"), plus v0.7.33's guard if N1MM's
  QSY comes late. Radio A unchanged.

## v0.7.33 — 2026-10-13 — radio B: frequency first, then the call

- v0.7.32 was too quick on radio B: the call went in before N1MM had moved
  the radio, and N1MM's wipe on the QSY then took it out again. Radio B
  waits again until N1MM reports the new frequency (at most
  `qsy_wait_ms`, then `qsy_settle_ms`) before the call goes in - about
  1.2 s per tap. The fixed 0.3 s after Enter stays replaced by "until N1MM
  took the frequency out of the box".
- If N1MM's report comes later than the wait, POUNCer now keeps watching
  the call box for up to 1 s more and puts the call in again if N1MM wiped
  it - so the call stays even on a slow PC.

## v0.7.32 — 2026-10-13 — radio B tap fast again

- **A tap for radio B was slow** (about 2.5 s) since v0.7.21: after typing
  the frequency into Entry Window B, POUNCer waited up to 1.5 s for N1MM's
  frequency report plus 0.25 s, and a fixed 0.3 s before that. Now it waits
  only until N1MM has taken the frequency out of the box (usually a few
  tens of ms), then at most 0.4 s for the report and 0.1 s more - about
  0.6 s per tap, like v0.7.18. The call check (put the call in again if
  N1MM's wipe came late) stays. Radio A unchanged.
- The status line shows how long the tap took, e.g. "(0.6 s)".

## v0.7.31 — 2026-10-13 — diagnostic log removed

- The **Diagnostic log** from v0.7.26 is removed: no **Diagnostics** button
  in the main window, no "Diagnostic log" in the phone's band menu, no
  `/diag` page and no `cache\diag.log` any more (an old file can simply be
  deleted). Taps, QSY and putting the call in work exactly as before.

## v0.7.30 — 2026-10-13 — self-test removed

- The **Self-test** button and window from v0.7.29 are removed again (no
  extra checks run). The Diagnostics log stays as before.

## v0.7.29 — 2026-10-13 — pre-contest self-test

- **Self-test** button in the main window (exe only, not on the phone):
  checks before the contest what otherwise shows up in the first hour -
  N1MM radio info (A / B, frequency, mode, Entry Window), the N1MM log,
  the callsign, the RBN feed and whether RBN answered **SH/DX** at login,
  whether the **RBN, backup and SSB servers** answer (connect and read
  the greeting only, no login, so the running connection is not
  disturbed), the **same login call** on the backup and SSB cluster (tip:
  `[ssb] login = S55OO-2`), the typed frequency's **decimal sign**, and
  **TEST** put into Entry Window A / B and read back (what was in the
  box is put back).
- Optional **QSY test**: each radio 1 kHz up and back (radio A by UDP,
  radio B typed), with how long N1MM took to report it.
- Each line OK / CHECK / FAIL, a summary, **Copy result**; also in the
  diagnostic log. Taps wait while it runs. Nothing transmits.

## v0.7.28 — 2026-10-13 — SSB

- **SSB contests.** RBN skimmers don't decode SSB, so SSB spots come from a
  **DX cluster** (human spots): **azure.s53m.com:8000**, only spots from
  **spotters in Europe**. The mode is guessed from the spot comment
  (SSB / USB / LSB / CW / FT8 ...) or else the band plan (phone segment).
- **The list follows the radio mode**, per radio: radio on USB / LSB = SSB
  spots, on CW = the RBN CW spots as before - so in SO2R radio A can be on
  CW and radio B on SSB. On SSB the WPM keys are hidden.
- The cluster is connected only while a radio is on SSB (and on a team
  master, which passes the SSB spots on to the others; 15 min after the
  last SSB). It gets the last 30 spots at start (SH/DX). Light **SSB
  cluster** in the main window; events in the diagnostic log.
- `[ssb] mode = auto|always|off`, `host`, `port`, `spotters = EU` (or ALL /
  NA / ...), `history`, `login` (e.g. S55OO-2 if the cluster already has
  you as the RBN backup), `commands`, `follow_radio_mode`; also on the
  Settings page.

## v0.7.27 — 2026-10-13 — settings page

- **Settings page** (`/settings`: **Settings** button in the main window,
  "Settings" in the phone's band menu): the most-used settings changed in
  the browser and written straight into this POUNCer's config.ini - only
  that line, all comments stay. Tap -> N1MM (wait times, settext / keys,
  wipe, focus), SO2R (radio B QSY, decimal sign, cursor back, radio at
  start), RBN (skimmer, SH/DX at start, backup server / port / commands),
  auto-update. Most are used at once; the ones marked ↻ after a restart -
  **Restart POUNCer** button on the page. Bad values are refused.
- Changes only from the browser on the PC itself (each PC its own); from a
  phone / other PC read-only unless `[web] remote_settings = true`.
  Every change is in the diagnostic log.

## v0.7.26 — 2026-10-13 — diagnostic log

- **Diagnostic log** (`/diag`: "Diagnostic log" in the phone's band menu,
  **Diagnostics** button in the main window): what POUNCer did, step by
  step. Each tap is one line with timings (+ms after the tap): Entry
  Window focus, QSY (UDP or the typed frequency, e.g. `"14000,1"+Enter`),
  when N1MM reported the new frequency (or that it didn't), wipe, the call
  put in and what the callsign box really showed (and if it was put in
  again), cursor back. Also F-keys / Enter, RBN connection changes (incl.
  the backup), the N1MM radios, and the settings at start (version,
  Windows decimal sign, type_mode, waits, ...). **Copy all** button to send
  it on. Last 500 lines; also in `cache\diag.log` (kept under 2 MB).

## v0.7.25 — 2026-10-13 — backup telnet server (azure.s53m.com:8000)

- **Backup when RBN's telnet dies:** after RBN's own telnet failed twice in
  a row (no connection / dropped) or sent no spots for 2 minutes, POUNCer
  connects to the backup server **azure.s53m.com:8000** (same login, same
  S53WW filter, same SH/DX at start). The light shows **RBN via backup**.
  Every 10 minutes RBN itself is tried again.
- `[rbn] backup_host`, `backup_port`, `backup_commands` (sent after login,
  e.g. `set/skimmer`); empty `backup_host` = no backup.
- Spot lines right after a prompt without a newline are read too.

## v0.7.24 — 2026-10-13 — calls go into N1MM in one go

- **Fix: long calls sometimes arrived wrong in N1MM** (e.g. "PHB9CBR/P"
  instead of "HB9CBR/P"), more often on a slow PC. The call was typed key
  by key; v0.7.21's check then read the box while a slow PC was still
  typing, cleared it and typed again - the keys still waiting got mixed in.
  Now the call (and radio B's frequency) is **put into N1MM's box in one
  go** (WM_SETTEXT, cursor at the end): nothing can get mixed up, however
  slow the PC. Then POUNCer reads the box and puts it in again if N1MM's
  wipe came late (up to 3 times). The extra Alt+W key wipe is gone (N1MM's
  {WIPE} command does it).
- `[n1mm] type_mode = settext` (default) or `keys` (typed as before, no
  re-typing).

## v0.7.23 — 2026-10-13 — last spots at start (SH/DX)

- **At start (and after a reconnect) POUNCer asks RBN for the last spots**,
  so there are stations to click right away instead of waiting for new
  spots: `sh/dx/100 spotter=S53WW-#` (your skimmer's last spots), and if
  that gives nothing `sh/dx/30`. They come with their own time, so old
  ones fade / expire as usual. In a team the master passes them on.
  `[rbn] history = 30` (0 = off).

## v0.7.22 — 2026-10-13 — radio B: frequency with the right decimal sign

- **Fix: radio B jumped to a wrong frequency** (e.g. 140001 instead of
  14000.1 kHz) and failed. N1MM reads a typed frequency with the Windows
  number format; on a Slovenian PC the decimal sign is "," so "14000.1"
  was read as 140001. POUNCer now types it with the Windows decimal sign
  ("14000,1"). `[n1mm] freq_decimal = auto` (or force "." / ",").

## v0.7.21 — 2026-10-13 — call typed reliably after the QSY

- **Fix: sometimes the frequency changed but the call didn't arrive in
  the Entry Window.** POUNCer typed the call after fixed short pauses; when
  N1MM / the radio took longer, N1MM's own "clear the call on QSY" wiped it
  again. Now POUNCer **waits until N1MM reports the radio on the new
  frequency** (max `qsy_wait_ms` = 1500), waits `qsy_settle_ms` (250) more,
  then wipes and types the call - and afterwards **reads the callsign box**
  and types the call once more if it isn't there ("[call typed again]" in
  the status line).
- `[n1mm] qsy_wait_ms`, `qsy_settle_ms` in config.ini.

## v0.7.20 — 2026-10-13 — click the Update light to check now

- Main window: the **Update** light ("Up to date (0.7.20) ↻") is clickable -
  it asks GitHub for a new version **right away** (instead of waiting for
  the 6-hour check); a newer exe is downloaded and checked as usual, then
  click the light again (or **Restart**) to start it. macOS: a click on
  "vX available" opens the Releases page.

## v0.7.19 — 2026-10-13 — SO2R: cursor goes back where it was

- **SO2R / SO2V: after a tap (or −/+ Hz) for the other radio the cursor
  returns to where you were** - the same Entry Window, the same box
  (callsign, exchange, ...) and the same cursor position / selection. E.g.
  typing the exchange in Entry Window A, tap a station for radio B on the
  phone: radio B QSYs and gets the call, then the cursor is back in A's
  exchange box. Status line: "[cursor back in Entry Window A]". Only when
  the cursor was in the *other* N1MM Entry Window (not when you were in
  POUNCer's own window or another program). N1MM's green dot (receive /
  keyboard focus) follows the cursor back.
- `[n1mm] return_focus = true` (false = stay in the other radio's window).

## v0.7.18 — 2026-10-13 — SO2R: radio B really QSYs

- **Fix: a tap with radio B chosen put the call into Entry Window B but
  moved radio A.** N1MM's UDP QSY command (radio 2) moved radio A in SO2R.
  Radio B is now QSYed like by hand: POUNCer brings Entry Window B to the
  front, types the frequency in kHz (e.g. `14025.3`) into it and presses
  Enter - N1MM tunes radio B, no transmit - then wipes it and types the
  call. Same for −/+ Hz on B. Radio A unchanged (UDP command).
- `[n1mm] radio_b_qsy = type` (default) or `udp` (the old way).

## v0.7.17 — 2026-10-13 — one band menu for the A / B radio

- Phone band menu: **one row of bands (160-10 m)** again, for the radio
  chosen with the **A / B** key in the header ("Band for radio B"). With A
  it shows that band's spots for radio A, with B for radio B; tap a station
  there and that radio goes to it. The two-row A / B menu is gone.

## v0.7.16 — 2026-10-13 — band menu shows a band, tap QSYs

- **Band menu (phone) no longer QSYs the radio** - it shows **that band's
  spots** for the radio (A or B). Tap a station there and the radio goes to
  it exactly like any spot tap (which works for A and B). Reason: in SO2R
  the band QSY for radio B moved radio A, also with Entry Window B in front.
- Small line: "15m list · B on 20m ✕" while looking at another band - tap
  it to go back. The list follows the radio again as soon as the radio
  changes band (e.g. after the tap). Menu: green = band shown, blue ring =
  band the radio is on. A band in B's row switches the phone to radio B.
- `/api/spots?band=15m` (with `radio`): the list of that band; POUNCer
  builds it on its next tick for as long as a phone asks (10 s).

## v0.7.15 — 2026-10-13 — contest summary, SO2R band fix

- **Contest summary** page (`/summary`, its own page like the help): **Σ**
  next to **?** on the phone page, "Contest summary" in the band menu, and a
  **Summary** button in the main window (opens it in the browser). Shows for
  the open N1MM log: QSOs, QSOs **from POUNCer** (call tapped on that band up
  to 15 min before it was logged) and %, multipliers and mults from spots,
  QSOs / hour on the air (gaps of 30 min+ = off time), best 60 minutes,
  tapped stations worked, a per-band table, rate per hour as bars (POUNCer
  part green) and the list of multipliers worked from spots. Updates every
  minute; Refresh button.
- **Fix (SO2R): band menu and −/+ Hz for radio B moved radio A.** N1MM
  QSYs the radio whose Entry Window has the focus, so - like a spot tap -
  POUNCer now brings Entry Window B to the front first (only with two
  radios; `focus_n1mm`).
- Every tap (tile, NEXT, main window) is kept in `cache\taps.jsonl`
  (14 days) so a restart doesn't lose them. New module `pouncer/summary.py`.

## v0.7.14 — 2026-10-13 — SO2R band menu for both radios

- Phone, SO2R: tapping the frequency opens the band menu with **a row for
  radio A and a row for radio B** (each radio's current band green, "this
  phone" marked), so either radio's band can be changed - also radio B's
  while the phone works A, and the other way round. Payload `rbands`
  {radio: band}. The ▾ menu hint is back next to the bright frequency.

## v0.7.13 — 2026-10-13 — busy-contest test options, A + B frequency

- **Test options** for busy contests (phone page), each on / off under
  *Test options* on the help page (**?**), saved per phone:
  - **Freq from my radio up** (button shows **Freq↑**; tap **Freq** twice
    to switch): the frequency sort starts at the radio frequency instead of
    the band bottom - the station you're on first, then up the band, the
    ones below at the end. The list goes with you as you tune up; NEXT
    walks up the band.
  - **Calm list**: tiles stay where they are instead of reshuffling every
    second. New stations are added at the end (yellow line), gone ones drop
    out, SNR / time change in place; a full re-sort every 15 s, never within
    3 s of touching / scrolling; sort, DX / MLT / ⚡, band or radio changes
    re-sort at once. "calm" in the small line while on.
- **SO2R: both frequencies** in the phone's top row - A with B under it,
  the radio this phone works big and bright (payload `freqs`).

## v0.7.12 — 2026-10-13 — team version check

- **Team version check.** Every POUNCer already announced its version to
  the team; now it is compared. When a team POUNCer runs another version
  than this one, the **Team** light turns **amber** and says which PC is
  on which version (e.g. `Team master (2): 192.168.1.20 on 0.7.9`), the
  team bar at the top turns orange with "VERSIONS DIFFER ... update all to
  the same", and the phone page shows a ⚠ note in its small line.
  A very old POUNCer that doesn't send its version shows as `?`.

## v0.7.11 — 2026-10-13 — SO2R: radio A / B

- **Radio A / B** for SO2R / SO2V. On the phone page a **A / B** key
  (top row, next to the frequency, CLR-sized; only shown when N1MM reports
  two radios - A blue, B orange)
  chooses which radio this phone works: spot taps (QSY + call), F-keys,
  ENTER, −/+ Hz and the band menu go to that radio, and the list follows
  **that radio's band**. Each phone remembers its own choice, so one phone
  can work radio A and another radio B. Main window: **Radio A (A/B)**
  button (clicks, keyboard F-keys and the table follow it).
- How (per N1MM's UDP docs): QSY packet with `radionr` 2 and mouse button
  **Right** for radio 2 ("Left = Radio 1, Right = Radio 2" in SO2V/SO2R);
  typing and keys go to that radio's own Entry Window (`EntryWindowHwnd`
  of its RadioInfo packet - N1MM sends one per radio), and the {WIPE}
  command carries its RadioNr.
- `[n1mm] default_radio = A` (or B) - the radio at start; empty = `radionr`.
  With one radio only A is used, exactly as before.

## v0.7.10 — 2026-10-13 — function keys from the keyboard

- Main window: pressing **F1-F12 on the keyboard** while POUNCer is the
  active window works like in N1MM - POUNCer brings N1MM's Entry Window to
  the front and presses that key there, so the N1MM message is **sent**.
  **Enter** too when it is one of the set keys (`[ui] fkeys`), logging the
  QSO. `[ui] forward_fkeys = true` (false = off).

## v0.7.9 — 2026-10-13 — NEXT button, macOS builds

- **macOS builds** (by S53ZO): every release also has
  `POUNCer-macos-arm64.tar.gz` (Apple Silicon) and
  `POUNCer-macos-x86_64.tar.gz` (Intel), built by the same workflow.
- macOS POUNCer **discovers new versions** too: the Update light turns
  blue with "vX available on GitHub" (download it from the Releases page);
  installing by itself stays Windows-only.

- Phone / browser page: **NEXT ▶** in the footer (right) goes to the next
  station of the list, in the order shown (sort + DX / MLT / ⚡): QSY and
  enter its call, exactly like tapping its tile. It starts after the
  station you tapped last; after the last one it starts at the top again.
  No transmit.
- The **NAR / WIDE** filter buttons are removed (NEXT took their place);
  the `filter_*` lines are gone from config.ini.

## v0.7.8 — 2026-10-13 — (don't use)

- Published by mistake from the commit before the NEXT change: it is
  v0.7.7 inside (and says so). Use v0.7.9.

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
