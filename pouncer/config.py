"""Configuration loading for POUNCer."""

from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field

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
    # where the spots come in from - each on/off (Spots menu), comma
    # separated: rbn (reversebeacon.net), telnet / telnet2 / telnet3 / telnet4 /
    # telnet5 (Telnet #1 - #5: [telnet1] .. [telnet5], skimmer AND people's spots), http
    rbn_source: str = "rbn,telnet"
    rbn_hamqth_backup: bool = False  # HamQTH: its RBN / cluster APIs while the telnets are down
    # Telnet #1 - #5 (v0.7.213, were "Telnet" = [rbn] backup_host and
    # "Telnet 2" = [ssb] host; #5: v0.7.235): equal sources, each its own connection
    telnet1_host: str = "azure.s53m.com"
    telnet1_port: int = 8000
    telnet1_login: str = ""          # empty = the RBN callsign
    telnet1_commands: tuple = ()     # sent after login, e.g. set/skimmer
    telnet2_host: str = ""
    telnet2_port: int = 8000
    telnet2_login: str = ""
    telnet2_commands: tuple = ()
    telnet3_host: str = ""
    telnet3_port: int = 8000
    telnet3_login: str = ""
    telnet3_commands: tuple = ()
    telnet4_host: str = ""
    telnet4_port: int = 8000
    telnet4_login: str = ""
    telnet4_commands: tuple = ()
    telnet5_host: str = ""
    telnet5_port: int = 8000
    telnet5_login: str = ""
    telnet5_commands: tuple = ()
    telnet1_role: str = "cluster"
    telnet2_role: str = "cluster"
    telnet3_role: str = "cluster"
    telnet4_role: str = "cluster"
    telnet5_role: str = "cluster"
    spot_processing: dict = field(default_factory=dict)
    # RBN over HTTP (S53ZO's Azure feed) while the telnet feed is down / silent
    rbn_http_backup: str = "https://azure.s53m.com/cors/rbn/live"
    # close the RBN connection while every radio is on SSB / AM / FM (phone
    # spots come from the DX cluster); back at once when a radio is on CW
    rbn_pause_on_phone: bool = True
    # Super Check Partial: "?" on spotted calls not in MASTER.SCP; kept fresh
    scp_enabled: bool = True
    scp_url: str = "https://azure.s53m.com/cors/MASTER.DTA"   # S53M, every Friday
    scp_refresh_days: int = 7
    scp_update_n1mm: bool = False       # also copy the new file into N1MM's SupportFiles
    # Backup when the chosen skimmer is quiet: listed prefixes, or (empty)
    # all skimmers on our continent; after this many minutes of silence
    backup_spotters: tuple[str, ...] = ()
    backup_after_minutes: int = 5
    backup_off: bool = False      # [rbn] backup_off: never the backup skimmers
    skimmer_quiet_minutes: int = 3   # own skimmer silent this long = warning
    telnet_silent_minutes: int = 5   # [rbn] telnet_silent_minutes: nothing received = reconnect; 0 = off
    cty_file: str = ""
    cty_auto_update: bool = True     # S53M's cty.dat, downloaded every Friday
    cty_check_days: int = 5          # ... this often
    cty_url: str = "https://azure.s53m.com/cors/cty.dat"
    cty_update_n1mm: bool = False    # also copy it into N1MM's SupportFiles
    hide_beacons: bool = True
    hide_warc: bool = False        # [rbn] hide_warc: no spots on 30 / 17 / 12 m
    hide_60m: bool = False         # [rbn] hide_60m: no spots on 60 m (unset: as hide_warc)
    serve_own_spots: bool = False   # [rbn] serve_own_spots: a spot of my own call on
                                    # port 10000 too (v0.7.233); off = SPOTTED pop-up only
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
    self_spot_alert: bool = True  # pop-up when a person spots my call (any source)
    self_spot_band_only: bool = True  # SPOTTED pop-up only on the PC whose radio is on that band
    internet_check: bool = True     # red 'internet down since …' bar, last spots kept greyed
    band_lights: bool = True     # band open lights (skimmers on our continent, 10 min)
    # window alert when a rare CQ (⚡) that is also a new multiplier shows up
    # on the band: a pop-up (click it = QSY) and / or a beep, each on its own
    look: str = "dark"            # window look: dark / light (Windows 11 style) / classic
    tiles: bool = False           # window spot list as tiles (like the phone); False = table
    layout: str = "clean"         # window: clean (spots first) / detailed (every status line)
    alert_popup: bool = True
    alert_sound: bool = False
    # [autostart] programs of an old config.ini (they start with N1MMautostarter.exe now)
    legacy_autostart: int = 0
    max_age_minutes: int = 10
    fade_minutes: int = 5  # phone: tile fades when not re-spotted this long
    trigger: str = "single"

    # SSB: human DX-cluster spots (RBN has no SSB) - auto = when a radio is
    # on USB/LSB (a team master: any team radio; 60 s grace), always, off
    ssb_mode: str = "auto"
    ssb_spotters: str = "EU"     # spotter continent (EU, NA, ... or ALL)
    ssb_pskreporter: bool = True  # 7074-7077 SSB spot: ask PSK Reporter if he is on FT8
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
    cluster_merge: bool = True      # receiver-aware selection for list, team and port 10000
    cluster_trust_q: int = 3        # [cluster] trust_q: Q:n from this on = call confirmed; 0 = off
    cluster_people_all: bool = True  # [cluster] people_all: people's spots (no -#) all out, any mode
    touch_view: bool = False  # open the phone page in the default browser at start


