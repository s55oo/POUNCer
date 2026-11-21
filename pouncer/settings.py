"""Settings page: the most-used config.ini settings, changed in the browser.

Each setting maps a ``[section] key`` in config.ini to a ``Config``
attribute. Saving writes just that line into config.ini (comments and
everything else stay as they are) and updates the running POUNCer: most
settings work at once ("live"), the rest from the next start.
"""

from __future__ import annotations

import os
import re

from .config import parse_radio

# kind: int / bool / choice / text ; live: used at once without a restart
SETTINGS: list[dict] = [
    {"key": "worked.profile", "attr": "worked_profile", "kind": "choice",
     "choices": ["auto", "off", "ttc", "mini40", "mini80", "mwc"],
     "live": False, "group": "Program", "label": "Contest session profile",
     "help": "auto: verified N1MM IDs; off: ordinary contest behavior. "
             "ttc: 15-minute periods; mini40/mini80: 10-minute periods; mwc: whole hour. "
             "UTC schedule, session counts and multipliers. Restart after changing."},
    {"key": "ui.zo_mode", "attr": "zo_mode", "kind": "bool", "live": True,
     "group": "Program", "label": "ZO mode",
     "help": "Three vertical callsign columns: Urgent, Medium, Normal. Unknown history "
             "is separated below Normal. Existing sorting applies within each column. "
             "Desktop uses tiles; browser keeps all three columns on phones too. "
             "Historical priority hints, not departure probabilities."},
    {"key": "ui.urgency_contest_minutes", "attr": "urgency_contest_minutes", "kind": "int",
     "min": 0, "max": 10080, "live": True, "group": "Program",
     "label": "History hint: contest duration (minutes, 0 = auto)",
     "help": "Hours hint below 25% of event duration, capped at 6 hours. "
             "Requires matching-contest history; unknown duration/source disables that hint. "
             "A historical heuristic, not a prediction of when a station will leave."},
    {"key": "cluster.enabled", "attr": "cluster_server", "kind": "bool", "live": True,
     "group": "Cluster server", "label": "Serve the spots as a DX cluster (telnet)",
     "help": "N1MM's Telnet window (or any cluster client) connects to this PC on the port "
             "below and gets the spots POUNCer collects (the sources ticked in the Spots "
             "menu, or the team master's) as 'DX de' lines - only while Relay is on. Spots from N1MM itself are never served "
             "back, and POUNCer ignores its own spots coming back from N1MM. Read-only: "
             "nothing a client sends changes POUNCer."},
    {"key": "cluster.port", "attr": "cluster_port", "kind": "int", "min": 1, "max": 65535,
     "live": True, "group": "Cluster server", "label": "  ... TCP port",
     "help": "In N1MM's Telnet window: add a cluster with this PC's address (127.0.0.1 on "
             "the same PC) and this port. Allow it in the firewall for other PCs."},
    {"key": "cluster.relay", "attr": "cluster_relay", "kind": "bool", "live": True,
     "group": "Cluster server", "label": "  ... Relay: hand out the spots",
     "help": "On = everything both ways: the spots of the sources switched on in the "
             "Spots menu go out to the clients on the port above, and whatever a client "
             "types (spots made in N1MM - Alt+P / Spot It, sh/dx, filters) goes over "
             "POUNCer's own telnet connection to the DX cluster (a Telnet #1 - #5 on); the "
             "answers come back. Off = nothing either way (a spot gets '*** POUNCer: spot "
             "not sent - Relay is off'). Also the Relay button."},
    {"key": "cluster.merge", "attr": "cluster_merge", "kind": "bool", "live": True,
     "group": "Cluster server", "label": "Receiver-aware spot collection",
     "help": "For the list, team and port 10000: suppress repeats for 180 seconds; "
             "a spot more than 500 Hz from the last sent frequency is a move, sent at "
             "once with its own 180 s window. Align receiver frequencies when qualified evidence "
             "is available; guard local CW callsign corrections. First discoveries pass "
             "promptly. Mirrors never count as extra receivers."},
    {"key": "cluster.trust_q", "attr": "cluster_trust_q", "kind": "int", "min": 0, "max": 99,
     "live": True, "group": "Cluster server", "label": "Trust DXSpider quality from Q:",
     "help": "A cluster spot with Q:n (DXSpider: n different skimmers heard it) from this "
             "number on counts as confirmed: never held as cw-conflict-unconfirmed. A "
             "GoCluster spot rated V (Telnet role gocluster) counts the same. The same spot "
             "from several Telnets counts once (the highest Q, never a sum). Default 3; "
             "0 = off (only POUNCer's own receiver count). A frequency move over 500 Hz is "
             "sent at once anyway (v0.7.248)."},
    {"key": "cluster.people_all", "attr": "cluster_people_all", "kind": "bool", "live": True,
     "group": "Cluster server", "label": "Send every person's spot (no -#), all modes",
     "help": "A spot by a person (spotter without '-#') from a cluster Telnet goes to port "
             "10000 as it is, in every mode (CW, SSB, FT8, FT4, RTTY ...): no repeat window, "
             "no CW-conflict hold, no PSK Reporter hold, no call / frequency correction. "
             "Your Spotters / Spotted filters, Remove WARC, beacons and your own call still "
             "apply, and the same spot from several Telnets goes out once. Off = people's "
             "spots go through the spot collector like the skimmers'. Default on."},
    {"key": "n1mm.listen_port", "attr": "n1mm_listen_port", "kind": "choice",
     "choices": [str(p) for p in range(12060, 12070)], "live": True, "group": "N1MM",
     "label": "POUNCer's N1MM port (UDP, in)",
     "help": "Where POUNCer listens for N1MM's Radio / App Info and spots. Set the same "
             "address in N1MM: Config > Configure Ports... > Broadcast Data - Radio, App "
             "Info (and Spots) to 127.0.0.1:<this port>. 12060 is N1MM's default; give each "
             "program that listens to N1MM its own port (12061, 12062 ...). Applied at once."},
    {"key": "n1mm.qsy_wait_ms", "attr": "qsy_wait_ms", "kind": "int", "min": 0, "max": 10000,
     "live": True, "group": "Tap -> N1MM",
     "label": "Wait for N1MM's new frequency (ms)",
     "help": "After a QSY, wait (at most) this long until N1MM reports the radio on the "
             "new frequency before the call is put in (default 2500). No report: the QSY is "
             "sent once more, the call kept in the box and put back if N1MM wipes it."},
    {"key": "n1mm.qsy_settle_ms", "attr": "qsy_settle_ms", "kind": "int", "min": 0, "max": 5000,
     "live": True, "group": "Tap -> N1MM",
     "label": "Then wait (ms)",
     "help": "Extra pause after that, before wiping and putting the call in. Raise it "
             "(e.g. 500) on a slow PC that loses calls."},
    {"key": "n1mm.type_mode", "attr": "type_mode", "kind": "choice",
     "choices": ["settext", "keys"], "live": True, "group": "Tap -> N1MM",
     "label": "How the call goes into N1MM",
     "help": "settext = in one go (nothing can get mixed up); keys = typed key by key."},
    {"key": "n1mm.wipe_before_call", "attr": "wipe_before_call", "kind": "bool",
     "live": True, "group": "Tap -> N1MM",
     "label": "Wipe the Entry Window first", "help": "N1MM's {WIPE} before the new call."},
    {"key": "n1mm.focus_n1mm", "attr": "focus_n1mm", "kind": "bool",
     "live": True, "group": "Tap -> N1MM",
     "label": "Bring N1MM to the front", "help": "Needed to put the call in."},
    {"key": "n1mm.radio_b_qsy", "attr": "radio_b_qsy", "kind": "choice",
     "choices": ["udp-left", "type", "udp"], "live": True, "group": "SO2R / SO2V",
     "label": "How radio B is QSYed",
     "help": "udp-left (default) = N1MM's UDP command for radio 2 with mouse button Left, "
             "nothing typed; type = frequency + Enter typed into Entry Window B; udp = the UDP "
             "command with Right (moved radio A here). The call goes in the same way for all three."},
    {"key": "n1mm.freq_decimal", "attr": "freq_decimal", "kind": "choice",
     "choices": ["auto", ".", ","], "live": True, "group": "SO2R / SO2V",
     "label": "Decimal sign of the typed frequency",
     "help": "auto = the Windows setting (\",\" in Slovenian)."},
    {"key": "n1mm.return_focus", "attr": "return_focus", "kind": "bool",
     "live": True, "group": "SO2R / SO2V",
     "label": "Cursor back where it was",
     "help": "After a tap for the other radio: back to the same Entry Window and box."},
    {"key": "n1mm.focus_back_to_a", "attr": "focus_back_to_a", "kind": "bool",
     "live": True, "group": "SO2R / SO2V",
     "label": "After radio B: focus back to A",
     "help": "Every tap (or -/+) for radio B ends with the cursor in Entry Window A, "
             "wherever it was before. Off = 'Cursor back where it was' decides."},
    {"key": "n1mm.cw_speed", "attr": "cw_speed", "kind": "bool",
     "live": False, "group": "Tap -> N1MM",
     "label": "Show N1MM's CW speed",
     "help": "Reads the WPM from the speed box of N1MM's Entry Window (A and B) and shows "
             "it in the window, the compact bar and on the phone."},
    {"key": "n1mm.cw_speed_popup", "attr": "cw_speed_popup", "kind": "bool",
     "live": True, "group": "Tap -> N1MM",
     "label": "Popup when the CW speed changes",
     "help": "A short 'CW 30 WPM' popup (window and phone) after WPM + / -, PgUp / PgDn "
             "or any other change in N1MM. It never takes the keyboard from N1MM."},
    {"key": "web.cw_keyboard", "attr": "web_cw_keyboard", "kind": "bool",
     "live": False, "group": "Tap -> N1MM",
     "label": "Phone CW keyboard (hold ENTER)",
     "help": "Hold ENTER on the phone 1.3 s: type text that N1MM sends as CW "
             "(its Ctrl+K window). Done = Enter, Stop = Esc. Transmits."},
    {"key": "web.phone_control", "attr": "web_phone_control", "kind": "choice",
     "choices": ["phone", "all", "off"], "live": True, "group": "Tap -> N1MM",
     "label": "Phones may control N1MM",
     "help": "QSY on a tap, F-keys, ENTER, Spot It, CW keyboard, tune. phone = each "
             "phone's own switch (band menu > Control N1MM, off until set there); all = "
             "every phone at once, no switch needed; off = no phone, look only (the "
             "browser on this PC always can). In a team the MASTER's choice counts for "
             "every POUNCer."},
    {"key": "n1mm.default_radio", "attr": "default_radio", "kind": "choice",
     "choices": ["A", "B"], "live": False, "group": "SO2R / SO2V",
     "label": "Radio at start", "help": "Which radio the main window works at start."},
    {"key": "rbn.telnet_silent_minutes", "attr": "telnet_silent_minutes", "kind": "int",
     "min": 0, "max": 120, "live": True, "group": "RBN",
     "label": "Reconnect a silent connection after (min)",
     "help": "RBN or a Telnet #1 - #5 connection that receives nothing at all this many "
             "minutes is taken for hung (a router forgot it, the node froze): POUNCer drops "
             "it and connects again by itself. Skimmer feeds send something every few "
             "seconds. Default 5; 0 = off. (TCP keepalive also notices a dead server.)"},
    {"key": "rbn.skimmer_quiet_minutes", "attr": "skimmer_quiet_minutes", "kind": "int",
     "min": 1, "max": 60, "live": False, "group": "RBN", "label": "Skimmer quiet warning (min)",
     "help": "Check line warns when a listed skimmer (S53WW, TK0C) has sent nothing this "
             "long while RBN works: offline or its connection down."},
    {"key": "filter.mode", "attr": "filter_mode", "kind": "choice",
     "choices": ["calls", "dxcc", "zones", "cont", "ww"], "live": True,
     "group": "Spotters", "label": "Spots from these spotters",
     "help": "One filter for every source (RBN, Telnet #1 - #5, HTTP, HamQTH): calls = the "
             "listed skimmers / spotters, dxcc = spotters in the listed countries, zones = in "
             "the listed CQ zones, cont = on the listed continents, ww = all. Also the "
             "Spotters menu."},
    {"key": "spotted.mode", "attr": "spotted_mode", "kind": "choice",
     "choices": ["calls", "dxcc", "zones", "cont", "ww"], "live": True,
     "group": "Spotted", "label": "Keep spots of these stations",
     "help": "Which SPOTTED stations (the DX) are kept, from every source: calls = the "
             "listed calls (prefix), dxcc = in the listed countries, zones = in the listed CQ "
             "zones, cont = on the listed continents, ww = all. Separate from Spotters "
             "(who spots). Also the Spotted menu."},
    {"key": "spotted.calls", "attr": "spotted_calls", "kind": "text", "live": True,
     "group": "Spotted", "label": "  Calls", "help": "e.g. 3Y0J, VP8 - prefix match."},
    {"key": "spotted.dxcc", "attr": "spotted_dxcc", "kind": "text", "live": True,
     "group": "Spotted", "label": "  DXCC", "help": "Prefix or name: JA, VK, ZL or Japan."},
    {"key": "spotted.zones", "attr": "spotted_zones", "kind": "text", "live": True,
     "group": "Spotted", "label": "  Zones", "help": "CQ zones: 3, 4, 5."},
    {"key": "spotted.conts", "attr": "spotted_conts", "kind": "text", "live": True,
     "group": "Spotted", "label": "  Continents", "help": "EU, NA, SA, AS, AF, OC."},
    {"key": "rbn.spotter", "attr": "rbn_spotter", "kind": "text", "live": True,
     "group": "Spotters", "label": "  Calls",
     "help": "Skimmers / spotters, comma separated (prefix: TK0C = TK0C-1, TK0C-2 ...), "
             "e.g. S53WW, TK0C. While all of them are quiet: the other skimmers on your "
             "continent. Spots of people then: the continent of these calls."},
    {"key": "filter.dxcc", "attr": "filter_dxcc", "kind": "text", "live": True,
     "group": "Spotters", "label": "  DXCC",
     "help": "Countries, comma separated - a prefix or the name: S5, 9A, OE or Slovenia."},
    {"key": "filter.zones", "attr": "filter_zones", "kind": "text", "live": True,
     "group": "Spotters", "label": "  Zones",
     "help": "CQ zones, comma separated: 14, 15."},
    {"key": "filter.conts", "attr": "filter_conts", "kind": "text", "live": True,
     "group": "Spotters", "label": "  Continents",
     "help": "EU, NA, SA, AS, AF, OC, comma separated."},
    {"key": "rbn.hide_beacons", "attr": "hide_beacons", "kind": "bool", "live": True,
     "group": "RBN", "label": "Exclude beacons",
     "help": "Beacon spots are dropped as they come in - not in the list, not on port 10000 "
             "(N1MM's Telnet window): the 18 NCDXF beacons (4U1UN, W6WX, OH2B ...) when on "
             "14100 / 18110 / 21150 / 24930 / 28200 (+-0.5 kHz; the same call elsewhere is "
             "a real station and stays), spots of type BEACON / NCDXF, calls ending in /B. "
             "Each one is a line in the traffic console (BEACON)."},
    {"key": "rbn.backup_off", "attr": "backup_off", "kind": "bool", "live": True,
     "group": "RBN", "label": "Disable backup spots",
     "help": "Only your chosen skimmers (Spotters: calls / DXCC / zones / continents), "
             "always. Off (default): while they all send nothing for "
             "backup_after_minutes (5), the backup skimmers - [rbn] backup_spotters, or "
             "every skimmer on your continent - are used too (orange in the traffic "
             "console)."},
    {"key": "rbn.hide_warc", "attr": "hide_warc", "kind": "bool", "live": True,
     "group": "RBN", "label": "Exclude WARC (30 / 17 / 12 m)",
     "help": "No spots on the WARC bands 30 m, 17 m and 12 m (no contests there): "
             "not in the list or on the phone, not on port 10000 (N1MM's Telnet window), "
             "EXCL WARC in the traffic console. Off again = they show again. Also the "
             "Spots menu > Exclude. 60 m has its own switch."},
    {"key": "rbn.hide_60m", "attr": "hide_60m", "kind": "bool", "live": True,
     "group": "RBN", "label": "Exclude 60 m",
     "help": "No spots on 60 m (5330 - 5410 kHz): not in the list or on the phone, not on "
             "port 10000, EXCL 60M in the traffic console. Until v0.7.245 part of Remove "
             "WARC spots - not set, it follows that switch. Also the Spots menu > Exclude."},
    {"key": "rbn.serve_own_spots", "attr": "serve_own_spots", "kind": "bool", "live": True,
     "group": "RBN", "label": "Allow my own spots",
     "help": "A spot of your own call is never put in the list (it still triggers the "
             "SPOTTED pop-up) - off (default), it is also kept off port 10000 (N1MM's "
             "Telnet window and other clients), silently. On: it goes out there too, "
             "like any other spot. Traffic console: EXCL SELF while off."},
    {"key": "rbn.history", "attr": "rbn_history", "kind": "int", "min": 0, "max": 500,
     "live": False, "group": "RBN", "label": "Last spots at start (SH/DX)",
     "help": "0 = off."},
    {"key": "rbn.sources", "attr": "rbn_source", "kind": "text", "live": True,
     "group": "RBN", "label": "Spots come in from",
     "help": "Comma separated, each one on or off: rbn (telnet.reversebeacon.net: skimmer "
             "spots only), telnet, telnet2, telnet3, telnet4, telnet5 (Telnet #1 - #5 - the servers below: "
             "everything each one carries, skimmer AND people's spots), http (the HTTP feed). "
             "Tick as many as you like (Spots menu); what is on goes into POUNCer and out to "
             "the cluster server (port 10000), the same spot from two sources counts once, a "
             "Telnet with the same server as one before it adds nothing. Kept for the next "
             "start. In a team: the master's choice."},
    {"key": "rbn.hamqth_backup", "attr": "rbn_hamqth_backup", "kind": "bool", "live": True,
     "group": "RBN", "label": "HamQTH: stands in while every telnet is down",
     "help": "When all the telnet connections you ticked (RBN, Telnet #1 - #5) have "
             "been down for a minute: RBN's skimmer spots (www.hamqth.com/api/v2/rbn) and, "
             "with a Telnet ticked, DX cluster spots (/api/v2/spots) from HamQTH.com. Every "
             "20 s, no login. Closed a minute after a telnet works again. Nothing ticked: "
             "HamQTH is the source itself. Data provided by HamQTH.com. Also the HamQTH "
             "button on the Spots line."},
    {"key": "rbn.pause_on_phone", "attr": "rbn_pause_on_phone", "kind": "bool", "live": True,
     "group": "RBN", "label": "Pause RBN while the radio is on SSB",
     "help": "Every radio on SSB / AM / FM for a minute: the RBN connection is closed (SSB "
             "spots are people's spots: Telnet #1 - #5); back at once when a radio goes "
             "to CW. Not "
             "while other team POUNCers use this one's feed."},
    {"key": "rbn.http_backup", "attr": "rbn_http_backup", "kind": "text", "live": True,
     "group": "RBN", "label": "HTTP feed (URL)",
     "help": "The http source (Spots menu): RBN's skimmer spots over HTTP (S53ZO's Azure "
             "feed, no login). Empty = off."},
    {"key": "cty.auto_update", "attr": "cty_auto_update", "kind": "bool", "live": False,
     "group": "Country file", "label": "Download S53M's cty.dat every Friday",
     "help": "Every Friday (or the first start after one) POUNCer asks S53M's server whether "
             "cty.dat changed (a few bytes when it didn't) and uses a newer one at once. "
             "N1MM's wl_cty.dat only before the first download. Off = N1MM's file."},
    {"key": "cty.update_n1mm", "attr": "cty_update_n1mm", "kind": "bool", "live": False,
     "group": "Country file", "label": "  ... also copy it into N1MM's SupportFiles",
     "help": "Only when the download is a wl_cty.dat (an own [cty] url) - S53M's cty.dat "
             "never replaces N1MM's wl_cty.dat."},
    {"key": "scp.enabled", "attr": "scp_enabled", "kind": "bool", "live": False,
     "group": "RBN", "label": "Mark calls not in Super Check Partial (?)",
     "help": "A '?' after a spotted call that is not in Super Check Partial (S53M's "
             "MASTER.DTA, downloaded every Friday) - maybe a skimmer error."},
    {"key": "scp.update_n1mm", "attr": "scp_update_n1mm", "kind": "bool", "live": False,
     "group": "RBN", "label": "Also update N1MM's MASTER.SCP",
     "help": "The Friday download is also written into N1MM's SupportFiles\\MASTER.SCP as "
             "text (N1MM reads it at its next start)."},
    {"key": "ssb.follow_radio_mode", "attr": "follow_radio_mode", "kind": "bool",
     "live": True, "group": "SSB",
     "label": "List follows the radio mode",
     "help": "Radio on USB / LSB = SSB spots (people's spots: Telnet #1 - #5), on CW = "
             "the CW skimmer spots."},
    {"key": "ssb.pskreporter", "attr": "ssb_pskreporter", "kind": "bool", "live": False,
     "group": "SSB", "label": "FT8 check with PSK Reporter",
     "help": "A spot on 7074 - 7077 / 1840 - 1843 (inside the SSB segment) without a mode: PSK Reporter "
             "is asked about that one call; FT8 / FT4 reports there = not SSB (in an SSB contest too). "
             "Also a CW spot in any FT8 / FT4 window (14074, 14080, 7047.5 ...): FT8 / FT4 reports "
             "= not CW; none = a real CW station, it stays."},
    {"key": "telnet1.enabled", "attr": "rbn_source", "kind": "bool", "live": True,
     "group": "RBN", "label": "Telnet #1: enable this connection", "source_key": "telnet",
     "help": "The very same switch as 'Telnet #1' in the Spots menu - ticking either one "
             "ticks the other. Refused (and says why) without a server below; in a team, "
             "the master's choice."},
    {"key": "telnet1.host", "attr": "telnet1_host", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #1: server",
     "help": "A DX cluster / skimmer node, e.g. azure.s53m.com. "
             "Tick 'Telnet #1' in the Spots menu to use it: all its spots, skimmers and "
             "people. Empty = not set."},
    {"key": "telnet1.port", "attr": "telnet1_port", "kind": "int", "min": 1, "max": 65535,
     "live": True, "group": "RBN", "label": "Telnet #1: port", "help": ""},
    {"key": "telnet1.login", "attr": "telnet1_login", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #1: login call",
     "help": "Empty = your call. Another SSID (e.g. S55OO-2) where your call is already "
             "logged in on that node."},
    {"key": "telnet1.role", "attr": "telnet1_role", "kind": "choice", "live": True,
     "choices": ["cluster", "local", "rbn", "gocluster"], "group": "RBN",
     "label": "Telnet #1: source role",
     "help": "cluster = DX cluster; local = direct skimmer; rbn = raw receiver feed; "
             "gocluster = processed reference. Only cluster receives relayed commands. "
             "GoCluster is login-only: no automatic history or startup commands."},
    {"key": "telnet1.commands", "attr": "telnet1_commands", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #1: commands after login",
     "help": "Comma separated, e.g. set/skimmer."},
    {"key": "telnet2.enabled", "attr": "rbn_source", "kind": "bool", "live": True,
     "group": "RBN", "label": "Telnet #2: enable this connection", "source_key": "telnet2",
     "help": "The very same switch as 'Telnet #2' in the Spots menu - ticking either one "
             "ticks the other. Refused (and says why) without a server below; in a team, "
             "the master's choice."},
    {"key": "telnet2.host", "attr": "telnet2_host", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #2: server",
     "help": "A DX cluster / skimmer node, e.g. dxc.example.net. "
             "Tick 'Telnet #2' in the Spots menu to use it: all its spots, skimmers and "
             "people. Empty = not set."},
    {"key": "telnet2.port", "attr": "telnet2_port", "kind": "int", "min": 1, "max": 65535,
     "live": True, "group": "RBN", "label": "Telnet #2: port", "help": ""},
    {"key": "telnet2.login", "attr": "telnet2_login", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #2: login call",
     "help": "Empty = your call. Another SSID (e.g. S55OO-2) where your call is already "
             "logged in on that node."},
    {"key": "telnet2.role", "attr": "telnet2_role", "kind": "choice", "live": True,
     "choices": ["cluster", "local", "rbn", "gocluster"], "group": "RBN",
     "label": "Telnet #2: source role",
     "help": "cluster = DX cluster; local = direct skimmer; rbn = raw receiver feed; "
             "gocluster = processed reference. Only cluster receives relayed commands. "
             "GoCluster is login-only: no automatic history or startup commands."},
    {"key": "telnet2.commands", "attr": "telnet2_commands", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #2: commands after login",
     "help": "Comma separated, e.g. set/skimmer."},
    {"key": "telnet3.enabled", "attr": "rbn_source", "kind": "bool", "live": True,
     "group": "RBN", "label": "Telnet #3: enable this connection", "source_key": "telnet3",
     "help": "The very same switch as 'Telnet #3' in the Spots menu - ticking either one "
             "ticks the other. Refused (and says why) without a server below; in a team, "
             "the master's choice."},
    {"key": "telnet3.host", "attr": "telnet3_host", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #3: server",
     "help": "A DX cluster / skimmer node, e.g. dxc.example.net. "
             "Tick 'Telnet #3' in the Spots menu to use it: all its spots, skimmers and "
             "people. Empty = not set."},
    {"key": "telnet3.port", "attr": "telnet3_port", "kind": "int", "min": 1, "max": 65535,
     "live": True, "group": "RBN", "label": "Telnet #3: port", "help": ""},
    {"key": "telnet3.login", "attr": "telnet3_login", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #3: login call",
     "help": "Empty = your call. Another SSID (e.g. S55OO-2) where your call is already "
             "logged in on that node."},
    {"key": "telnet3.role", "attr": "telnet3_role", "kind": "choice", "live": True,
     "choices": ["cluster", "local", "rbn", "gocluster"], "group": "RBN",
     "label": "Telnet #3: source role",
     "help": "cluster = DX cluster; local = direct skimmer; rbn = raw receiver feed; "
             "gocluster = processed reference. Only cluster receives relayed commands. "
             "GoCluster is login-only: no automatic history or startup commands."},
    {"key": "telnet3.commands", "attr": "telnet3_commands", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #3: commands after login",
     "help": "Comma separated, e.g. set/skimmer."},
    {"key": "telnet4.enabled", "attr": "rbn_source", "kind": "bool", "live": True,
     "group": "RBN", "label": "Telnet #4: enable this connection", "source_key": "telnet4",
     "help": "The very same switch as 'Telnet #4' in the Spots menu - ticking either one "
             "ticks the other. Refused (and says why) without a server below; in a team, "
             "the master's choice."},
    {"key": "telnet4.host", "attr": "telnet4_host", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #4: server",
     "help": "A DX cluster / skimmer node, e.g. dxc.example.net. "
             "Tick 'Telnet #4' in the Spots menu to use it: all its spots, skimmers and "
             "people. Empty = not set."},
    {"key": "telnet4.port", "attr": "telnet4_port", "kind": "int", "min": 1, "max": 65535,
     "live": True, "group": "RBN", "label": "Telnet #4: port", "help": ""},
    {"key": "telnet4.login", "attr": "telnet4_login", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #4: login call",
     "help": "Empty = your call. Another SSID (e.g. S55OO-2) where your call is already "
             "logged in on that node."},
    {"key": "telnet4.role", "attr": "telnet4_role", "kind": "choice", "live": True,
     "choices": ["cluster", "local", "rbn", "gocluster"], "group": "RBN",
     "label": "Telnet #4: source role",
     "help": "cluster = DX cluster; local = direct skimmer; rbn = raw receiver feed; "
             "gocluster = processed reference. Only cluster receives relayed commands. "
             "GoCluster is login-only: no automatic history or startup commands."},
    {"key": "telnet4.commands", "attr": "telnet4_commands", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #4: commands after login",
     "help": "Comma separated, e.g. set/skimmer."},
    {"key": "telnet5.enabled", "attr": "rbn_source", "kind": "bool", "live": True,
     "group": "RBN", "label": "Telnet #5: enable this connection", "source_key": "telnet5",
     "help": "The very same switch as 'Telnet #5' in the Spots menu - ticking either one "
             "ticks the other. Refused (and says why) without a server below; in a team, "
             "the master's choice."},
    {"key": "telnet5.host", "attr": "telnet5_host", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #5: server",
     "help": "A DX cluster / skimmer node, e.g. dxc.example.net. "
             "Tick 'Telnet #5' in the Spots menu to use it: all its spots, skimmers and "
             "people. Empty = not set."},
    {"key": "telnet5.port", "attr": "telnet5_port", "kind": "int", "min": 1, "max": 65535,
     "live": True, "group": "RBN", "label": "Telnet #5: port", "help": ""},
    {"key": "telnet5.login", "attr": "telnet5_login", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #5: login call",
     "help": "Empty = your call. Another SSID (e.g. S55OO-2) where your call is already "
             "logged in on that node."},
    {"key": "telnet5.role", "attr": "telnet5_role", "kind": "choice", "live": True,
     "choices": ["cluster", "local", "rbn", "gocluster"], "group": "RBN",
     "label": "Telnet #5: source role",
     "help": "cluster = DX cluster; local = direct skimmer; rbn = raw receiver feed; "
             "gocluster = processed reference. Only cluster receives relayed commands. "
             "GoCluster is login-only: no automatic history or startup commands."},
    {"key": "telnet5.commands", "attr": "telnet5_commands", "kind": "text", "live": True,
     "group": "RBN", "label": "Telnet #5: commands after login",
     "help": "Comma separated, e.g. set/skimmer."},
    {"key": "ui.min_snr", "attr": "min_snr", "kind": "int", "min": 0, "max": 60,
     "live": True, "group": "Program", "label": "Minimum SNR (dB)",
     "help": "Window and phone list: hide spots weaker than this; 0 = off, all spots (default). Also next to "
             "the sort buttons (window); on the phone hold the SNR key 1.3 s."},
    {"key": "ui.min_snr_keep_mults", "attr": "min_snr_keep_mults", "kind": "bool",
     "live": True, "group": "Program", "label": "Min SNR: keep new mults",
     "help": "A new multiplier is shown even when it is weaker than the minimum SNR."},
    {"key": "ui.mark_tried", "attr": "mark_tried", "kind": "bool",
     "live": True, "group": "Program", "label": "Mark stations already tried",
     "help": "A station you tapped (called) gets \u21bb3m = last tapped 3 min ago "
             "(\u00d72 = twice), in the window and on the phone."},
    {"key": "ui.tried_minutes", "attr": "tried_minutes", "kind": "int", "min": 1, "max": 720,
     "live": True, "group": "Program", "label": "Tried mark for (minutes)",
     "help": "How long after the last tap the mark stays."},
    {"key": "ui.check_line", "attr": "check_line", "kind": "bool",
     "live": True, "group": "Program", "label": "Pre-contest check line",
     "help": "READY / CHECK / NOT READY line at the top of the window: N1MM, log, call, "
             "RBN, cty, SCP, phone, PC clock. The phone warns only when something is wrong."},
    {"key": "ui.band_lights", "attr": "band_lights", "kind": "bool",
     "live": True, "group": "Program", "label": "Band open lights",
     "help": "Per band, what skimmers on your continent hear (10 min): '20m: 34 DX 85%'; "
             "the colour goes from orange (only your continent) to green (all DX) "
             "with the DX share; grey = nothing."},
    {"key": "ui.look", "attr": "look", "kind": "choice", "choices": ["dark", "light", "classic"],
     "live": False, "group": "Program", "label": "Window look",
     "help": "dark / light = Windows 11 style (dark like the phone page); classic = the "
             "look up to v0.7.92. From the next start (View > Look)."},
    {"key": "ui.self_spot_alert", "attr": "self_spot_alert", "kind": "bool", "live": True,
     "group": "Program", "label": "Pop-up when someone spots my call",
     "help": "A spot of your call (the log's station call) by a person, not a skimmer, "
             "from any source - a Telnet, N1MM's spots, the team master: 'SPOTTED: S55OO by "
             "DL1ABC on 14025.0'. Your self-spot shows too (marked). Once per spotter and "
             "QRG / 15 min."},
    {"key": "ui.self_spot_band_only", "attr": "self_spot_band_only", "kind": "bool", "live": True,
     "group": "Program", "label": "SPOTTED only on the PC on that band",
     "help": "My call spotted on 20 m: the pop-up only on the PC whose radio is on 20 m; the "
             "others log it and show it in the status line. Off = every PC."},
    {"key": "ui.internet_check", "attr": "internet_check", "kind": "bool", "live": False,
     "group": "Program", "label": "Watch the internet (red bar when it's down)",
     "help": "Every 30 s a plain connect to 1.1.1.1 / 8.8.8.8 / 9.9.9.9 (nothing sent). Down: a "
             "red bar 'Internet down since 12:03', the last spots stay in the list greyed out "
             "(not aged out), the phone shows it too; every connection retries at once when "
             "it's back. From the next start."},
    {"key": "ui.rate_line", "attr": "rate_line", "kind": "bool", "live": False, "group": "Program",
     "label": "Rate / mults line",
     "help": "Window: a line with the rate (last 10 min / hour), QSOs and multipliers per "
             "band from the open N1MM log, every 30 s. Also View menu."},
    {"key": "ui.layout", "attr": "layout", "kind": "choice", "choices": ["clean", "detailed"],
     "live": True, "group": "Program", "label": "Window layout",
     "help": "clean (default): the spots first - one row of lights with one sentence when "
             "something is wrong (click it: details), the call big in the list. detailed: "
             "every status line (check line, N1MM / RBN / Log lines, rate line) and the table "
             "as before. View > Detailed layout."},
    {"key": "ui.spot_tiles", "attr": "tiles", "kind": "bool", "live": True, "group": "Program",
     "label": "Spot list as tiles",
     "help": "The window's spot list as tiles like the phone page (call big, green = "
             "strong, pink bar = new mult); off (default) = the table with columns in small "
             "fonts. View menu / Ctrl+T."},
    {"key": "ui.alert_popup", "attr": "alert_popup", "kind": "bool",
     "live": True, "group": "Program", "label": "Alert pop-up (history hint + new mult)",
     "help": "Window: a yellow pop-up when a historical opportunity hint (⚡) that is also a new multiplier "
             "appears on your band; click it to QSY and call. Never takes the keyboard "
             "from N1MM. (The phone has its own switches in the band menu.)"},
    {"key": "ui.alert_sound", "attr": "alert_sound", "kind": "bool",
     "live": True, "group": "Program", "label": "Alert sound",
     "help": "Window: two short beeps for the same alert - on its own, with or without "
             "the pop-up."},
    {"key": "ui.fkeys", "attr": "fkeys_text", "kind": "text", "live": False,
     "group": "Program", "label": "TX / log keys",
     "help": "In this order, KEY:label - F1-F12 (transmit), Enter (log), SpotIt (N1MM Alt+P). "
             "e.g. F1:CQ F2:RPRT F3:TU F4:CALL F5:HisCALL F7:? F11:.. Enter SpotIt:SPOTit"},
    {"key": "ui.fkeys_half", "attr": "fkeys_half_text", "kind": "text", "live": False,
     "group": "Program", "label": "  ... half-size keys (phone)",
     "help": "Two half keys share one key's place, e.g. F1 F3 F5 F7 F11 SpotIt. Empty = all full size."},
    {"key": "ui.auto_update", "attr": "auto_update", "kind": "bool", "live": False,
     "group": "Program", "label": "Auto-update from GitHub", "help": ""},
]
# ---- pages: how the Settings window and the /settings page show them ----
# page -> [(part, [setting keys])]; a key not listed here lands on "Other"
PAGES: list[tuple[str, list[tuple[str, list[str]]]]] = [
    ("Display", [
        ("Window", ["ui.layout", "ui.look", "ui.zo_mode", "ui.spot_tiles", "ui.band_lights",
                    "ui.check_line", "ui.rate_line", "ui.internet_check"]),
        ("Spot list", ["ui.min_snr", "ui.min_snr_keep_mults", "ui.mark_tried",
                       "ui.tried_minutes", "scp.enabled"]),
        ("Keys", ["ui.fkeys", "ui.fkeys_half"]),
    ]),
    ("Alerts & history", [
        ("Pop-ups", ["ui.alert_popup", "ui.alert_sound", "ui.self_spot_alert",
                     "ui.self_spot_band_only"]),
        ("History hints", ["worked.profile", "ui.urgency_contest_minutes"]),
    ]),
    ("N1MM & radios", [
        ("N1MM connection", ["n1mm.listen_port"]),
        ("Tap -> N1MM", ["n1mm.qsy_wait_ms", "n1mm.qsy_settle_ms", "n1mm.type_mode",
                         "n1mm.wipe_before_call", "n1mm.focus_n1mm"]),
        ("SO2R / SO2V", ["n1mm.default_radio", "n1mm.radio_b_qsy", "n1mm.freq_decimal",
                         "n1mm.return_focus", "n1mm.focus_back_to_a"]),
        ("CW speed", ["n1mm.cw_speed", "n1mm.cw_speed_popup"]),
    ]),
    ("Phone page", [
        ("", ["web.phone_control", "web.cw_keyboard"]),
    ]),
    ("Telnet", [
        ("", ["telnet1.enabled", "telnet1.host", "telnet1.port", "telnet1.login",
              "telnet1.role", "telnet1.commands",
              "telnet2.enabled", "telnet2.host", "telnet2.port", "telnet2.login",
              "telnet2.role", "telnet2.commands",
              "telnet3.enabled", "telnet3.host", "telnet3.port", "telnet3.login",
              "telnet3.role", "telnet3.commands",
              "telnet4.enabled", "telnet4.host", "telnet4.port", "telnet4.login",
              "telnet4.role", "telnet4.commands",
              "telnet5.enabled", "telnet5.host", "telnet5.port", "telnet5.login",
              "telnet5.role", "telnet5.commands"]),
    ]),
    ("Spots", [
        ("Exclude", ["rbn.hide_warc", "rbn.hide_60m", "rbn.hide_beacons"]),
        ("Sources", ["rbn.sources", "rbn.history",
                     "rbn.serve_own_spots",
                     "rbn.backup_off",
                     "rbn.pause_on_phone",
                     "rbn.skimmer_quiet_minutes", "rbn.telnet_silent_minutes",
                     "rbn.http_backup", "rbn.hamqth_backup"]),
        ("SSB", ["ssb.follow_radio_mode", "ssb.pskreporter"]),
        ("Spot collector", ["cluster.trust_q", "cluster.people_all"]),
        ("Spotters - who heard it", ["filter.mode", "rbn.spotter", "filter.dxcc",
                                     "filter.zones", "filter.conts"]),
        ("Spotted - who was heard", ["spotted.mode", "spotted.calls", "spotted.dxcc",
                                     "spotted.zones", "spotted.conts"]),
    ]),
    ("Cluster server", [
        ("", ["cluster.enabled", "cluster.port", "cluster.relay", "cluster.merge"]),
    ]),
    ("Updates", [
        ("Country file & Super Check Partial", ["cty.auto_update", "cty.update_n1mm",
                                                "scp.update_n1mm"]),
        ("Updates", ["ui.auto_update"]),
    ]),
]


