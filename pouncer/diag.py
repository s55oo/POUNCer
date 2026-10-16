"""Log file and diagnostics: what POUNCer did, kept on disk.

``setup_file_log`` adds a rotating ``cache/pouncer.log`` (1 MB, 3 old ones
kept) to the normal logging, so taps, QSYs, autostart, team and connection
problems can be read afterwards - the exe has no console window.

``build_zip`` makes *Help > Save diagnostics*: one zip with the log files,
a facts.txt (versions, radios, team, connections) and config.ini with any
password / token / secret / key value replaced by ``***``.
"""

from __future__ import annotations

import datetime
import logging
import logging.handlers
import os
import re
import zipfile

LOG_NAME = "pouncer.log"
LOG_BYTES = 1_000_000
LOG_BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# config keys whose values never leave the PC (fkeys / hotkeys are not secrets)
SECRET_RE = re.compile(r"^(\s*[;#]?\s*)([\w.]*(?:pass|password|passwd|token|secret|api_?key)[\w.]*)(\s*[=:]\s*)(.*)$",
                       re.IGNORECASE)
EXTRA_FILES = ("cwspeed.txt", "warm-up.txt", "warm-up-ssb.txt", "window.json",
               "perf.csv", "perf.csv.1")

_handler: logging.Handler | None = None


def log_path(cache_dir: str) -> str:
    return os.path.join(cache_dir or "cache", LOG_NAME)


def setup_file_log(cache_dir: str, level: int = logging.INFO) -> str | None:
    """Add the rotating log file (once). Returns its path, None if it can't."""
    global _handler
    if _handler is not None:
        return getattr(_handler, "baseFilename", None)
    path = log_path(cache_dir)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        h = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES,
                                                 backupCount=LOG_BACKUPS, encoding="utf-8")
    except OSError:
        return None
    h.setFormatter(logging.Formatter(FORMAT))
    h.setLevel(level)
    root = logging.getLogger()
    root.addHandler(h)
    if root.level > level or root.level == logging.NOTSET:
        root.setLevel(level)
    _handler = h
    return path


def redact_ini(text: str) -> str:
    """config.ini without secrets: 'password = x' -> 'password = ***'."""
    out = []
    for line in text.splitlines():
        m = SECRET_RE.match(line)
        if m and m.group(4).strip():
            line = m.group(1) + m.group(2) + m.group(3) + "***"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def build_zip(out_path: str, config_path: str, cache_dir: str, facts: dict) -> str:
    """Write the diagnostics zip; returns its path."""
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        lines = [f"{k}: {v}" for k, v in facts.items()]
        z.writestr("facts.txt", "\n".join(lines) + "\n")
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, encoding="utf-8", errors="replace") as fh:
                    z.writestr("config.ini", redact_ini(fh.read()))
            except OSError:
                pass
        base = log_path(cache_dir)
        for n in [""] + [f".{i}" for i in range(1, LOG_BACKUPS + 1)]:
            p = base + n
            if os.path.exists(p):
                if _handler is not None:
                    try:
                        _handler.flush()
                    except Exception:  # noqa: BLE001
                        pass
                z.write(p, os.path.basename(p))
        for name in EXTRA_FILES:
            p = os.path.join(cache_dir or "cache", name)
            if os.path.exists(p):
                z.write(p, name)
    return out_path


def zip_name(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    return now.strftime("POUNCer-diagnostics-%Y%m%d-%H%M%S.zip")
