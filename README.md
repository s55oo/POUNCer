# POUNCer

**Search & pounce helper for N1MM Logger+ contesting.** POUNCer follows the
band your radio is on and shows who is calling CQ there right now - live
**Reverse Beacon Network** spots from a chosen skimmer (default **S53WW**) on
CW, and **DX-cluster** spots on SSB. Each station is marked with what helps you
decide: new multiplier, already worked, and whether it usually runs for hours
or only briefly (from 5 years of contest logs). **One tap** tunes the radio -
radio A or B in SO2R / SO2V - and puts the call into N1MM's Entry Window.
Use it on the PC, or on a phone or tablet next to the radio; several
operators' POUNCers share one feed as a team.

Windows (POUNCer.exe) and macOS; nothing to install - put the exe and
`config.ini` in a folder next to N1MM and start it.

<p align="center"><img src="docs/phone-view.jpg" width="320"
  alt="POUNCer phone page in SO2R on 20 m: radio A and B frequencies, the A / B key, sort and filter buttons, spot tiles and the F-key column"></p>

*The phone page in a contest (SO2R): radio **A** on 14043.0 with **B**
(7013.0) under it, the **A / B** key, CLR / Time / Freq / SNR / DX / MLT / ⚡
sorting, **S** = slave in a team, "backup skimmers" while S53WW was quiet.
Tiles: green = strong (20 dB+), ⚡ = rare CQ, run % and hours from past
logs (R58% ~9h), first heard, SNR and how long it has been calling (cq2m);
yellow frame = the station you tapped last, blue ring = the radio is on it. F-keys on the right, WPM / ±Hz / NEXT at the
bottom.*

## How it works

* **Own call** - taken from the open N1MM log's *Change Your Station Data*
  (follows the log you open); your own spots are never listed, and EU/DX
  colouring is from that call's continent.
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
  are DX: their callsign is **yellow** on the phone page
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
  (banner, the **Log** button next to Sort, or *File > Reconnect log* in the
  main window) re-reads which log N1MM has open immediately.
* **Touch / big-button view** - the phone page in your PC's default browser
  (touch screen, second monitor): *View > Phone page in the browser* in the
  main window opens it (or set `[ui] touch_view = true` to open it at startup).
  The old separate Tk touch window is gone - one view for everything.
