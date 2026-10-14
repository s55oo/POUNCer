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
    rbn_history: int = 30
    # backup telnet server when RBN's own telnet is down (CW feed)
    rbn_backup_host: str = "azure.s53m.com"
    rbn_backup_port: int = 8000
    rbn_backup_commands: tuple = ()     # at start ask RBN for the last spots (SH/DX); 0 = off
    # Backup when the chosen skimmer is quiet: listed prefixes, or (empty)
    # all skimmers on our continent; after this many minutes of silence
    backup_spotters: tuple[str, ...] = ()
    backup_after_minutes: int = 5
    cty_file: str = ""
    hide_beacons: bool = True
    follow_modes: tuple[str, ...] = ("CW",)
    rbn_ft8_port: int = 7001

    n1mm_listen_ip: str = "0.0.0.0"
    n1mm_listen_port: int = 12060
    n1mm_control_ip: str = "127.0.0.1"
    n1mm_control_port: int = 13064
    n1mm_radionr: int = 1
    default_radio: int = 1  # SO2R: radio a tap goes to at start (1 = A, 2 = B)
    radio_b_qsy: str = "type"  # SO2R radio B QSY: "type" (kHz + Enter in Entry Window B) or "udp"
    freq_decimal: str = "auto"  # typed frequency: auto (Windows setting), "." or ","
    set_callsign: bool = True
    wipe_before_call: bool = True
    focus_n1mm: bool = True
    return_focus: bool = True
    type_mode: str = "settext"  # how calls / frequencies go into N1MM: settext or keys
    qsy_wait_ms: int = 1500    # after a QSY: wait (max) until N1MM reports the new frequency
    qsy_settle_ms: int = 250   # ... then this long before wiping / typing the call  # SO2R: after a tap on the other radio, cursor back where it was
    n1mm_ini: str = ""

    worked_db: str = ""
    worked_hide: bool = True
    worked_refresh_seconds: int = 10

    fkeys: tuple[tuple[str, str], ...] = (
        ("F2", "RPRT"), ("F3", "TU"), ("F4", "CALL"), ("F5", "HisCALL"), ("F7", "?"),
        ("Enter", "Enter"),
    )

    tune_step_hz: int = 25
    # RX filter width assumed at start (N1MM only toggles): narrow/wide/""
    filter_start: str = "narrow"
    # RX filter by CAT (sent via N1MM {CATnASC ...}); empty = Alt+' toggle
    filter_narrow_cat: str = ""
    filter_wide_cat: str = ""
    # Run/S&P history from the public contest-log archive (runhist.py)
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
    sort: str = "snr"
    max_age_minutes: int = 10
    fade_minutes: int = 5  # phone: tile fades when not re-spotted this long
    trigger: str = "single"

    # SSB: human DX-cluster spots (RBN has no SSB) - auto = when a radio is
    # on USB/LSB (and on a team master), always, off
    ssb_mode: str = "auto"
    ssb_host: str = "azure.s53m.com"
    ssb_port: int = 8000
    ssb_spotters: str = "EU"     # spotter continent (EU, NA, ... or ALL)
    ssb_history: int = 30
    ssb_login: str = ""          # empty = the RBN callsign
    ssb_commands: tuple = ()
    follow_radio_mode: bool = True   # radio on USB/LSB -> SSB spots, CW -> CW
    web_enabled: bool = True
    web_remote_settings: bool = False  # settings page: change from other devices too
    config_path: str = ""              # the config.ini that was read (settings page writes it)
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    touch_view: bool = False  # open the phone page in the default browser at start


