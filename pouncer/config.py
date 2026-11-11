"""Configuration loading for POUNCer."""

from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass

from .rbn import MODES


def parse_modes(text: str, default: tuple[str, ...] = ("CW",)) -> tuple[str, ...]:
    """Parse a comma/space separated mode list into the valid modes present."""
    seen: list[str] = []
    for part in re.split(r"[,\s]+", text or ""):
        p = part.strip().upper()
        if p in MODES and p not in seen:
            seen.append(p)
    return tuple(seen) or default


# key lists older POUNCers wrote as their default: read as "not changed"
OLD_DEFAULT_FKEYS = (
    (("F2", "RPRT"), ("F3", "TU"), ("F4", "CALL"), ("F5", "HisCALL"), ("F7", "?"), ("Enter", "Enter")),
    (("F2", "RPRT"), ("F3", "TU"), ("F1", "QRL?"), ("F4", "CALL"), ("F5", "HisCALL"), ("F7", "?"),
     ("Enter", "Enter")),
    (("F2", "RPRT"), ("F3", "TU"), ("F1", "QRL?"), ("F4", "CALL"), ("F5", "HisCALL"), ("F7", "?"),
     ("Enter", "Enter"), ("SpotIt", "SPOTit")),
    (("F1", "QRL?"), ("F3", "TU"), ("F2", "RPRT"), ("F4", "CALL"), ("F5", "HisCALL"), ("F7", "?"),
     ("Enter", "Enter"), ("SpotIt", "SPOTit")),
    (("F1", "QRL?"), ("F3", "TU"), ("F2", "RPRT"), ("F4", "CALL"), ("F5", "HisCALL"), ("F7", "?"),
     ("F11", "F11"), ("Enter", "Enter"), ("SpotIt", "SPOTit")),
)
# half-size key lists older POUNCers wrote as their default (upper-case)
OLD_DEFAULT_FKEYS_HALF = (
    ("F1", "F3", "F5", "F7", "SPOTIT"),
)


def parse_fkeys(text: str) -> tuple[tuple[str, str], ...]:
    """Parse a key list like ``F4:MyCall, F1:CQ, Enter:Log`` into (key, label).

    Entries without a label use the key itself as the label. Keys may be
    F1-F12 or ``Enter`` (a QSO-log key); anything else is ignored. Order is
    preserved and duplicates are dropped.
    """
    out: list[tuple[str, str]] = []
    for part in re.split(r"[,;\s]+", text or ""):
        part = part.strip()
        if not part:
            continue
        key, _, label = part.partition(":")
        key = key.strip()
        if re.fullmatch(r"F([1-9]|1[0-2])", key.upper()):
            key = key.upper()
        elif key.lower() in ("enter", "return", "cr"):
            key = "Enter"
        elif key.lower() in ("spotit", "spot", "alt+p", "altp"):
            key = "SpotIt"                 # N1MM Alt+P: Spot It (no TX)
        else:
            continue
        label = label.strip() or key
        if all(key != k for k, _ in out):
            out.append((key, label))
    return tuple(out)