* **Phone view (web)** - a small local web server (stdlib only) serves the same
  needed spots as big tap tiles for a phone/browser. Tapping a tile QSYs and
  enters the call on this PC. Open
  `http://<pc-ip>:8080` - the exact URL is shown on the **Phone view** line of
  the main window: the ZeroTier address if ZeroTier is running, otherwise the
  LAN address, plus the port it listens on.
  Binds to all interfaces, so it works over your LAN and over ZeroTier/VPN for
  remote access. LAN/VPN only, no password. **Sort: Time / Freq / SNR** buttons (**CLR** in front clears all spots, with Undo)
  in the top header row reorder the tiles (the choice is remembered in the
  browser); the **DX** button next to them puts DX spots on top, still in that
  order, and **MLT** puts new multipliers on top (double, then single; magenta
  bar on the tile - thick = double). The station your radio is tuned to
  (within 0.5 kHz) has a light-blue outline. **Hold a tile for 2 s** to remove
  that call from the list (with Undo); the station you tapped keeps a yellow
  border until you tap another. The **footer** has **WPM − / WPM +** on the
  left (N1MM PgDn / PgUp = CW speed) and **−25 Hz / +25 Hz** in the middle
  (move the VFO via N1MM; step = `[ui] tune_step_hz`), and **NEXT ▶** on
  the right: the next station of the list in the order shown (after the one
  you tapped last; at the end it starts again at the top) - QSY and call,
  like tapping its tile. None of them transmit.
  The **?** at the end of the status line opens a help page explaining
  every button.
  **cq3m** on a tile = how long that station has been calling on that
  frequency (from the RBN spots, live).
  **Run history:** each tile shows `R81% ~23h` = how often that station ran
  (called CQ) and how many hours it was on the air in its logs of the last 6 years of
  the same contest (else its other contests in that mode), from S53ZO's public contest-log archive (`*` = reconstructed log,
  for stations that never sent one). The **R%** is for the **spot's band**
  when its logs have enough QSOs there (a 20 m runner that only S&Ps on 80 m
  shows its 80 m habit on an 80 m spot), else over all bands. Logs written
  without CAT (no real frequencies) still give the **hours**, just no R%; **⚡** = rare CQ (runs under 30% or on
  under 6 h) - it won't last, work it now
  (`[ui] run_history`, cached in a `cache` folder). The **⚡** header button
  sorts the rarely-running stations to the top. A new call is known in about
  a second (8 looked up at a time; instant once cached).
  **Warm-up:** at start POUNCer downloads (once, refreshed after 30 days)
  ready lists: **CW** - about 39 000 stations from CQWW CW, ARRL DX CW and
  CQ WPX CW - and, **separately, SSB** - the same contests' phone versions
  (CQWW SSB, ARRL DX SSB, CQ WPX SSB, about 48 000 stations), since who runs and for how long is
  different on SSB. Last 5 years, submitted + reconstructed logs, so the
  marks are there the moment a call is spotted; an SSB spot gets the SSB
  numbers (`[ui] warmup`; built by `server/warmlist.py`, release `warmup`).
  Stored as `cache\warm.sqlite` and `cache\warm-ssb.sqlite`;
  `cache\warm-up.txt` / `warm-up-ssb.txt` and the main window say when
  they were downloaded. QSOs logged without CAT (only the band edge, e.g.
  28000) don't count as running.
  Calls not in it are looked up live.
  Or put a ready-made **`runlist.sqlite`** next to `config.ini`
  (`[ui] run_list`) - loads at start, no download.
  Or with the optional **run list server** (`server/`, Docker, e.g. on ZeroTier;
  `[ui] run_server`) the whole CQWW list is loaded at start, so the marks
  are there the moment a call is spotted. See `server/README.md`.
  Confirmations ("Wide filter", "CW faster", Undo after a remove) pop up at
  the top, just under the header, for 5 s (tap to close). On a phone the tiles are compact one-line rows (callsign left,
  details right) so about 10 fit on screen; wider screens get two columns of
  big tiles. The TX/log key buttons sit in a column on the right of the list
  (F2 at the top, each key two rows tall); they are green and turn red when
  pressed.
  The header shows the radio frequency; **tap it to pick a band** (160, 80,
  40, 20, 15, 10 m): the list then shows **that band's spots** - the radio
  stays where it is until you **tap a station** there, which QSYs it as
  usual. "15m list · on 20m ✕" in the small line (tap it to go back to the
  radio's band); once the radio changes band the list follows it again.
* **TX / log buttons** - the main window and the phone page all
  show buttons for `[ui] fkeys` (default `F2:RPRT F3:TU F4:CALL F5:HisCALL F7:? Enter`). An F-key
  button presses that function key in the focused N1MM Entry Window, so N1MM
  sends its message - e.g. F4 normally sends `{MYCALL}`. Unlike tapping a spot,
  **F-keys transmit over the air**. The `Enter` button logs the current QSO in
  N1MM (no transmission). N1MM's documented way to do this from another program
  is to send the keystroke to the Entry Window (`EntryWindowHwnd`). Reorder or
  disable the buttons with e.g. `fkeys = F1:CQ F4:MyCall Enter` (empty = no
  buttons).
* **Keyboard F-keys** - with the POUNCer window active, F1-F12 (and Enter,
  if it is one of the set keys) go straight to N1MM: POUNCer focuses the
  Entry Window and presses the key there, so the message is sent
  (`[ui] forward_fkeys`).

Only the Python standard library is used (tkinter for the GUI). No pip install
is required.

## Main window menu

<p align="center"><img src="docs/main-window.png" width="720"
  alt="POUNCer 0.7.39 main window: menu bar File / View / Settings / Help, team bar, connection lights, N1MM / RBN / Log lines, sort and A/B buttons, F-key row and the spot list"></p>

*The Windows main window (0.7.39): menu bar, team bar, connection lights
(RBN, N1MM, Log, Team, Phone, SSB, Warm-up, Update), the N1MM / RBN / Log
lines, Sort and A/B buttons, the F-key row and the spot list.*

The options and actions are in the menu bar (sort, the A / B key and the
F-keys stay as buttons - they're used all contest long):

* **File** - open the config folder (Explorer), Reconnect log, Restart
  POUNCer, Exit.
* **View** - *Also enter callsign in N1MM* and *Hide worked* (tick), Clear
  spots, Contest summary, Phone page in the browser.
* **Settings...** - the settings window (below).
* **Help** - what every button does, check for a new version, POUNCer on
  GitHub, About.

The window remembers its size and position (maximised too), the column
widths and the sort for the next start (`cache\window.json`; delete it to
start with the defaults). A position on a monitor that is no longer there
is moved back onto the screen.

## Connection lights (Windows window)

Under the MASTER / SLAVE bar a row of lights shows at a glance that
everything is connected:

| Light | Green | Amber | Red | Grey |
|---|---|---|---|---|
| RBN | logged in / spots coming (slave: via master) | connecting | disconnected | - |
| N1MM | radio info coming in | none for 2 min | never received | - |
| Log | contest log open | checking | no log / N1MM not running | - |
| Team | master or slave (count) | alone on the network, or a team POUNCer runs another version (says which IP / version) | - | team off |
| Phone | web view running | - | port in use | disabled |
| Warm-up | list loaded | downloading | download failed | off |
| Update | up to date | checking / check failed | - | off (blue = new version ready: **Restart** button) |

**Auto-update:** click the **Update** light ("Up to date (…) ↻") to check
GitHub right now - a new version is downloaded, then click it again (or
**Restart**) to start it. Otherwise the exe checks GitHub at start and every 6 h; a newer
release is downloaded, its SHA-256 checked, and it replaces POUNCer.exe on
disk. It runs from the next start (or press **Restart -> vX**); POUNCer
never restarts by itself in the middle of a contest. `[ui] auto_update`.

## Two radios (SO2R / SO2V)

When N1MM runs two radios (it then has Entry Windows **A** and **B**), the
phone page shows an **A / B** key in the top row, next to the frequency
(CLR-sized; A blue, B orange). It
chooses which radio that phone works: taps (QSY + callsign), F-keys,
ENTER, −/+ Hz and the band menu go to that radio's Entry Window, and the
spot list follows that radio's band. Every phone keeps its own choice -
e.g. one phone for radio A, a second one for radio B. In the main window
the **Radio A (A/B)** button does the same for clicks and keyboard F-keys.
`[n1mm] default_radio = A` / `B` sets the start radio. Radio B is
QSYed with N1MM's UDP command for radio 2 with mouse button **Left**,
nothing typed (`[n1mm] radio_b_qsy = udp-left`, as SP_Helper does; with
"Right", as N1MM's docs say, it moved radio A on an SO2V). Fallbacks:
`type` = the frequency typed + Enter into Entry Window B, `udp` = the UDP
command with Right. Either way the call then goes into Entry Window B
after N1MM reports the new frequency. After a tap
for the other radio the cursor goes back to where it was - same Entry
Window, same box (callsign / exchange) and cursor position
(`[n1mm] return_focus = true`). Or, with **After radio B: focus back to
A** (`[n1mm] focus_back_to_a = true`, Settings page), every tap or -/+ for
radio B ends with the cursor in Entry Window A, wherever it was - you keep
running on A while B pounces. With one radio
nothing changes. The frequency in the top row then shows **both radios** -
A with B under it; the radio this phone works is the big one. Tapping it
opens one band menu (160-10 m) for the radio chosen with the **A / B** key:
with A it shows that band's spots for radio A, with B for radio B (green =
the band shown, blue ring = the band the radio is on). Tap a station there
and that radio goes to it.

## Settings

**Settings...** in the main window's menu bar opens the settings window: the
most-used settings on tabs (*Tap -> N1MM*, *SO2R / SO2V*, *RBN*, *SSB*,
*Program*) - wait times, settext / keys, radio B, focus back to A, decimal
sign, cursor back, skimmer, backup server, SSB cluster, auto-update. A tick
box or list is saved at once, a number or text with Enter (or when you leave
the box), straight into this POUNCer's config.ini (only that line; comments
stay). Most work at once, the ones marked ↻ after **Restart POUNCer**.

The same settings are on the **Settings** page in the browser (the phone's
band menu, `/settings`) - only from the browser on the PC itself unless
`[web] remote_settings = true`.

## SSB

RBN has no SSB, so for SSB POUNCer uses a **DX cluster** (human spots,
default azure.s53m.com:8000, only spotters in Europe). The list follows the
radio: on USB / LSB it shows SSB spots, on CW the RBN CW spots - per radio,
so SO2R with one radio on CW and one on SSB works. The cluster is connected
only while needed (light "SSB cluster"). `[ssb]` in config.ini or the
Settings page.

## Backup when RBN is down

If RBN's telnet fails twice in a row or sends nothing for 2 minutes,
POUNCer switches to the backup telnet server **azure.s53m.com:8000** (light:
"RBN via backup") and tries RBN again every 10 minutes. `[rbn] backup_host`
/ `backup_port` / `backup_commands`; empty `backup_host` = off.

## Spots at start

Right after logging in to RBN POUNCer asks for the **last spots**
(`sh/dx/100 spotter=S53WW-#`, or `sh/dx/30` if that gives nothing), so the
list has stations to click straight away. `[rbn] history = 30` (0 = off).

## Contest summary

**Σ** on the phone page (next to **?**, also in the band menu) or the
*View > Contest summary* in the main window opens a summary of the log N1MM has
open: QSOs and how many came **from POUNCer** (you tapped that call on that
band - tile, NEXT or main window - up to 15 minutes before it was logged),
multipliers and how many came from spots, QSOs per hour on the air, best
60 minutes, a per-band table, rate per hour (bars) and every multiplier
worked from a spot. Taps are kept in `cache\taps.jsonl` for 14 days.

## Busy contests: test options (phone page)

Two options to try, on / off under **Test options** on the help page (**?**),
saved per phone:

- **Freq from my radio up** (**Freq↑**; also: tap **Freq** twice) - the
  frequency sort starts at the radio frequency: the station you're on first,
  then the next ones up the band, the ones below you at the end. As you tune
  up the list goes with you, and **NEXT** walks up the band.
- **Calm list** - tiles keep their place instead of jumping every second:
  new stations are added at the end (yellow line on top), gone ones drop out,
  SNR / time update in place. The list is re-sorted every 15 s, never within
  3 s of a touch or scroll; changing the sort, DX / MLT / ⚡, band or radio
  re-sorts at once. "calm" starts the small line while it is on.

## Several POUNCers (team)

Running one POUNCer per band / PC on the same network - LAN or ZeroTier? They
find each other by themselves (UDP broadcast on every network adapter, port
12079; `[team] peers` = extra IPs if a network drops broadcasts) and work as
a **team**:

- the one **running longest** (with a working RBN connection) is the
  **master** - the only one connected to RBN; it passes the spots to the
  others (**slaves**), so there is one RBN connection for the whole station;
- whatever run history one learns is shared with all - each call is
  downloaded from GitHub only **once**; a POUNCer started later gets
  everything known so far from the master;
- if the master is switched off (or its RBN is down over a minute) the next
  one takes over within seconds.

The main window shows a coloured bar at the top - **MASTER** (green) or
**SLAVE** (blue, with the master's IP) - and the role in its title; the phone's status line starts with **M** or **S**. Allow UDP 12079
in the Windows firewall on each PC. `[team] enabled = false` = stand-alone.
Give them all the same `[rbn] spotter` and modes.

## Reading a tile (phone view)

```
 M0KTZ ⚡          7003.1 R6%* ~4h
                   1132Z 22dB cq3m
```

| Part | Meaning |
| --- | --- |
| **Green** tile | strong on the skimmer: 20dB or more; **grey** = weaker |
| **Yellow** call | DX (outside your continent); white = your own continent |
| **Magenta** bar on the left | new multiplier on this band (thin = single, thick = double) |
| **Blue** outline | your radio is on that frequency (within 0.5 kHz) |
| **Yellow** ring | the tile you tapped last |
| **7003.1** (bold) | spot frequency, kHz; the mode is shown only when it isn't CW |
| R6% / ~4h / `*` | run history: % of QSOs made running (on this band when known) and hours on the air in its logs of the last 6 years of this contest; `*` = reconstructed log |
| ⚡ | rare CQ: runs under 30 % or on under 6 h - work it now |
| 1132Z | first spotted on this frequency (UTC) |
| 22dB | skimmer SNR |
| cq3m | calling on this frequency for 3 minutes (from RBN) |

A **faded** tile = not re-spotted for 5 min (probably gone). When a rare CQ
(⚡) that is also a **new multiplier** appears, the phone beeps (alerts on/off
in the band menu). If S53WW goes quiet for 5 min, other skimmers on your
continent are used ("⚠ backup skimmers") until it is back.

Tap = QSY + call into N1MM (no TX; the callsign box is always emptied first); hold 2 s = remove from the list. The
**?** after the status line opens the same explanation on the phone.

## Download

Get **POUNCer.exe** and **config.ini** from the
[Releases page](https://github.com/s55oo/POUNCer/releases) and put both in the
same folder. Release builds come from GitHub Actions with the PyInstaller
bootloader compiled from source, so Windows Defender doesn't flag them
(locally built exes can be flagged `Trojan:Win32/Wacatac.B!ml` - a false
positive). Each release lists the exe's SHA-256.

For macOS, download **POUNCer-macos-arm64.tar.gz** (Apple Silicon) or
**POUNCer-macos-x86_64.tar.gz** (Intel) from the same release. Extract the
archive, keep `POUNCer` and `config.ini` together, and run `./POUNCer` from
Terminal in that folder. The archives preserve executable permissions and
include an editable config; matching `.sha256` files are published alongside
them. These builds are unsigned and are not notarized. Installing updates by
itself is Windows-only; the macOS POUNCer turns its Update light blue ("vX
available on GitHub") when a new version is out - download it from the
Releases page.

The **Build POUNCer (Windows and macOS)** GitHub Actions workflow also makes
both macOS builds available as artifacts when run manually without a release
tag. N1MM Logger+ still runs on Windows; configure its broadcast destination
to the Mac's IP address when using POUNCer on another computer. Local Windows
keyboard and window controls are unavailable on macOS.

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
| `[ui] touch_view` | Open the phone page in the default browser at startup (`true`/`false`). |
| `[ui] fkeys` | TX/log buttons in the main window and phone page: `KEY[:label]` list (default `F2:RPRT F3:TU F4:CALL F5:HisCALL F7:? Enter`). Each F-key **transmits** its N1MM message; `Enter` logs the QSO. Empty = no buttons. |
| `[web] enabled` | Serve the phone/browser view (`true`/`false`, default `true`). |
| `[n1mm] default_radio` | SO2R: radio at start, `A` (radio 1) or `B` (radio 2); empty = `radionr`. Switch on the phone (per phone) or with the main window's A/B button. |
| `[web] host` | Interface to bind (`0.0.0.0` = all, including ZeroTier). |
| `[web] port` | Port for the phone view (default 8080). |

## Tests

```
python -m unittest discover -s tests -v
```
