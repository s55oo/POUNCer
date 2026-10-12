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
    cty_file: str = ""
    hide_beacons: bool = True
    follow_modes: tuple[str, ...] = ("CW",)
    rbn_ft8_port: int = 7001

    n1mm_listen_ip: str = "0.0.0.0"
    n1mm_listen_port: int = 12060
    n1mm_control_ip: str = "127.0.0.1"
    n1mm_control_port: int = 13064
    n1mm_radionr: int = 1
    set_callsign: bool = True
    wipe_before_call: bool = True
    focus_n1mm: bool = True
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
    team: bool = True       # work together with other POUNCers on the network
    team_port: int = 12079
    sort: str = "snr"
    max_age_minutes: int = 10
    trigger: str = "single"

    web_enabled: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    touch_view: bool = False
    touch_columns: int = 2
    touch_rows: int = 8
    touch_font: int = 26


def load_config(path: str) -> Config:
    cfg = Config()
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
    cfg.set_callsign = b("n1mm", "set_callsign", cfg.set_callsign)
    cfg.wipe_before_call = b("n1mm", "wipe_before_call", cfg.wipe_before_call)
    cfg.focus_n1mm = b("n1mm", "focus_n1mm", cfg.focus_n1mm)
    cfg.n1mm_ini = s("n1mm", "ini", cfg.n1mm_ini).strip()

    cfg.worked_db = s("worked", "db", cfg.worked_db).strip()
    cfg.worked_hide = b("worked", "hide", cfg.worked_hide)
    cfg.worked_refresh_seconds = i("worked", "refresh_seconds", cfg.worked_refresh_seconds)

    cfg.sort = s("ui", "sort", cfg.sort).strip().lower()
    cfg.max_age_minutes = i("ui", "max_age_minutes", cfg.max_age_minutes)
    cfg.tune_step_hz = max(1, i("ui", "tune_step_hz", cfg.tune_step_hz))
    fs = s("ui", "filter_start", cfg.filter_start).strip().lower()
    cfg.filter_start = fs if fs in ("narrow", "wide") else ""
    cfg.filter_narrow_cat = s("ui", "filter_narrow_cat", cfg.filter_narrow_cat).strip()
    cfg.filter_wide_cat = s("ui", "filter_wide_cat", cfg.filter_wide_cat).strip()
    cfg.run_history = b("ui", "run_history", cfg.run_history)
    cfg.run_server = s("ui", "run_server", cfg.run_server).strip()
    cfg.run_list = s("ui", "run_list", cfg.run_list).strip()
    cfg.team = b("team", "enabled", cfg.team)
    cfg.team_port = i("team", "port", cfg.team_port)
    cfg.trigger = s("ui", "trigger", cfg.trigger).strip().lower()
    cfg.touch_view = b("ui", "touch_view", cfg.touch_view)
    cfg.touch_columns = i("ui", "touch_columns", cfg.touch_columns)
    cfg.touch_rows = i("ui", "touch_rows", cfg.touch_rows)
    cfg.touch_font = i("ui", "touch_font", cfg.touch_font)
    cfg.fkeys = parse_fkeys(
        s("ui", "fkeys", s("web", "fkeys", ",".join(f"{k}:{v}" for k, v in cfg.fkeys)))
    )

    cfg.web_enabled = b("web", "enabled", cfg.web_enabled)
    cfg.web_host = s("web", "host", cfg.web_host).strip()
    cfg.web_port = i("web", "port", cfg.web_port)

    if cfg.sort not in ("time", "freq", "snr", "wpm", "call", "cont", "mult", "mode", "type", "spotter"):
        cfg.sort = "snr"
    if cfg.trigger not in ("single", "double"):
        cfg.trigger = "single"
    return cfg