def place(key: str) -> tuple[str, str]:
    """(page, part) of a setting; not in PAGES: ('Other', its own group)."""
    return _PLACE.get(key, ("Other", BY_KEY.get(key, {}).get("group", "")))


BY_KEY = {s["key"]: s for s in SETTINGS}
_PLACE = {k: (page, part) for page, parts in PAGES for part, keys in parts for k in keys}
_ORDER = {k: i for i, k in enumerate(k for _p, parts in PAGES for _q, keys in parts for k in keys)}



def _show(item: dict, value) -> object:
    if item.get("source_key"):          # telnetN.enabled: "on" in [rbn] sources (a CSV)
        return item["source_key"] in set(filter(None, str(value or "").split(",")))
    if item["attr"] == "default_radio":
        return "B" if value == 2 else "A"
    if item["attr"].endswith("_commands"):
        return ", ".join(value or ())
    return value


def current(cfg) -> list[dict]:
    """Every setting with its value now (for the page)."""
    out = []
    # in page order (PAGES); a setting not placed there comes last, under "Other"
    for item in sorted(SETTINGS, key=lambda it: _ORDER.get(it["key"], len(_ORDER))):
        d = {k: v for k, v in item.items() if k != "attr"}
        d["group"], d["part"] = place(item["key"])
        d["value"] = _show(item, getattr(cfg, item["attr"], None))
        out.append(d)
    return out