@dataclass
class Config:
    rbn_host: str = "telnet.reversebeacon.net"
    rbn_port: int = 7000
    rbn_callsign: str = ""  # empty: taken from N1MM (see app)
    rbn_spotter: str = "S53WW"
    # the one spotter filter for every source (the Spotters buttons):
    # calls (rbn_spotter) / dxcc / zones / cont / ww
    filter_mode: str = "calls"
    filter_dxcc: str = ""
    filter_zones: str = ""
    filter_conts: str = "EU"
    # the Spotted filter: which spotted stations (the DX) are kept
    spotted_mode: str = "ww"
    spotted_calls: str = ""
    spotted_dxcc: str = ""
    spotted_zones: str = ""
    spotted_conts: str = ""
    rbn_history: int = 30
    # backup telnet server when RBN's own telnet is down (CW feed)
    rbn_backup_host: str = "azure.s53m.com"
    # where the spots come in from - each on/off (the Spots buttons), comma
    # separated: rbn (reversebeacon.net), telnet (backup_host: its skimmer
    # spots), http (http_backup), cluster (the DX cluster's spots of people)
    rbn_source: str = "rbn,cluster"
    rbn_telnet_backup: bool = False  # Telnet-bck: the telnet server while RBN is down
    rbn_hamqth_backup: bool = False  # HamQTH: its RBN / cluster APIs while the telnets are down
    rbn_backup_port: int = 8000
    rbn_backup_commands: tuple = ()     # at start ask RBN for the last spots (SH/DX); 0 = off
    # RBN over HTTP (S53ZO's Azure feed) while the telnet feed is down / silent
    rbn_http_backup: str = "https://azure.s53m.com/cors/rbn/live"
    # close the RBN connection while every radio is on SSB / AM / FM (phone
    # spots come from the DX cluster); back at once when a radio is on CW
    rbn_pause_on_phone: bool = True
    # Super Check Partial: "?" on spotted calls not in MASTER.SCP; kept fresh
    scp_enabled: bool = True
    scp_url: str = "https://www.supercheckpartial.com/MASTER.SCP"
    scp_refresh_days: int = 7
    scp_update_n1mm: bool = False       # also copy the new file into N1MM's SupportFiles
    # Backup when the chosen skimmer is quiet: listed prefixes, or (empty)
    # all skimmers on our continent; after this many minutes of silence
    backup_spotters: tuple[str, ...] = ()
    backup_after_minutes: int = 5
    skimmer_quiet_minutes: int = 3   # own skimmer silent this long = warning
    cty_file: str = ""
    cty_auto_update: bool = True     # check country-files.com for a newer wl_cty.dat
    cty_check_days: int = 5          # ... this often
    cty_url: str = "https://www.country-files.com/cty/wl_cty.dat"
    cty_update_n1mm: bool = False    # also copy it into N1MM's SupportFiles
    hide_beacons: bool = True
    follow_modes: tuple[str, ...] = ("CW",)
    rbn_ft8_port: int = 7001

    n1mm_listen_ip: str = "0.0.0.0"
    n1mm_listen_port: int = 12060
    n1mm_control_ip: str = "127.0.0.1"
    n1mm_control_port: int = 13064
    n1mm_radionr: int = 1
    default_radio: int = 1  # SO2R: radio a tap goes to at start (1 = A, 2 = B)
    radio_b_qsy: str = "udp-left"  # SO2R/SO2V radio B QSY: "udp-left" (UDP radio 2, Left), "type", "udp"
    freq_decimal: str = "auto"  # typed frequency: auto (Windows setting), "." or ","
    set_callsign: bool = True
    wipe_before_call: bool = True
    focus_n1mm: bool = True
    return_focus: bool = True  # SO2R: after a tap on the other radio, cursor back where it was
    focus_back_to_a: bool = False  # SO2R/SO2V: after a radio B tap, always to Entry Window A
    cw_speed: bool = True       # read N1MM's CW speed (WPM) from the Entry Window's speed box
    cw_speed_popup: bool = True  # ... and show a popup with the new speed when it changes
    type_mode: str = "settext"  # how calls / frequencies go into N1MM: settext or keys
    qsy_wait_ms: int = 2500    # after a QSY: wait (max) until N1MM reports the new frequency
    qsy_settle_ms: int = 250   # ... then this long before wiping / typing the call
    n1mm_ini: str = ""

    worked_profile: str = "auto"
    worked_db: str = ""
    worked_hide: bool = True
    worked_refresh_seconds: int = 10

    fkeys: tuple[tuple[str, str], ...] = (
        ("F1", "CQ"), ("F2", "RPRT"), ("F3", "TU"), ("F4", "CALL"), ("F5", "HisCALL"),
        ("F7", "?"), ("F11", ".."), ("Enter", "Enter"), ("SpotIt", "SPOTit"),
    )
    fkeys_text: str = ""                 # the [ui] fkeys line as written (Settings)
    fkeys_half_text: str = ""
    # phone page: these keys are half as tall (F3 + F1, F5 + F7 share one key's place)
    fkeys_half: tuple[str, ...] = ("F1", "F3", "F5", "F7", "F11", "SPOTIT")

    tune_step_hz: int = 25
    # RX filter width assumed at start (N1MM only toggles): narrow/wide/""
    filter_start: str = "narrow"
    # RX filter by CAT (sent via N1MM {CATnASC ...}); empty = Alt+' toggle
    filter_narrow_cat: str = ""
    filter_wide_cat: str = ""
    # Run/S&P history from the public contest-log archive (runhist.py)
    zo_mode: bool = False
    urgency_contest_minutes: int = 0  # 0: verified duration table; otherwise explicit override
    run_history: bool = True
    run_history_dir: str = ""  # default: "cache" next to config.ini
    run_server: str = ""  # optional run list server URL (server/runlist.py)
    run_list: str = "runlist.sqlite"  # ready-made run list file (next to config.ini)
    warmup: bool = True  # download the warm-up lists (big CW and SSB contests) at start
    auto_update: bool = True  # exe: download a newer release, used from the next start
    forward_fkeys: bool = True  # F1-F12 pressed in POUNCer go to N1MM
    team: bool = True       # work together with other POUNCers on the network
    team_port: int = 12079
    team_peers: tuple[str, ...] = ()  # extra POUNCer IPs (networks without broadcast)
    team_key: str = ""                # team password key (hex, never the password); "" = off
    team_master: bool = False         # take the master role back on (re)joining the team
    sort: str = "snr"
    min_snr: int = 0             # list (window + phone): hide spots weaker than this (0 = all)
    min_snr_keep_mults: bool = True  # ... but never hide a new multiplier
    mark_tried: bool = True      # mark stations already tapped (called): "↻3m"
    tried_minutes: int = 30      # ... for this long after the last tap
    check_line: bool = True      # pre-contest check line in the window (+ phone warning)
    rate_line: bool = True       # window: rate / QSOs / mults per band from the log
    self_spot_alert: bool = True  # pop-up when a person spots my call on a cluster
    self_spot_band_only: bool = True  # SPOTTED pop-up only on the PC whose radio is on that band
    internet_check: bool = True     # red 'internet down since …' bar, last spots kept greyed
    # its own cluster connection, filtered on the node to spots of my call only
    self_spot_host: str = "azure.s53m.com"
    self_spot_port: int = 8000
    self_spot_filter: str = "accept/spots call {call}"
    self_spot_ssid: str = "77"
    band_lights: bool = True     # band open lights (skimmers on our continent, 10 min)
    # window alert when a rare CQ (⚡) that is also a new multiplier shows up
    # on the band: a pop-up (click it = QSY) and / or a beep, each on its own
    look: str = "dark"            # window look: dark / light (Windows 11 style) / classic
    tiles: bool = False           # window spot list as tiles (like the phone); False = table
    layout: str = "clean"         # window: clean (spots first) / detailed (every status line)
    alert_popup: bool = True
    alert_sound: bool = False
    # [autostart]: your other programs, started with POUNCer (see autostart.py)
    autostart: bool = True
    # programs 1-8: full path (+ args after a quoted path), seconds after the
    # one before (app1: after POUNCer's start), wait until N1MM is up first
    autostart_app1: str = ""
    autostart_delay1: int = 5
    autostart_wait1: bool = False
    autostart_app2: str = ""
    autostart_delay2: int = 5
    autostart_wait2: bool = False
    autostart_app3: str = ""
    autostart_delay3: int = 5
    autostart_wait3: bool = False
    autostart_app4: str = ""
    autostart_delay4: int = 5
    autostart_wait4: bool = False
    autostart_app5: str = ""
    autostart_delay5: int = 5
    autostart_wait5: bool = False
    autostart_app6: str = ""
    autostart_delay6: int = 5
    autostart_wait6: bool = False
    autostart_app7: str = ""
    autostart_delay7: int = 5
    autostart_wait7: bool = False
    autostart_app8: str = ""
    autostart_delay8: int = 5
    autostart_wait8: bool = False
    autostart_folder: str = "autostart"             # next to config.ini
    autostart_folder_delay: int = 3
    autostart_wait_n1mm: bool = True    # N1MM listed first: wait for it before the rest
    autostart_n1mm_timeout: int = 180   # ... at most this many seconds
    max_age_minutes: int = 10
    fade_minutes: int = 5  # phone: tile fades when not re-spotted this long
    trigger: str = "single"

    # SSB: human DX-cluster spots (RBN has no SSB) - auto = when a radio is
    # on USB/LSB (a team master: any team radio; 60 s grace), always, off
    ssb_mode: str = "auto"
    ssb_host: str = "azure.s53m.com"
    ssb_port: int = 8000
    ssb_spotters: str = "EU"     # spotter continent (EU, NA, ... or ALL)
    ssb_history: int = 30
    ssb_login: str = ""          # empty = the RBN callsign
    ssb_commands: tuple = ()
    ssb_pskreporter: bool = True  # 7074-7077 SSB spot: ask PSK Reporter if he is on FT8
    ssb_use_n1mm: str = "auto"   # auto = N1MM's spot packets replace our cluster while they come; off
    follow_radio_mode: bool = True   # radio on USB/LSB -> SSB spots, CW -> CW
    web_enabled: bool = True
    web_remote_settings: bool = False  # settings page: change from other devices too
    web_cw_keyboard: bool = True  # phone: hold ENTER 1.3 s = CW keyboard (N1MM Ctrl+K, transmits)
    # phones controlling N1MM (QSY / keys / CW): "phone" = each phone's own
    # switch (off until set), "all" = every phone, no switch needed, "off" =
    # none (only this PC's browser). A team master's choice counts for all PCs.
    web_phone_control: str = "phone"
    config_path: str = ""              # the config.ini that was read (settings page writes it)
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    cluster_server: bool = False    # serve POUNCer's spots as a telnet DX cluster
    cluster_port: int = 10000
    cluster_relay: bool = True      # pass the real cluster through to the client (N1MM)
    cluster_spots_out: bool = True  # a client's (N1MM's) DX spots go on to the DX cluster
    touch_view: bool = False  # open the phone page in the default browser at start