def parse_radio(text: str, fallback: int = 1) -> int:
    """"A" / "1" -> 1, "B" / "2" -> 2; anything else -> fallback (radionr)."""
    t = (text or "").strip().upper()
    if t in ("A", "1"):
        return 1
    if t in ("B", "2"):
        return 2
    return 2 if fallback == 2 else 1


SOURCES = ("rbn", "telnet", "telnet2", "telnet3", "telnet4", "telnet5", "http")
TELNETS = ("telnet", "telnet2", "telnet3", "telnet4", "telnet5")  # Telnet #1 - #5
                                                                   # (#4: v0.7.215; #5: v0.7.235)
# older names: "cluster" was Telnet 2 (people's spots of [ssb] host)
SOURCE_ALIASES = {"cluster": "telnet2", "telnet1": "telnet", "telnet 2": "telnet2",
                  "telnet #1": "telnet", "telnet #2": "telnet2", "telnet #3": "telnet3",
                  "telnet #4": "telnet4", "telnet #5": "telnet5"}


def telnet_no(key: str) -> int:
    """'telnet' -> 1, 'telnet2' -> 2, 'telnet3' -> 3, 'telnet4' -> 4, 'telnet5' -> 5."""
    return TELNETS.index(key) + 1


def telnet_conf(cfg, n: int) -> tuple:
    """(host, port, login, commands) of Telnet #n; login '' = the RBN call."""
    return (str(getattr(cfg, f"telnet{n}_host", "") or "").strip(),
            int(getattr(cfg, f"telnet{n}_port", 8000) or 8000),
            str(getattr(cfg, f"telnet{n}_login", "") or "").strip(),
            tuple(getattr(cfg, f"telnet{n}_commands", ()) or ()))


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
    want = {SOURCE_ALIASES.get(w, w) for w in want}
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
    cfg.rbn_hamqth_backup = b("rbn", "hamqth_backup", cfg.rbn_hamqth_backup)
    if parser.has_option("rbn", "sources"):
        cfg.rbn_source = parse_sources(s("rbn", "sources", cfg.rbn_source))
    elif parser.has_option("rbn", "source"):   # older config.ini: one source (+ the SSB cluster)
        one = s("rbn", "source", "rbn").strip().lower()
        one = one if one in ("rbn", "telnet", "http") else "rbn"
        ssb_off = s("ssb", "mode", "auto").strip().lower() == "off"
        cfg.rbn_source = parse_sources(one + ("" if ssb_off else ",telnet"))
    # Telnet #1 - #5; an older config.ini: #1 = [rbn] backup_host / port /
    # commands, #2 = [ssb] host / port / login / commands (read, not rewritten)
    old = {1: ("rbn", "backup_host", "backup_port", None, "backup_commands"),
           2: ("ssb", "host", "port", "login", "commands")}
    for n in (1, 2, 3, 4, 5):
        sec = f"telnet{n}"
        osec, ohost, oport, ologin, ocmds = old.get(n, (None,) * 5)

        def pick(key, okey, default):
            if parser.has_option(sec, key):
                return s(sec, key, default)
            if osec and okey and parser.has_option(osec, okey):
                return s(osec, okey, default)
            return default
        setattr(cfg, f"{sec}_host", str(pick("host", ohost, getattr(cfg, f"{sec}_host"))).strip())
        try:
            setattr(cfg, f"{sec}_port", int(str(pick("port", oport, getattr(cfg, f"{sec}_port"))).strip()))
        except ValueError:
            pass
        role = s(sec, "role", "cluster").strip().lower()
        if role not in ("cluster", "local", "rbn", "gocluster"):
            raise ValueError(f"{sec}.role must be cluster, local, rbn or gocluster")
        setattr(cfg, f"{sec}_role", role)
        setattr(cfg, f"{sec}_login", str(pick("login", ologin, "")).strip().upper())
        setattr(cfg, f"{sec}_commands", tuple(
            c.strip() for c in str(pick("commands", ocmds, "")).split(",") if c.strip()))
    if not parser.has_section("telnet2") and \
            (cfg.telnet2_host.lower(), cfg.telnet2_port) == (cfg.telnet1_host.lower(), cfg.telnet1_port):
        cfg.telnet2_host = ""   # the old Telnet 2 = Telnet's own server: nothing extra, left empty
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
    cfg.backup_off = b("rbn", "backup_off", cfg.backup_off)
    cfg.skimmer_quiet_minutes = max(1, i("rbn", "skimmer_quiet_minutes", cfg.skimmer_quiet_minutes))
    cfg.telnet_silent_minutes = max(0, min(120, i("rbn", "telnet_silent_minutes",
                                                 cfg.telnet_silent_minutes)))
    cfg.cty_file = s("rbn", "cty", cfg.cty_file).strip()
    cfg.cty_auto_update = b("cty", "auto_update", cfg.cty_auto_update)
    cfg.cty_check_days = max(1, i("cty", "check_days", cfg.cty_check_days))
    cfg.cty_url = s("cty", "url", cfg.cty_url).strip() or cfg.cty_url
    cfg.cty_update_n1mm = b("cty", "update_n1mm", cfg.cty_update_n1mm)
    cfg.hide_beacons = b("rbn", "hide_beacons", cfg.hide_beacons)
    cfg.hide_warc = b("rbn", "hide_warc", cfg.hide_warc)
    # 60 m was part of "WARC" until v0.7.245: unset, it follows hide_warc
    cfg.hide_60m = b("rbn", "hide_60m", cfg.hide_warc)
    cfg.serve_own_spots = b("rbn", "serve_own_spots", cfg.serve_own_spots)
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
    # autostart moved to N1MMautostarter.exe: only count what an old config.ini still has
    cfg.legacy_autostart = sum(1 for n in range(1, 9)
                               if parser.get("autostart", f"app{n}", raw=True, fallback="").strip())
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
    cfg.follow_radio_mode = b("ssb", "follow_radio_mode", cfg.follow_radio_mode)
    cfg.ssb_pskreporter = b("ssb", "pskreporter", cfg.ssb_pskreporter)
    cfg.web_enabled = b("web", "enabled", cfg.web_enabled)
    cfg.web_remote_settings = b("web", "remote_settings", cfg.web_remote_settings)
    cfg.web_cw_keyboard = b("web", "cw_keyboard", cfg.web_cw_keyboard)
    cfg.web_phone_control = phone_control_mode(s("web", "phone_control", cfg.web_phone_control))
    cfg.web_host = s("web", "host", cfg.web_host).strip()
    cfg.web_port = i("web", "port", cfg.web_port)
    cfg.cluster_server = b("cluster", "enabled", cfg.cluster_server)
    cfg.cluster_port = i("cluster", "port", cfg.cluster_port)
    cfg.cluster_relay = b("cluster", "relay", cfg.cluster_relay)
    cfg.cluster_merge = b("cluster", "merge", cfg.cluster_merge)
    cfg.cluster_trust_q = max(0, min(99, i("cluster", "trust_q", cfg.cluster_trust_q)))
    cfg.cluster_people_all = b("cluster", "people_all", cfg.cluster_people_all)
    from .spot_processing import DEFAULTS, SpotProcessor
    if parser.has_section("spot_processing"):
        for key, text in parser.items("spot_processing"):
            if key not in DEFAULTS or key == "enabled":
                raise ValueError(f"Unknown spot_processing setting {key}; use cluster.merge to enable")
            integer = key in ("max_entries", "max_changes", "calibration_min_calls", "calibration_retry_batch")
            cfg.spot_processing[key] = int(text) if integer else float(text)
        SpotProcessor(cfg.spot_processing)  # fail early on invalid policy


    if cfg.sort not in ("time", "freq", "snr", "call", "cont", "mult", "mode", "type", "spotter"):
        cfg.sort = "snr"
    if cfg.trigger not in ("single", "double"):
        cfg.trigger = "single"
    return cfg
