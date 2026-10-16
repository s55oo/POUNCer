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
    {"key": "n1mm.qsy_wait_ms", "attr": "qsy_wait_ms", "kind": "int", "min": 0, "max": 10000,
     "live": True, "group": "Tap -> N1MM",
     "label": "Wait for N1MM's new frequency (ms)",
     "help": "After a QSY, wait (at most) this long until N1MM reports the radio on the "
             "new frequency before the call is put in."},
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
    {"key": "n1mm.alt_w_after_call", "attr": "alt_w_after_call", "kind": "bool",
     "live": True, "group": "Tap -> N1MM",
     "label": "Alt+W after the call (test)",
     "help": "After the call is in N1MM's Entry Window, press Alt+W - against the call "
             "showing twice (Entry Window and above it). If the call disappears from "
             "the Entry Window too, switch this off."},
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
    {"key": "n1mm.default_radio", "attr": "default_radio", "kind": "choice",
     "choices": ["A", "B"], "live": False, "group": "SO2R / SO2V",
     "label": "Radio at start", "help": "Which radio the main window works at start."},
    {"key": "rbn.spotter", "attr": "rbn_spotter", "kind": "text", "live": False,
     "group": "RBN", "label": "Skimmer", "help": "Only spots of this skimmer (e.g. S53WW)."},
    {"key": "rbn.history", "attr": "rbn_history", "kind": "int", "min": 0, "max": 500,
     "live": False, "group": "RBN", "label": "Last spots at start (SH/DX)",
     "help": "0 = off."},
    {"key": "rbn.backup_host", "attr": "rbn_backup_host", "kind": "text", "live": False,
     "group": "RBN", "label": "Backup telnet server",
     "help": "Used when RBN's telnet is down. Empty = no backup."},
    {"key": "rbn.backup_port", "attr": "rbn_backup_port", "kind": "int", "min": 1,
     "max": 65535, "live": False, "group": "RBN", "label": "Backup port", "help": ""},
    {"key": "rbn.backup_commands", "attr": "rbn_backup_commands", "kind": "text",
     "live": False, "group": "RBN", "label": "Backup commands after login",
     "help": "Comma separated, e.g. set/skimmer."},
    {"key": "rbn.http_backup", "attr": "rbn_http_backup", "kind": "text", "live": True,
     "group": "RBN", "label": "HTTP backup (URL)",
     "help": "RBN over HTTP (S53ZO's Azure feed, no login) while the telnet feed is down or "
             "silent for a minute. Empty = off."},
    {"key": "scp.enabled", "attr": "scp_enabled", "kind": "bool", "live": False,
     "group": "RBN", "label": "Mark calls not in Super Check Partial (?)",
     "help": "A '?' after a spotted call that is not in MASTER.SCP - maybe a skimmer error."},
    {"key": "scp.update_n1mm", "attr": "scp_update_n1mm", "kind": "bool", "live": False,
     "group": "RBN", "label": "Also update N1MM's MASTER.SCP",
     "help": "The weekly download is also copied into N1MM's SupportFiles (N1MM reads it at "
             "its next start)."},
    {"key": "ssb.follow_radio_mode", "attr": "follow_radio_mode", "kind": "bool",
     "live": True, "group": "SSB",
     "label": "List follows the radio mode",
     "help": "Radio on USB / LSB = SSB spots (DX cluster), on CW = RBN CW spots."},
    {"key": "ssb.mode", "attr": "ssb_mode", "kind": "choice", "choices": ["auto", "always", "off"],
     "live": True, "group": "SSB", "label": "DX cluster for SSB",
     "help": "auto = connect while a radio is on SSB (and on a team master)."},
    {"key": "ssb.spotters", "attr": "ssb_spotters", "kind": "text", "live": False,
     "group": "SSB", "label": "Spotters from", "help": "EU, NA, AS, ... or ALL."},
    {"key": "ssb.host", "attr": "ssb_host", "kind": "text", "live": False,
     "group": "SSB", "label": "DX cluster", "help": ""},
    {"key": "ssb.port", "attr": "ssb_port", "kind": "int", "min": 1, "max": 65535,
     "live": False, "group": "SSB", "label": "DX cluster port", "help": ""},
    {"key": "ssb.login", "attr": "ssb_login", "kind": "text", "live": False,
     "group": "SSB", "label": "Login call",
     "help": "Empty = your call. E.g. S55OO-2 if the cluster already has you as the RBN backup."},
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
    {"key": "autostart.enabled", "attr": "autostart", "kind": "bool", "live": True,
     "group": "Autostart", "label": "Start my programs with POUNCer",
     "help": "Programs 1-4 below, then everything in the autostart folder - each after its "
             "delay. Used at the next start of POUNCer (a restart starts nothing again)."},
    {"key": "autostart.app1", "attr": "autostart_app1", "kind": "file", "live": True,
     "group": "Autostart", "label": "Program 1",
     "help": "Full path of an .exe, .bat or shortcut. Arguments: \"C:\\path\\tool.exe\" --arg"
             if 1 == 1 else ""},
    {"key": "autostart.app1_delay", "attr": "autostart_delay1", "kind": "int", "min": 0,
     "max": 600, "live": True, "group": "Autostart", "label": "  ... start after (seconds)",
     "help": "after POUNCer's start" if 1 == 1 else "after program 0"},
    {"key": "autostart.app2", "attr": "autostart_app2", "kind": "file", "live": True,
     "group": "Autostart", "label": "Program 2",
     "help": "Full path of an .exe, .bat or shortcut. Arguments: \"C:\\path\\tool.exe\" --arg"
             if 2 == 1 else ""},
    {"key": "autostart.app2_delay", "attr": "autostart_delay2", "kind": "int", "min": 0,
     "max": 600, "live": True, "group": "Autostart", "label": "  ... start after (seconds)",
     "help": "after POUNCer's start" if 2 == 1 else "after program 1"},
    {"key": "autostart.app3", "attr": "autostart_app3", "kind": "file", "live": True,
     "group": "Autostart", "label": "Program 3",
     "help": "Full path of an .exe, .bat or shortcut. Arguments: \"C:\\path\\tool.exe\" --arg"
             if 3 == 1 else ""},
    {"key": "autostart.app3_delay", "attr": "autostart_delay3", "kind": "int", "min": 0,
     "max": 600, "live": True, "group": "Autostart", "label": "  ... start after (seconds)",
     "help": "after POUNCer's start" if 3 == 1 else "after program 2"},
    {"key": "autostart.app4", "attr": "autostart_app4", "kind": "file", "live": True,
     "group": "Autostart", "label": "Program 4",
     "help": "Full path of an .exe, .bat or shortcut. Arguments: \"C:\\path\\tool.exe\" --arg"
             if 4 == 1 else ""},
    {"key": "autostart.app4_delay", "attr": "autostart_delay4", "kind": "int", "min": 0,
     "max": 600, "live": True, "group": "Autostart", "label": "  ... start after (seconds)",
     "help": "after POUNCer's start" if 4 == 1 else "after program 3"},
    {"key": "autostart.folder", "attr": "autostart_folder", "kind": "text", "live": True,
     "group": "Autostart", "label": "Autostart folder",
     "help": "Everything in it (.exe .bat .cmd .lnk ...) starts after programs 1-4, in name "
             "order. A name like 'autostart' = next to config.ini. Empty = no folder."},
    {"key": "autostart.folder_delay", "attr": "autostart_folder_delay", "kind": "int", "min": 0,
     "max": 600, "live": True, "group": "Autostart", "label": "  ... seconds between them",
     "help": ""},
    {"key": "ui.auto_update", "attr": "auto_update", "kind": "bool", "live": False,
     "group": "Program", "label": "Auto-update from GitHub", "help": ""},
]
BY_KEY = {s["key"]: s for s in SETTINGS}


def _show(item: dict, value) -> object:
    if item["attr"] == "default_radio":
        return "B" if value == 2 else "A"
    if item["attr"] == "rbn_backup_commands":
        return ", ".join(value or ())
    return value


def current(cfg) -> list[dict]:
    """Every setting with its value now (for the page)."""
    out = []
    for item in SETTINGS:
        d = {k: v for k, v in item.items() if k != "attr"}
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
        return c, c
    if item["attr"] == "rbn_backup_commands":
        parts = tuple(p.strip() for p in text.split(",") if p.strip())
        return parts, ", ".join(parts)
    if item["attr"] == "rbn_http_backup" and text and not text.lower().startswith(("http://", "https://")):
        raise ValueError(f"{item['label']}: an http(s):// address, or empty")
    if any(ch in text for ch in "\r\n[]"):
        raise ValueError(f"{item['label']}: no line breaks or brackets")
    if item["attr"] in ("rbn_spotter", "ssb_spotters", "ssb_login"):
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