def parse(item: dict, text: str):
    """(value for Config, text for config.ini); ValueError if not valid."""
    text = str(text).strip()
    kind = item["kind"]
    if kind == "int":
        v = int(text)
        if not item.get("min", v) <= v <= item.get("max", v):
            raise ValueError(f"{item['label']}: {item.get('min')}-{item.get('max')}")
        return v, str(v)
    if kind == "bool":
        v = text.lower() in ("1", "true", "yes", "on")
        if text.lower() not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
            raise ValueError(f"{item['label']}: true or false")
        return v, "true" if v else "false"
    if kind == "choice":
        low = {c.lower(): c for c in item["choices"]}
        if text.lower() not in low:
            raise ValueError(f"{item['label']}: one of {', '.join(item['choices'])}")
        c = low[text.lower()]
        if item["attr"] == "default_radio":
            return parse_radio(c, 1), c
        if item["attr"] == "n1mm_listen_port":
            return int(c), c
        return c, c
    if item["attr"].endswith("_commands"):
        parts = tuple(p.strip() for p in text.split(",") if p.strip())
        return parts, ", ".join(parts)
    if item["attr"] == "rbn_http_backup" and text and not text.lower().startswith(("http://", "https://")):
        raise ValueError(f"{item['label']}: an http(s):// address, or empty")
    if any(ch in text for ch in "\r\n[]"):
        raise ValueError(f"{item['label']}: no line breaks or brackets")
    if item["attr"] in ("rbn_spotter", "ssb_spotters", "telnet1_login", "telnet2_login",
                        "telnet3_login", "telnet4_login", "telnet5_login", "filter_conts",
                        "spotted_calls", "spotted_conts"):
        text = text.upper()
    return text, text