def parse_radio(text: str, fallback: int = 1) -> int:
    """"A" / "1" -> 1, "B" / "2" -> 2; anything else -> fallback (radionr)."""
    t = (text or "").strip().upper()
    if t in ("A", "1"):
        return 1
    if t in ("B", "2"):
        return 2
    return 2 if fallback == 2 else 1


SOURCES = ("rbn", "telnet", "http", "cluster")


PHONE_CONTROL = ("phone", "all", "off")


def phone_control_mode(text) -> str:
    """[web] phone_control: 'phone' / 'all' / 'off' (older true / false read as
    'phone' / 'off'; anything else = 'phone')."""
    v = str(text or "").strip().lower()
    if v in ("all", "all phones", "everyone"):
        return "all"
    if v in ("off", "false", "no", "0", "none"):
        return "off"
    return "phone"


def parse_sources(text) -> str:
    """'Telnet, RBN,x' -> 'rbn,telnet': the known ones, in a fixed order,
    each once. Empty = none (no spots - the user's choice)."""
    want = {w.strip().lower() for w in str(text or "").replace(";", ",").split(",")}
    return ",".join(x for x in SOURCES if x in want)


def has_source(cfg, name: str) -> bool:
    return name in str(getattr(cfg, "rbn_source", "") or "").split(",")