def parse_radio(text: str, fallback: int = 1) -> int:
    """"A" / "1" -> 1, "B" / "2" -> 2; anything else -> fallback (radionr)."""
    t = (text or "").strip().upper()
    if t in ("A", "1"):
        return 1
    if t in ("B", "2"):
        return 2
    return 2 if fallback == 2 else 1


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
    cfg.rbn_backup_port = i("rbn", "backup_port", cfg.rbn_backup_port)
    cfg.rbn_backup_commands = tuple(
        c.strip() for c in s("rbn", "backup_commands", "").split(",") if c.strip())
    cfg.backup_spotters = tuple(
        b for b in s("rbn", "backup_spotters", "").replace(";", ",").replace(" ", ",").split(",") if b
    )
    cfg.backup_after_minutes = max(1, i("rbn", "backup_after_minutes", cfg.backup_after_minutes))
    cfg.cty_file = s("rbn", "cty", cfg.cty_file).strip()
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
    cfg.radio_b_qsy = qb if qb in ("type", "udp") else "type"
    fd = s("n1mm", "freq_decimal", cfg.freq_decimal).strip().lower()
    cfg.freq_decimal = fd if fd in ("auto", ".", ",") else "auto"
    cfg.set_callsign = b("n1mm", "set_callsign", cfg.set_callsign)
    cfg.wipe_before_call = b("n1mm", "wipe_before_call", cfg.wipe_before_call)
    cfg.focus_n1mm = b("n1mm", "focus_n1mm", cfg.focus_n1mm)
    cfg.return_focus = b("n1mm", "return_focus", cfg.return_focus)
    cfg.qsy_wait_ms = max(0, i("n1mm", "qsy_wait_ms", cfg.qsy_wait_ms))
    tm = s("n1mm", "type_mode", cfg.type_mode).strip().lower()
    cfg.type_mode = tm if tm in ("settext", "keys") else "settext"
    cfg.qsy_settle_ms = max(0, i("n1mm", "qsy_settle_ms", cfg.qsy_settle_ms))
    cfg.n1mm_ini = s("n1mm", "ini", cfg.n1mm_ini).strip()

    cfg.worked_db = s("worked", "db", cfg.worked_db).strip()
    cfg.worked_hide = b("worked", "hide", cfg.worked_hide)
    cfg.worked_refresh_seconds = i("worked", "refresh_seconds", cfg.worked_refresh_seconds)

    cfg.sort = s("ui", "sort", cfg.sort).strip().lower()
    cfg.max_age_minutes = i("ui", "max_age_minutes", cfg.max_age_minutes)
    cfg.fade_minutes = max(1, i("ui", "fade_minutes", cfg.fade_minutes))
    cfg.tune_step_hz = max(1, i("ui", "tune_step_hz", cfg.tune_step_hz))
    fs = s("ui", "filter_start", cfg.filter_start).strip().lower()
    cfg.filter_start = fs if fs in ("narrow", "wide") else ""
    cfg.filter_narrow_cat = s("ui", "filter_narrow_cat", cfg.filter_narrow_cat).strip()
    cfg.filter_wide_cat = s("ui", "filter_wide_cat", cfg.filter_wide_cat).strip()
    cfg.run_history = b("ui", "run_history", cfg.run_history)
    cfg.run_server = s("ui", "run_server", cfg.run_server).strip()
    cfg.run_list = s("ui", "run_list", cfg.run_list).strip()
    cfg.warmup = b("ui", "warmup", cfg.warmup)
    cfg.auto_update = b("ui", "auto_update", cfg.auto_update)
    cfg.forward_fkeys = b("ui", "forward_fkeys", cfg.forward_fkeys)
    cfg.team = b("team", "enabled", cfg.team)
    cfg.team_port = i("team", "port", cfg.team_port)
    cfg.team_peers = tuple(
        p for p in s("team", "peers", "").replace(";", ",").replace(" ", ",").split(",") if p
    )
    cfg.trigger = s("ui", "trigger", cfg.trigger).strip().lower()
    cfg.touch_view = b("ui", "touch_view", cfg.touch_view)
    cfg.fkeys = parse_fkeys(
        s("ui", "fkeys", s("web", "fkeys", ",".join(f"{k}:{v}" for k, v in cfg.fkeys)))
    )

    sm = s("ssb", "mode", cfg.ssb_mode).strip().lower()
    cfg.ssb_mode = sm if sm in ("auto", "always", "off") else "auto"
    cfg.ssb_host = s("ssb", "host", cfg.ssb_host).strip()
    cfg.ssb_port = i("ssb", "port", cfg.ssb_port)
    cfg.ssb_spotters = s("ssb", "spotters", cfg.ssb_spotters).strip().upper() or "EU"
    cfg.ssb_history = max(0, i("ssb", "history", cfg.ssb_history))
    cfg.ssb_login = s("ssb", "login", "").strip()
    cfg.ssb_commands = tuple(c.strip() for c in s("ssb", "commands", "").split(",") if c.strip())
    cfg.follow_radio_mode = b("ssb", "follow_radio_mode", cfg.follow_radio_mode)
    cfg.web_enabled = b("web", "enabled", cfg.web_enabled)
    cfg.web_remote_settings = b("web", "remote_settings", cfg.web_remote_settings)
    cfg.web_host = s("web", "host", cfg.web_host).strip()
    cfg.web_port = i("web", "port", cfg.web_port)

    if cfg.sort not in ("time", "freq", "snr", "wpm", "call", "cont", "mult", "mode", "type", "spotter"):
        cfg.sort = "snr"
    if cfg.trigger not in ("single", "double"):
        cfg.trigger = "single"
    return cfg