_HEADER = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def set_ini_value(path: str, section: str, key: str, value: str) -> None:
    """Write ``key = value`` into ``[section]`` of an ini file, keeping all
    comments and other lines; adds the key (or the section) if missing."""
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*[=:]", re.IGNORECASE)
    start = end = None
    for n, line in enumerate(lines):
        m = _HEADER.match(line)
        if m:
            if start is not None:
                end = n
                break
            if m.group(1).strip().lower() == section.lower():
                start = n
    new = f"{key} = {value}"
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{section}]", new]
    else:
        end = len(lines) if end is None else end
        for n in range(start + 1, end):
            if key_re.match(lines[n]):
                lines[n] = new
                break
        else:
            at = end
            while at - 1 > start and not lines[at - 1].strip():
                at -= 1                      # before the blank lines at the end
            lines.insert(at, new)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def save(cfg, path: str, key: str, text: str) -> dict:
    """Validate, write config.ini, update the running config."""
    item = BY_KEY.get(key)
    if item is None:
        return {"ok": False, "error": f"unknown setting {key}"}
    try:
        value, ini_text = parse(item, text)
    except ValueError as exc:
        return {"ok": False, "error": str(exc) or "not valid"}
    if not path:
        return {"ok": False, "error": "no config.ini path"}
    section, name = key.split(".", 1)
    try:
        set_ini_value(path, section, name, ini_text)
    except OSError as exc:
        return {"ok": False, "error": f"could not write config.ini: {exc}"}
    setattr(cfg, item["attr"], value)
    return {"ok": True, "key": key, "value": _show(item, value), "live": item["live"]}
