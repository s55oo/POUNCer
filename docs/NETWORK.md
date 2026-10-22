# POUNCer – network connections and protocols

Every connection POUNCer opens or listens on, what goes over it and in which
format: N1MM Logger+, the Reverse Beacon Network, the DX cluster, the phone
page, the team, and the downloads from the internet. Valid for **v0.7.71**.
Ports and addresses are the defaults; each can be changed in `config.ini`
(the section and key are given).

No connection uses a password. POUNCer logs in to RBN and the DX cluster
with **your callsign only**, and sends no personal data anywhere. Addresses
in the examples are private example addresses.

![POUNCer network connections: N1MM on UDP 12060 / 13064, Windows messages and files on the shack PC; RBN telnet, the Azure backup / DX cluster / HTTP feed, GitHub and Super Check Partial on the internet; phones on HTTP 8080 and other POUNCers on UDP 12079 on the LAN](network-diagram.svg)

*(Also as [PNG](network-diagram.png).)*

## Overview

| # | Direction | Transport | Port | Other end | What for | config.ini |
|---|---|---|---|---|---|---|
| 1 | **in** | UDP | 12060 | N1MM Logger+ (same PC) | radio frequency, mode, Entry Window (RadioInfo XML); the open log + contest (AppInfo XML; contest number also from contactinfo / lookupinfo); N1MM's cluster spots (spot XML, SSB) | `[n1mm] listen_ip`, `listen_port` |
| 2 | **out** | UDP | 13064 | N1MM Logger+ (127.0.0.1) | QSY, wipe, CAT strings (XML commands) | `[n1mm] control_ip`, `control_port` |
| 3 | local | files | – | N1MM's log `.s3db` + `-wal`, `N1MM Logger.ini`, `wl_cty.dat`, `MASTER.SCP` | worked stations, mults, contest, callsign, countries | `[n1mm] ini`, `[worked] db` |
| 4 | local | Windows messages | – | N1MM Entry Windows | bring to front, put the call in, read it back, keys; read the CW speed box (WPM); Ctrl+K + typed text for the phone CW keyboard | `[n1mm] type_mode`, `cw_speed`; `[web] cw_keyboard` |
| 5 | **out** | TCP (telnet) | 7000 / 7001 | telnet.reversebeacon.net | RBN skimmer spots (CW/RTTY 7000, FT8/FT4 7001) | `[rbn] host`, `port`, `ft8_port` |
| 6 | **out** | TCP (telnet) | 8000 | azure.s53m.com | RBN backup when RBN's telnet is down | `[rbn] backup_host`, `backup_port` |
| 7 | **out** | HTTPS | 443 | azure.s53m.com `/cors/rbn/live` | RBN over HTTP, second backup | `[rbn] http_backup` |
| 8 | **out** | TCP (telnet) | 8000 | azure.s53m.com | DX cluster: SSB spots | `[ssb] host`, `port` |
| 8b | **out** | TCP (telnet) | 8000 | azure.s53m.com (`[ssb] self_spot_host`) | spots of MY call only (node filter `accept/spots call <call>`, login `<call>-77`, team: the master only) for the SPOTTED pop-up | `[ssb] self_spot_host`, `self_spot_port`, `self_spot_filter` - the log says once whether the node applies the filter ("self-spot feed: the node filter works" / "does NOT apply") |
| 9 | **in** + out | UDP broadcast | 12079 | other POUNCers on the network | team: who is master, who needs RBN, spots, run history; the master's band-open counts | `[team] port`, `peers` |
| 10 | **in** | HTTP (TCP) | 8080 | phones, tablets, browsers on the LAN; other POUNCers | phone page, its API, settings, team sync | `[web] host`, `port` |
| 11 | **out** | HTTPS | 443 | api.github.com, github.com | update check and download (SHA-256 checked); clock check fallback: `HEAD api.github.com` (`Date` + `Age`) only if no NTP server answers | `[ui] auto_update`; clock: `[ui] check_line` |
| 12 | **out** | HTTPS | 443 | github.com (release `warmup`) | warm-up lists (CW, SSB) | `[ui] warmup` |
| 13 | **out** | HTTPS | 443 | raw.githubusercontent.com (S53ZO's log archive) | run history of calls not in the lists | `[ui] run_history` |
| 14 | **out** | HTTPS | 443 | www.supercheckpartial.com | MASTER.SCP (busted-call marks) | `[scp] url` |
| 15 | – | UDP connect | – | 8.8.8.8 | finding the PC's LAN address – **nothing is sent** | – |
| 17 | **out** | UDP (NTP) | 123 | pool.ntp.org, then time.windows.com | PC clock check every 30 min (SNTP, 48 bytes, no data of yours) | `[ui] check_line` |
| 18 | **out** | MQTT (TCP) | 1883 | mqtt.pskreporter.info | FT8 or SSB? one call's PSK Reporter reports for up to 2 min (only for a spot on 7074 - 7077 / 1840 - 1843 without a mode) | `[ssb] pskreporter` |
| 16 | local | process start | – | your own programs (.exe / .bat / shortcuts) | autostart with POUNCer – no network | `[autostart]` |

Firewall: inbound **UDP 12060** (only from N1MM on the same PC), **TCP 8080**
(phones on the LAN) and **UDP 12079** (team) must be allowed; everything
else is outgoing.

---

## 1. N1MM → POUNCer: RadioInfo (UDP 12060, in)

N1MM broadcasts one XML datagram per radio whenever something changes (and
regularly). Set it up in N1MM: *Config → Config Ports, Mode Control,
Winkey, etc. → Broadcast Data*: tick **Radio**, address `127.0.0.1:12060`.
POUNCer listens on `0.0.0.0:12060` (UTF-8 XML, one packet = one radio).

On the same port POUNCer also reads **AppInfo** (tick *App Info* in the same
dialog, `127.0.0.1:12060`) - sent at start and when a contest is opened or
switched - and the contest number in **contactinfo / contactreplace /
lookupinfo** packets (*Contacts*, *Lookup*). They name the open log and
contest, so only that contest is counted (the ini can be out of date):

```xml
<AppInfo><app>N1MM</app><dbname>S55OO.s3db</dbname><contestnr>8</contestnr>
  <contestname>CQWWCW</contestname><StationName>PC</StationName><mycall>S55OO</mycall></AppInfo>
```

**Spots** (tick *Spots*, same address): every spot of N1MM's Telnet window.
While they come (from the first one, then at most 20 s apart) POUNCer uses the SSB
ones and closes its own DX cluster connection (`[ssb] use_n1mm_spots`):

(N1MM writes the frequency with the Windows decimal sign - `21035,56` on a
Slovenian Windows; both are read.)

```xml
<spot><app>N1MM</app><dxcall>K1ABC</dxcall><frequency>14205.0</frequency>
  <spottercall>S50XYZ</spottercall><comment>59 cq</comment><action>add</action>
  <mode>USB</mode><timestamp>2026-10-24 12:00:00</timestamp></spot>
```

```xml
<RadioInfo>
  <StationName>SHACK-PC</StationName>
  <RadioNr>2</RadioNr>
  <Freq>701250</Freq>          <!-- tens of Hz: 701250 = 7012.50 kHz -->
  <TXFreq>701250</TXFreq>
  <Mode>CW</Mode>
  <ActiveRadioNr>1</ActiveRadioNr>
  <EntryWindowHwnd>2222</EntryWindowHwnd>
  ...
</RadioInfo>
```

Used fields: `RadioNr` (1 = A, 2 = B; in SO2V 2 = VFO B), `Freq` / `TXFreq`
(→ kHz → band to follow), `Mode` (USB/LSB = the list shows SSB spots),
`ActiveRadioNr`, `StationName`, `EntryWindowHwnd` (the window handle of that
radio's Entry Window – POUNCer brings exactly that window to the front).
After a QSY POUNCer waits until RadioInfo reports the new frequency
(±0.15 kHz, at most `qsy_wait_ms`) before it puts the call in. Other XML
packets on the port are ignored.

## 2. POUNCer → N1MM: commands (UDP 13064, out)

UTF-8 XML datagrams to `127.0.0.1:13064` (N1MM's control port). None of
them transmits.

**QSY** – documented by N1MM (`radio_setfrequency`):

```xml
<?xml version="1.0" encoding="utf-8"?>
<radio_setfrequency>
  <app>POUNCer</app>
  <radionr>2</radionr>
  <frequency>7012.500</frequency>      <!-- kHz -->
  <mousebutton>Left</mousebutton>
</radio_setfrequency>
```

- Radio A: `radionr` 1, `mousebutton` Left.
- Radio B (`[n1mm] radio_b_qsy`): **udp-left** (default) = `radionr` 2 with
  **Left** – works on SO2V; **udp** = `radionr` 2 with Right (as N1MM's
  documentation says; on an SO2V it moved VFO A); **type** = no packet:
  the frequency is typed into Entry Window B + Enter (section 4).
- Also used by the band menu and the −/+ Hz buttons.

**Radio command** – `RadioCmd`, not in N1MM's documentation but accepted on
the same port (also used by other N1MM control programs). Runs an N1MM
macro as if typed in that radio's Entry Window:

```xml
<RadioCmd><RadioNr>2</RadioNr><FocusEntry>2222</FocusEntry>
<RadioCommand>{WIPE}</RadioCommand></RadioCmd>
```

- `{WIPE}` – clear the Entry Window before a new call.
- `{CAT1ASC …}` / `{CAT2ASC …}` – a raw CAT string to the radio, e.g. the
  RX filter width (`[ui] filter_narrow_cat`, `filter_wide_cat`).

## 3. N1MM's files (local, read only)

Not network, but part of working with N1MM – read, never written (except
`MASTER.SCP` if `[scp] update_n1mm = true`):

- **`N1MM Logger.ini`** (Documents\N1MM Logger+) – which log (`.s3db`) and
  contest are open, the station call. Re-read every `[worked] refresh_seconds`.
- **The log `*.s3db`** (SQLite, read-only) – table `DXLOG`: `Call`, `Band`,
  `Mode` and the contest's mult fields, only for the open contest
  (`ContestNR`); table `Station`: our call. N1MM writes new QSOs into the
  `-wal` side file; POUNCer watches both every 0.5 s, so a logged station
  disappears from the list within about half a second. Worked = same call
  **and** same band **and** same mode (SSB/USB/LSB count as one).
- **`SupportFiles\wl_cty.dat`** – country / continent / zones of a call.
- **`SupportFiles\MASTER.SCP`** – Super Check Partial, until POUNCer's own
  download (section 14) is there.

## 4. POUNCer → N1MM's windows (local Windows messages)

To put a call into N1MM, POUNCer works with N1MM's Entry Window like a
person, using Windows API calls (no network):

1. **Front:** `SetForegroundWindow` on the radio's Entry Window
   (`EntryWindowHwnd` from RadioInfo; `AttachThreadInput` so Windows allows
   it).
2. **Find the box:** `GetGUIThreadInfo` → the text box with the cursor (the
   callsign box).
3. **Put the call in:** `WM_SETTEXT` with the whole call in one go
   (`[n1mm] type_mode = settext`, default), or typed key by key with
   `SendInput` (`keys`).
4. **Check:** `WM_GETTEXT` reads the box back; if N1MM wiped it (its QSY
   wipe), the call is put in again (up to 3 times, plus up to 1 s more on
   radio B).
5. **Cursor back:** `EM_GETSEL` / `EM_SETSEL` + `SetFocus` restore the box
   and cursor position you were in (`return_focus`), or Entry Window A is
   brought back (`focus_back_to_a`).

**CW speed (read only):** once a second POUNCer reads the speed box of each
Entry Window with `WM_GETTEXT` (`SendMessageTimeout`, 200 ms, so a busy N1MM
can't stall it). The box is found with `EnumChildWindows`: the spin box
(text box + arrows) holding only a number 5 – 99, or the box that moved at
POUNCer's first PgUp / PgDn. Found boxes are listed in `cache\cwspeed.txt`.

Keys sent with `SendInput` (only when you press the matching button): F1–F12
(N1MM's messages – these **transmit**), Enter (log), Page Up / Down (CW
speed), Ctrl+K + the typed text + Enter / Esc / Backspace (phone CW
keyboard - **transmits**), Alt+W (wipe, keys mode only), Alt+' (filter wide / narrow), and for
radio B with `radio_b_qsy = type`: the frequency (Windows decimal sign,
e.g. `14025,3`) + Enter.

## 5. RBN telnet (TCP 7000 / 7001, out)

`telnet.reversebeacon.net:7000` (CW / RTTY; `:7001` FT8 / FT4 when those
modes are followed). Plain text lines, Telnet option negotiation answered
"no".

- **Login:** when the server asks for the call (or after 3 s): the callsign
  + CRLF. Nothing else, no password.
- **Last spots at start:** 2 s after login `sh/dx/100 spotter=S53WW-#`; if
  that gives nothing, `sh/dx/30` (`[rbn] history`).
- **Spots** (filtered by skimmer in POUNCer, default S53WW):

```
DX de S53WW-#:     14014.0  K1ABC          CW    13 dB  25 WPM  CQ      1135Z
```

- **History lines** (answer to `sh/dx`):

```
  14025.0  K1ABC        13-Oct-2026 1159Z  CW 23 dB 28 WPM CQ          <S53WW-#>
```

- Reconnects with growing pauses (2 → 60 s). If no line arrives for 120 s,
  or it fails twice in a row, it switches to the backup (section 6) and
  tries RBN again every 10 minutes.
- **Not connected at all** while RBN isn't needed: an SSB contest is open in
  N1MM, or every radio is on SSB / AM / FM for a minute - in a team only
  while no POUNCer says `cw` in its hello (section 9). Light "RBN on hold".

## 6. Telnet backup (TCP 8000, out)

`azure.s53m.com:8000` – a DX cluster that passes on the RBN skimmer spots in
the same `DX de …` format. Same login (callsign), `[rbn] backup_commands`
sent 1 s after login, then the same `sh/dx` request and line parsing.

## 7. RBN over HTTP (HTTPS, out)

`GET https://azure.s53m.com/cors/rbn/live` – S53ZO's server keeps one RBN
connection and serves its reports in pages. Used only while the telnet feed
(sections 5 and 6) has been down or silent for a minute; stopped a minute
after telnet works again. No login.

- First request: `?lookbackSeconds=600&limit=2000` (the last 10 minutes).
- Then: `?cursor=<cursor from the last answer>&limit=2000` – only what is
  new; `hasMore: true` = ask again at once, else every 5 s. Errors: retry
  after 10 → 120 s.

```json
{"cursor": "20261015:7214711", "reset": false, "hasMore": false,
 "spots": [{"spotter": "S53WW", "spotterRaw": "S53WW-#", "dxCall": "K1VR",
            "freqKHz": 14027.4, "band": "20M", "mode": "CW", "snr": 18,
            "speed": 28, "ts": 1786112300123}]}
```

`ts` = UTC milliseconds. There is no CQ / beacon field: `…/B` calls are
dropped as beacons. Same skimmer filter as the telnet spots.

## 8. DX cluster for SSB (TCP 8000, out)

`azure.s53m.com:8000` (`[ssb] host`, `port`), only while a radio is on
USB / LSB (or on a team master; `[ssb] mode`). Login with `[ssb] login`
(empty = your call; use e.g. `S55OO-2` if the RBN backup is on the same
server). `[ssb] commands` 1 s after login, `sh/dx/<history>` 2 s after.
Human spots:

```
DX de S50A:      14250.0  K1ABC        59 SSB                    1200Z
```

POUNCer keeps spotters from your continent (`[ssb] spotters`), guesses the
mode from the comment (SSB / USB / LSB / CW / FT8…), else an FT8 / FT4 window
(0 - 3 kHz above 7074, 14074, 7047.5 …), else the band plan,
and shows them as SSB spots.

**Not connected while N1MM sends its spots** (section 1, `[ssb]
use_n1mm_spots = auto`): from N1MM's first spot this connection is closed
(light "SSB cluster on hold"); after 20 s without an N1MM spot (slow band: 4 x
its usual gap) it is opened again, for at least a minute. A spot of your own
call by a person on either source brings the SPOTTED pop-up.

## 9. Team (UDP 12079, in and out)

Several POUNCers on one network. Compact JSON datagrams, each with `id` (a
random id of that POUNCer) and `t` (type):

| `t` | When | Content | Sent to |
|---|---|---|---|
| `hi` | every 2 s | `started` (time), `web` (its web port), `healthy` (RBN works or closed on purpose), `ver` (version), `cw` (this POUNCer needs RBN: a radio on CW, not an SSB contest), `paused` (master: RBN closed, nobody needs it) | broadcast `255.255.255.255:12079` out of **every** network adapter (LAN, ZeroTier…) + directly to each known POUNCer and each `[team] peers` IP |
| `spot` | master, per spot | `s`: `{dxcall, freq_khz, mode, snr, wpm, spot_type, spotter, time_utc}` | directly to each POUNCer |
| `update` | master, *Help > Update all POUNCers in the team* | - | every POUNCer checks GitHub, downloads the newest and restarts (only from the master) | directly to each POUNCer |
| `self` | master, when your call is spotted | `d`: `{call, spotter, khz, comment}` - the slaves show the SPOTTED pop-up too | directly to each POUNCer |
| `rh` | when a call's run history is learned | `d`: `{"CALL\|CONTEST\|MODE": {run, hours, rec, bands…}}` | directly to each POUNCer |

```json
{"t":"hi","started":1786112000.5,"web":8080,"healthy":true,"ver":"0.7.41","id":"3f2a…"}
{"t":"hi", …, "bands":{"40m":{"spots":90,"calls":40,"dx":34,"state":"open"}, …}}   (master: band-open counts for the slaves)
```

- **Master** = the one running longest whose RBN works; it alone talks to
  RBN / the cluster and passes spots on. Not heard for 6 s, or its RBN down
  for over a minute → the next one takes over.
- A POUNCer that joins fetches what is already known with
  `GET http://<master>:8080/api/runhist` (section 10).
- Different versions in one team are shown on the Team light.

## 10. Phone page and API (HTTP, TCP 8080, in)

A small web server on `0.0.0.0:8080` (`[web] host`, `port`): the phone page
for any browser on the LAN (or ZeroTier). Plain HTTP, no login – keep it on
your own network. All answers are HTML or JSON; requests are `GET` with
query parameters (`radio=A|B` = which radio the phone works).

| Path | What |
|---|---|
| `/`, `/help`, `/summary`, `/zones`, `/timeline`, `/settings` | pages: spot tiles, help, contest summary, CQ zones (who hears me / what I hear), band opening timeline, settings |
| `/api/spots?band=&radio=` | the list for a radio / band (JSON: radios, log, mults, spots with run %, hours, `busted`…) |
| `/api/timeline?hours=12` | spots / stations / DX stations per band and 10 minutes, heard by skimmers on your continent, up to 48 h back (kept in `cache/timeline.json`) |
| `/api/spots?since=<tag>` | the list again only if it changed: `{same: true}` (a few bytes) when its tag (a fingerprint without the spots' ages) is the phone's; the phone asks once a second, never two at once, not while hidden |
| `/api/ping` | phone ping (every minute): answers at once, the page times the round trip |
| `/api/explain?call=3B8X&freq=14025.1` | phone Debug (hold a tile): everything POUNCer knows about that spot - entity, zones, each multiplier type new / worked, worked, run history, SCP, tapped, all skimmers that spotted it, the raw spot - read-only, local data |
| `/api/zones?min=15` | per band and CQ zone 1-40: `hears_me` (skimmers that spotted your call) and `i_hear` (stations heard by skimmers on your continent), best SNR, count, calls, minutes ago - from the RBN / cluster feed POUNCer already has, nothing new on the network |
| `/api/qsy?call=&freq=&mode=&radio=` | tap: QSY + call into N1MM (the next `/api/spots` carries `tap`: what really happened) |
| `/api/key?key=F2&radio=` | F-key / Enter / PgUp / PgDn to N1MM (F-keys **transmit**) |
| `/api/minsnr?db=10` | Minimum SNR of the list (0–60; 0 = off, the default) - window and phone, saved to config.ini |
| `/api/cw?op=open\|text\|back\|done\|stop&text=&n=&radio=` | Phone CW keyboard: open = Ctrl+K in the Entry Window, text = typed into N1MM's CW window (**transmits**; A–Z, 0–9, space, CW punctuation, max 200), back = n × Backspace, done = Enter, stop = Esc. 404 when `[web] cw_keyboard = false` |
| `/api/tune?hz=-25&radio=` | move the VFO |
| `/api/band?band=20m&radio=` | look at another band |
| `/api/filter?w=narrow\|wide&fix=` | RX filter |
| `/api/hide`, `/api/unhide?call=&freq=` | hide a station |
| `/api/clear?undo=`, `/api/reconnect` | clear spots, re-read the log |
| `/api/summary` | contest summary (JSON) |
| `/api/settings`, `/api/settings/set?key=&value=`, `/api/restart` | settings – **changes only from the PC itself** (127.0.0.1) unless `[web] remote_settings = true` |
| `/api/runhist` | everything known about run history (team sync) |

## 11. Updates (HTTPS, out)

- `GET https://api.github.com/repos/s55oo/POUNCer/releases/latest` – at
  start, every 6 h and when the Update light is clicked.
- If newer: the asset `POUNCer.exe` is downloaded and its **SHA-256** is
  compared with the one GitHub gives for the asset before it replaces the
  exe (restart with the Restart button). macOS: only a notice.
- PC clock (pre-contest check line), at start and every 30 min: an **NTP**
  query (UDP 123, SNTP, one 48-byte packet each way) to `pool.ntp.org`, else
  `time.windows.com` - accurate to milliseconds, like a time-sync program.
  Only if neither answers: `HEAD https://api.github.com` and its `Date`
  header, corrected by `Age` (a cached answer's Date is older than now).

## 12. Warm-up lists (HTTPS, out)

`https://github.com/s55oo/POUNCer/releases/download/warmup/warm.sqlite`
(CW) and `…/warm-ssb.sqlite` (SSB): run % (overall and per band) and hours
for ~46 000 / ~61 000 stations. Downloaded at start when missing, older
than 30 days or without per-band data; SQLite header checked. Built by the
repository's GitHub Action from S53ZO's public log archive.

## 13. Run history of other calls (HTTPS, out)

For a spotted call not in the lists: from
`https://raw.githubusercontent.com/s53zo/Hamradio-Contest-logs-Archives/main/`
– the index shard `SH6/logs_XX.sqlite` (XX = crc32 of the call), then up to
6 of that call's Cabrillo logs. Up to 12 downloads at a time; cached in
`cache\` so each is fetched once. Only public contest logs; the spotted
callsign is visible in the URL to GitHub. Optional own run list server:
`[ui] run_server` (`GET <server>/runs?contest=&mode=`).

## 14. Super Check Partial (HTTPS, out)

`GET https://www.supercheckpartial.com/MASTER.SCP` at every start (and every
`[scp] refresh_days` while running), as a **conditional** request:
`If-Modified-Since` (the copy's date = the server's `Last-Modified`) and
`If-None-Match` (its `ETag`). The server answers **304 Not Modified** -
no body - when the copy is current, so only a newer list is downloaded. Kept
only if it holds at least 10 000 calls (an error page never replaces a good
list). Optionally also copied into N1MM's `SupportFiles` (`[scp]
update_n1mm`).

## 15. Finding the LAN address (no traffic)

To show the phone address, POUNCer opens a UDP socket and *connects* it to
`8.8.8.8:80` – this only asks Windows which network card would be used;
**no packet is sent**. ZeroTier addresses are shown first when ZeroTier
runs.

## 18. PSK Reporter (MQTT, TCP 1883, out)

Only for an SSB spot in an FT8 / FT4 window **inside** the SSB segment (7074 -
7077 on 40 m, 1840 - 1843 on 160 m) without "FT8" / "SSB" in it (in an SSB contest too): POUNCer connects to `mqtt.pskreporter.info:1883`
(MQTT 3.1.1, no login, client id `pouncer-<random>`), subscribes to
`pskr/filter/v2/+/+/<CALL>/#` - **that one station transmitting**, never the
whole feed - for 2 minutes, or until the first FT8 / FT4 report near the
spot (at most 8 calls at once), then unsubscribes and
hangs up when nothing is watched. Each report is a small JSON (`sc` call, `f`
Hz, `md` mode, `rp` SNR, `rc` receiver …); an FT8 / FT4 report within 3 kHz of
the spot makes the spot FT8 / FT4. Nothing about you is sent.
