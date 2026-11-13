"""Which settings every POUNCer of a team runs with - collected on the master.

Every POUNCer sends its settings (``report``) to the master: at start, after
each Save in Settings, and every 10 minutes. The master keeps them in
``cache/team-settings.jsonl``, one line per POUNCer per change: the full set
the first time, then only what changed (old -> new). Help > Troubleshooting >
Save diagnostics puts the file in the zip, so the settings people actually
choose can be looked at when tuning the defaults.

Nothing private leaves a PC: program paths, host names, logins and command
lines are sent only as "(set)" or "" - never their text.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import threading

from . import settings as settingsmod

log = logging.getLogger("pouncer.teamsettings")

FILE = "team-settings.jsonl"
MAX_BYTES = 1_000_000          # then the oldest half goes
REPORT_S = 600                 # every 10 minutes (and at start / after a Save)
MAX_KEYS = 400
MAX_TEXT = 200

# shown only as "(set)" / "": paths, addresses, logins, commands
_PRIVATE_WORDS = ("host", "login", "pass", "token", "secret", "commands", "folder")


def private(item: dict) -> bool:
    key = item.get("key", "")
    return item.get("kind") == "file" or any(w in key for w in _PRIVATE_WORDS)


def report(cfg) -> dict[str, str]:
    """{setting key: value as text} of this PC, private ones masked."""
    out = {}
    for it in settingsmod.current(cfg):
        v = it.get("value")
        if it["kind"] == "bool":
            text = "true" if v else "false"
        elif isinstance(v, (list, tuple)):
            text = ", ".join(str(x) for x in v)
        else:
            text = "" if v is None else str(v)
        if private(it):
            text = "(set)" if text.strip() else ""
        out[it["key"]] = text[:MAX_TEXT]
    return out


def valid(d) -> bool:
    """A report from the network: small, flat, text only."""
    if not isinstance(d, dict):
        return False
    s = d.get("s")
    if not isinstance(s, dict) or len(s) > MAX_KEYS:
        return False
    if not all(isinstance(k, str) and len(k) <= 64 and isinstance(v, str) and len(v) <= MAX_TEXT
               for k, v in s.items()):
        return False
    return all(isinstance(d.get(f, ""), str) and len(d.get(f, "")) <= 64
               for f in ("v", "call", "why"))


class SettingsLog:
    """The master's file: one line per POUNCer per change."""

    def __init__(self, cache_dir: str) -> None:
        self.path = os.path.join(cache_dir or "cache", FILE)
        self.last: dict[str, dict[str, str]] = {}   # PC -> its settings
        self._lock = threading.Lock()

    @staticmethod
    def pc_name(call: str, ip: str) -> str:
        return f"{call or '?'}@{ip or 'this PC'}"

    def add(self, call: str, ip: str, version: str, values: dict[str, str],
            why: str = "", now: datetime.datetime | None = None) -> bool:
        """Note a report; True when something was written (new or changed)."""
        pc = self.pc_name(call, ip)
        now = now or datetime.datetime.now(datetime.timezone.utc)
        with self._lock:
            old = self.last.get(pc)
            if old == values:
                return False
            line = {"t": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "pc": pc, "version": version,
                    "why": why}
            if old is None:
                line["settings"] = dict(values)
            else:
                line["changed"] = {k: [old.get(k), values.get(k)]
                                   for k in sorted(set(old) | set(values))
                                   if old.get(k) != values.get(k)}
            line["hash"] = hashlib.sha1(json.dumps(values, sort_keys=True).encode()).hexdigest()[:10]
            self.last[pc] = dict(values)
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                self._trim()
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(line, separators=(",", ":")) + "\n")
            except OSError as exc:
                log.warning("team settings log not written: %s", exc)
                return False
            return True

    def _trim(self) -> None:
        """Keep the file under MAX_BYTES: drop the oldest half of the lines."""
        try:
            if os.path.getsize(self.path) <= MAX_BYTES:
                return
        except OSError:
            return
        with open(self.path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.writelines(lines[len(lines) // 2:])