def load_config(path: str) -> Config:
    cfg = Config()
    cfg.config_path = os.path.abspath(path) if path else ""
    parser = configparser.ConfigParser()
    if path and os.path.exists(path):
        parser.read(path, encoding="utf-8")
    else:
        parser.read_dict({})

    def s(section: str, key: str, default: str) -> str:
        return parser.get(section, key, fallback=default)

    def i(section: str, key: str, default: int) -> int:
        try:
            return parser.getint(section, key, fallback=default)
        except ValueError:
            return default

    def b(section: str, key: str, default: bool) -> bool:
        try:
            return parser.getboolean(section, key, fallback=default)
        except ValueError:
            return default

    cfg.rbn_host = s("rbn", "host", cfg.rbn_host)
    cfg.rbn_port = i("rbn", "port", cfg.rbn_port)
    cfg.rbn_callsign = s("rbn", "callsign", cfg.rbn_callsign).strip().upper()
    cfg.rbn_spotter = s("rbn", "spotter", cfg.rbn_spotter)
    cfg.rbn_history = max(0, i("rbn", "history", cfg.rbn_history))
    cfg.rbn_backup_host = s("rbn", "backup_host", cfg.rbn_backup_host).strip()
    cfg.rbn_telnet_backup = b("rbn", "telnet_backup", cfg.rbn_telnet_backup)
    cfg.rbn_hamqth_backup = b("rbn", "hamqth_backup", cfg.rbn_hamqth_backup)
    if parser.has_option("rbn", "sources"):
        cfg.rbn_source = parse_sources(s("rbn", "sources", cfg.rbn_source))
    else:    # older config.ini: one source (+ the SSB cluster, unless it was off)
        one = s("rbn", "source", "rbn").strip().lower()
        one = one if one in ("rbn", "telnet", "http") else "rbn"
        ssb_off = s("ssb", "mode", "auto").strip().lower() == "off"
        cfg.rbn_source = parse_sources(one + ("" if ssb_off else ",cluster"))
    cfg.rbn_backup_port = i("rbn", "backup_port", cfg.rbn_backup_port)
    cfg.rbn_backup_commands = tuple(
        c.strip() for c in s("rbn", "backup_commands", "").split(",") if c.strip())
    cfg.rbn_http_backup = s("rbn", "http_backup", cfg.rbn_http_backup).strip()
    cfg.rbn_pause_on_phone = b("rbn", "pause_on_phone", cfg.rbn_pause_on_phone)
    cfg.scp_enabled = b("scp", "enabled", cfg.scp_enabled)
    cfg.scp_url = s("scp", "url", cfg.scp_url).strip()
    cfg.scp_refresh_days = max(1, i("scp", "refresh_days", cfg.scp_refresh_days))
    cfg.scp_update_n1mm = b("scp", "update_n1mm", cfg.scp_update_n1mm)
    cfg.backup_spotters = tuple(
        b for b in s("rbn", "backup_spotters", "").replace(";", ",").replace(" ", ",").split(",") if b
    )
    cfg.backup_after_minutes = max(1, i("rbn", "backup_after_minutes", cfg.backup_after_minutes))
    cfg.skimmer_quiet_minutes = max(1, i("rbn", "skimmer_quiet_minutes", cfg.skimmer_quiet_minutes))
    cfg.cty_file = s("rbn", "cty", cfg.cty_file).strip()
    cfg.cty_auto_update = b("cty", "auto_update", cfg.cty_auto_update)
    cfg.cty_check_days = max(1, i("cty", "check_days", cfg.cty_check_days))
    cfg.cty_url = s("cty", "url", cfg.cty_url).strip() or cfg.cty_url
    cfg.cty_update_n1mm = b("cty", "update_n1mm", cfg.cty_update_n1mm)
    cfg.hide_beacons = b("rbn", "hide_beacons", cfg.hide_beacons)
    cfg.follow_modes = parse_modes(
        s("rbn", "follow_modes", ",".join(cfg.follow_modes))
    )
    cfg.rbn_ft8_port = i("rbn", "ft8_port", cfg.rbn_ft8_port)

    cfg.n1mm_listen_ip = s("n1mm", "listen_ip", cfg.n1mm_listen_ip)
    cfg.n1mm_listen_port = i("n1mm", "listen_port", cfg.n1mm_listen_port)
    cfg.n1mm_control_ip = s("n1mm", "control_ip", cfg.n1mm_control_ip)
    cfg.n1mm_control_port = i("n1mm", "control_port", cfg.n1mm_control_port)
    cfg.n1mm_radionr = i("n1mm", "radionr", cfg.n1mm_radionr)
    cfg.default_radio = parse_radio(s("n1mm", "default_radio", ""), cfg.n1mm_radionr)
    qb = s("n1mm", "radio_b_qsy", cfg.radio_b_qsy).strip().lower()
    cfg.radio_b_qsy = qb if qb in ("type", "udp-left", "udp") else "udp-left"
    fd = s("n1mm", "freq_decimal", cfg.freq_decimal).strip().lower()
    cfg.freq_decimal = fd if fd in ("auto", ".", ",") else "auto"
    cfg.set_callsign = b("n1mm", "set_callsign", cfg.set_callsign)
    cfg.wipe_before_call = b("n1mm", "wipe_before_call", cfg.wipe_before_call)
    cfg.focus_n1mm = b("n1mm", "focus_n1mm", cfg.focus_n1mm)
    cfg.return_focus = b("n1mm", "return_focus", cfg.return_focus)
    cfg.focus_back_to_a = b("n1mm", "focus_back_to_a", cfg.focus_back_to_a)
    cfg.cw_speed = b("n1mm", "cw_speed", cfg.cw_speed)
    cfg.cw_speed_popup = b("n1mm", "cw_speed_popup", cfg.cw_speed_popup)
    cfg.qsy_wait_ms = max(0, i("n1mm", "qsy_wait_ms", cfg.qsy_wait_ms))
    if cfg.qsy_wait_ms == 1500:      # the old default, unchanged: N1MM is often slower
        cfg.qsy_wait_ms = Config.qsy_wait_ms
    tm = s("n1mm", "type_mode", cfg.type_mode).strip().lower()
    cfg.type_mode = tm if tm in ("settext", "keys") else "settext"
    cfg.qsy_settle_ms = max(0, i("n1mm", "qsy_settle_ms", cfg.qsy_settle_ms))
    cfg.n1mm_ini = s("n1mm", "ini", cfg.n1mm_ini).strip()

    cfg.worked_profile = s("worked", "profile", cfg.worked_profile).strip().lower()
    if cfg.worked_profile not in ("auto", "off", "ttc", "mini40", "mini80", "mwc"):
        cfg.worked_profile = "auto"
    cfg.worked_db = s("worked", "db", cfg.worked_db).strip()
    cfg.worked_hide = b("worked", "hide", cfg.worked_hide)
    cfg.worked_refresh_seconds = i("worked", "refresh_seconds", cfg.worked_refresh_seconds)

    cfg.sort = s("ui", "sort", cfg.sort).strip().lower()
    cfg.min_snr = max(0, min(60, i("ui", "min_snr", cfg.min_snr)))
    cfg.min_snr_keep_mults = b("ui", "min_snr_keep_mults", cfg.min_snr_keep_mults)
    cfg.mark_tried = b("ui", "mark_tried", cfg.mark_tried)
    cfg.tried_minutes = max(1, min(720, i("ui", "tried_minutes", cfg.tried_minutes)))
    cfg.check_line = b("ui", "check_line", cfg.check_line)
    cfg.rate_line = b("ui", "rate_line", cfg.rate_line)
    cfg.self_spot_alert = b("ui", "self_spot_alert", cfg.self_spot_alert)
    cfg.self_spot_band_only = b("ui", "self_spot_band_only", cfg.self_spot_band_only)
    cfg.internet_check = b("ui", "internet_check", cfg.internet_check)
    cfg.self_spot_host = s("ssb", "self_spot_host", cfg.self_spot_host).strip()
    cfg.self_spot_port = i("ssb", "self_spot_port", cfg.self_spot_port)
    cfg.self_spot_filter = s("ssb", "self_spot_filter", cfg.self_spot_filter).strip()
    cfg.self_spot_ssid = s("ssb", "self_spot_ssid", cfg.self_spot_ssid).strip().lstrip("-")
    cfg.band_lights = b("ui", "band_lights", cfg.band_lights)
    lk = s("ui", "look", cfg.look).strip().lower()
    cfg.look = lk if lk in ("dark", "light", "classic") else "dark"
    # "spot_tiles" since v0.7.96: the old "tiles = true" (the v0.7.93 default) is
    # not read, so the window is back on the small-font table
    cfg.tiles = b("ui", "spot_tiles", cfg.tiles)
    lay = s("ui", "layout", cfg.layout).strip().lower()
    cfg.layout = lay if lay in ("clean", "detailed") else "clean"
    cfg.alert_popup = b("ui", "alert_popup", cfg.alert_popup)
    cfg.alert_sound = b("ui", "alert_sound", cfg.alert_sound)
    cfg.autostart = b("autostart", "enabled", cfg.autostart)
    # raw: Windows paths may hold a "%" (no ini interpolation)
    for n in range(1, 9):
        setattr(cfg, f"autostart_app{n}",
                parser.get("autostart", f"app{n}", raw=True, fallback="").strip())
        setattr(cfg, f"autostart_delay{n}", max(0, min(600, i("autostart", f"app{n}_delay", 5))))
        setattr(cfg, f"autostart_wait{n}", b("autostart", f"app{n}_wait_n1mm", False))
    cfg.autostart_folder = s("autostart", "folder", cfg.autostart_folder).strip()
    cfg.autostart_folder_delay = max(0, min(600, i("autostart", "folder_delay",
                                                   cfg.autostart_folder_delay)))
    cfg.autostart_wait_n1mm = b("autostart", "wait_n1mm", cfg.autostart_wait_n1mm)
    cfg.autostart_n1mm_timeout = max(10, min(900, i("autostart", "n1mm_timeout",
                                                    cfg.autostart_n1mm_timeout)))
    cfg.max_age_minutes = i("ui", "max_age_minutes", cfg.max_age_minutes)
    cfg.fade_minutes = max(1, i("ui", "fade_minutes", cfg.fade_minutes))
    cfg.tune_step_hz = max(1, i("ui", "tune_step_hz", cfg.tune_step_hz))
    fs = s("ui", "filter_start", cfg.filter_start).strip().lower()
    cfg.filter_start = fs if fs in ("narrow", "wide") else ""
    cfg.filter_narrow_cat = s("ui", "filter_narrow_cat", cfg.filter_narrow_cat).strip()
    cfg.filter_wide_cat = s("ui", "filter_wide_cat", cfg.filter_wide_cat).strip()
    cfg.zo_mode = b("ui", "zo_mode", cfg.zo_mode)
    cfg.urgency_contest_minutes = max(0, min(10080, i("ui", "urgency_contest_minutes", cfg.urgency_contest_minutes)))
    cfg.run_history = b("ui", "run_history", cfg.run_history)
    cfg.run_server = s("ui", "run_server", cfg.run_server).strip()
    cfg.run_list = s("ui", "run_list", cfg.run_list).strip()
    cfg.warmup = b("ui", "warmup", cfg.warmup)
    cfg.auto_update = b("ui", "auto_update", cfg.auto_update)
    cfg.forward_fkeys = b("ui", "forward_fkeys", cfg.forward_fkeys)
    cfg.team = b("team", "enabled", cfg.team)
    cfg.team_port = i("team", "port", cfg.team_port)
    cfg.team_master = b("team", "master", cfg.team_master)
    cfg.team_peers = tuple(
        p for p in s("team", "peers", "").replace(";", ",").replace(" ", ",").split(",") if p
    )
    key = s("team", "key", "").strip().lower()
    cfg.team_key = key if len(key) == 64 and all(c in "0123456789abcdef" for c in key) else ""
    cfg.trigger = s("ui", "trigger", cfg.trigger).strip().lower()
    cfg.touch_view = b("ui", "touch_view", cfg.touch_view)
    default_keys = cfg.fkeys
    cfg.fkeys = parse_fkeys(
        s("ui", "fkeys", s("web", "fkeys", ",".join(f"{k}:{v}" for k, v in cfg.fkeys)))
    )
    # an unchanged key list of an older POUNCer (the old default) gets the new
    # default keys (F1, SPOTit, ...) - a list you changed yourself stays
    if cfg.fkeys in OLD_DEFAULT_FKEYS:
        cfg.fkeys = default_keys
    cfg.fkeys_text = " ".join(k if v == k else f"{k}:{v}" for k, v in cfg.fkeys)
    default_half = cfg.fkeys_half
    half = s("ui", "fkeys_half", s("web", "fkeys_half", " ".join(cfg.fkeys_half)))
    cfg.fkeys_half = tuple(k.upper() for k in re.split(r"[,;\s]+", half) if k.strip())
    if cfg.fkeys_half in OLD_DEFAULT_FKEYS_HALF:      # unchanged old default: new keys too
        cfg.fkeys_half = default_half
    cfg.fkeys_half_text = " ".join(cfg.fkeys_half)

    sm = s("ssb", "mode", cfg.ssb_mode).strip().lower()
    cfg.ssb_mode = sm if sm in ("auto", "always", "off") else "auto"
    cfg.ssb_host = s("ssb", "host", cfg.ssb_host).strip()
    cfg.ssb_port = i("ssb", "port", cfg.ssb_port)
    cfg.ssb_spotters = s("ssb", "spotters", cfg.ssb_spotters).strip().upper() or "EU"
    # the Spotters filter; older config.ini: calls / zones from [rbn] spotter,
    # continents from [ssb] spotters
    from .rbn import parse_spotters
    old_calls, old_zones = parse_spotters(cfg.rbn_spotter)
    if parser.has_option("filter", "mode"):
        mode = s("filter", "mode", "calls").strip().lower()
    else:
        mode = "calls" if old_calls else ("zones" if old_zones else "ww")
    cfg.filter_mode = mode if mode in ("calls", "dxcc", "zones", "cont", "ww") else "calls"
    cfg.filter_dxcc = s("filter", "dxcc", cfg.filter_dxcc).strip()
    cfg.filter_zones = s("filter", "zones", ", ".join(str(z) for z in sorted(old_zones))).strip()
    old_cont = cfg.ssb_spotters if cfg.ssb_spotters not in ("ALL", "*", "HOME", "OWN") else "EU"
    cfg.filter_conts = s("filter", "conts", old_cont).strip().upper() or "EU"
    m2 = s("spotted", "mode", cfg.spotted_mode).strip().lower()
    cfg.spotted_mode = m2 if m2 in ("calls", "dxcc", "zones", "cont", "ww") else "ww"
    cfg.spotted_calls = s("spotted", "calls", cfg.spotted_calls).strip().upper()
    cfg.spotted_dxcc = s("spotted", "dxcc", cfg.spotted_dxcc).strip()
    cfg.spotted_zones = s("spotted", "zones", cfg.spotted_zones).strip()
    cfg.spotted_conts = s("spotted", "conts", cfg.spotted_conts).strip().upper()
    cfg.ssb_history = max(0, i("ssb", "history", cfg.ssb_history))
    cfg.ssb_login = s("ssb", "login", "").strip()
    cfg.ssb_commands = tuple(c.strip() for c in s("ssb", "commands", "").split(",") if c.strip())
    cfg.follow_radio_mode = b("ssb", "follow_radio_mode", cfg.follow_radio_mode)
    cfg.ssb_pskreporter = b("ssb", "pskreporter", cfg.ssb_pskreporter)
    un = s("ssb", "use_n1mm_spots", cfg.ssb_use_n1mm).strip().lower()
    cfg.ssb_use_n1mm = "off" if un in ("off", "false", "no", "0") else "auto"
    cfg.web_enabled = b("web", "enabled", cfg.web_enabled)
    cfg.web_remote_settings = b("web", "remote_settings", cfg.web_remote_settings)
    cfg.web_cw_keyboard = b("web", "cw_keyboard", cfg.web_cw_keyboard)
    cfg.web_phone_control = phone_control_mode(s("web", "phone_control", cfg.web_phone_control))
    cfg.web_host = s("web", "host", cfg.web_host).strip()
    cfg.web_port = i("web", "port", cfg.web_port)
    cfg.cluster_server = b("cluster", "enabled", cfg.cluster_server)
    cfg.cluster_port = i("cluster", "port", cfg.cluster_port)
    cfg.cluster_relay = b("cluster", "relay", cfg.cluster_relay)
    cfg.cluster_spots_out = b("cluster", "spots_out", cfg.cluster_spots_out)

    if cfg.sort not in ("time", "freq", "snr", "call", "cont", "mult", "mode", "type", "spotter"):
        cfg.sort = "snr"
    if cfg.trigger not in ("single", "double"):
        cfg.trigger = "single"
    return cfg
