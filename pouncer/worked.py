"""Read the N1MM Logger+ log N1MM currently has open to find worked calls.

N1MM stores each log as a SQLite database (``.s3db``) with a ``DXLOG`` table.
A single database holds **many contests**; the QSOs are distinguished by the
``ContestNR`` column. The contest N1MM currently has open is recorded in
``N1MM Logger.ini`` (``[Configurer] Contest NR=`` / ``Contest Type=``).

We open that database read-only and build a set of worked
``(call, band, mode)`` tuples for the *open contest only*. A spot is "worked"
when its callsign appears in the open log on the same band AND mode.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import sqlite3
import time
from typing import Callable, Iterable

from .bands import BANDS, band_from_khz

log = logging.getLogger(__name__)

_PHONE_MODES = {"USB", "LSB", "SSB", "AM", "FM", "PH", "PHONE"}

_KNOWN_BANDS = ("160m", "80m", "60m", "40m", "30m", "20m", "17m",
                "15m", "12m", "10m", "6m")


def normalize_mode(mode: str | None) -> str:
    """Collapse modes that are equivalent for "worked on this mode" checks."""
    m = (mode or "").strip().upper()
    if m in _PHONE_MODES:
        return "PHONE"
    return m or "UNKNOWN"


def normalize_mult(field: str, value: object):
    """Comparable multiplier value: zone as int, prefixes upper-case without
    N1MM's "*" (WAE-only entity) marker. None when empty/unknown."""
    if value is None:
        return None
    if field == "ZN":
        try:
            z = int(str(value).strip())
        except ValueError:
            return None
        return z if z > 0 else None
    text = str(value).strip().upper().lstrip("*")
    return text or None


def band_label(band_value: object) -> str | None:
    """Normalise an N1MM ``Band`` value to a band name like ``20m``.

    N1MM stores the band as MHz (e.g. 14.0, 21.0); older/custom values may be
    strings such as ``20M``.
    """
    if band_value is None:
        return None
    if isinstance(band_value, (int, float)):
        return band_from_khz(float(band_value) * 1000.0)
    text = str(band_value).strip().lower()
    if text.endswith("m"):
        return text if text in _KNOWN_BANDS else None
    try:
        return band_from_khz(float(text) * 1000.0)
    except ValueError:
        return None


def default_n1mm_dirs() -> list[str]:
    home = pathlib.Path.home()
    candidates = [
        home / "Documents" / "N1MM Logger+",
        home / "OneDrive" / "Documents" / "N1MM Logger+",
    ]
    return [str(c) for c in candidates]


def inspect_active(ini_path: str | None = None) -> dict:
    """Describe what ``N1MM Logger.ini`` says about the open log.

    Always returns a dict with keys ``ini`` (the ini file found, or None),
    ``path`` (absolute .s3db, or None when the ini names no log),
    ``contest_nr`` (int or None) and ``contest_name`` (str). An ini that names
    a log wins over one that does not (e.g. Documents vs OneDrive\\Documents).
    """
    result: dict = {"ini": None, "path": None, "contest_nr": None, "contest_name": ""}
    dirs = [os.path.dirname(ini_path)] if ini_path else default_n1mm_dirs()
    for base in dirs:
        candidate = ini_path or os.path.join(base, "N1MM Logger.ini")
        if not os.path.exists(candidate):
            continue
        db = None
        contest_nr = None
        contest_name = ""
        try:
            with open(candidate, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.match(r"\s*DXLog Name=(.*?)\s*$", line)
                    if m:
                        db = m.group(1).strip() or None
                        continue
                    m = re.match(r"\s*Contest NR=(-?\d+)\s*$", line)
                    if m:
                        contest_nr = int(m.group(1))
                        continue
                    m = re.match(r"\s*Contest Type=(.+?)\s*$", line)
                    if m:
                        contest_name = m.group(1).strip()
        except OSError:
            continue
        if result["ini"] is None:
            result["ini"] = candidate
        if not db:
            continue
        path = os.path.join(base, "Databases", db)
        if not os.path.exists(path):
            alt = os.path.join(base, db)
            path = alt if os.path.exists(alt) else path
        return {
            "ini": candidate, "path": path,
            "contest_nr": contest_nr, "contest_name": contest_name,
        }
    return result


_CALL_RE = re.compile(r"^(?=.*\d)(?=.*[A-Z])[A-Z0-9/]{3,15}$")


def n1mm_station_call(ini_path: str | None = None) -> str:
    """Your callsign as N1MM Logger+ has it, or "" if not found.

    Read from ``N1MM Logger.ini``: ``[Packet] Logon Callsign`` (the call N1MM
    logs into telnet clusters with) first, then ``[EntryWindow] Operator``.
    Placeholders such as ``DEFAULT`` are ignored.
    """
    found: dict[tuple[str, str], str] = {}
    dirs = [os.path.dirname(ini_path)] if ini_path else default_n1mm_dirs()
    for base in dirs:
        candidate = ini_path or os.path.join(base, "N1MM Logger.ini")
        if not os.path.exists(candidate):
            continue
        section = ""
        try:
            with open(candidate, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("[") and line.endswith("]"):
                        section = line[1:-1].strip().lower()
                        continue
                    key, sep, value = line.partition("=")
                    if sep:
                        found.setdefault(
                            (section, key.strip().lower()), value.strip().upper()
                        )
        except OSError:
            continue
        for want in (("packet", "logon callsign"), ("entrywindow", "operator")):
            call = found.get(want, "")
            if call and call != "DEFAULT" and _CALL_RE.match(call):
                return call
        found.clear()
    return ""


def read_active(ini_path: str | None = None) -> dict | None:
    """Return info about the log N1MM currently has open, or None.

    Keys: ``path`` (absolute .s3db), ``contest_nr`` (int or None),
    ``contest_name`` (str).
    """
    info = inspect_active(ini_path)
    if not info["path"]:
        return None
    return {k: info[k] for k in ("path", "contest_nr", "contest_name")}


def find_active_db(ini_path: str | None = None) -> str | None:
    """Return the path to the currently active N1MM database, if any."""
    info = read_active(ini_path)
    if info and os.path.exists(info["path"]):
        return info["path"]
    return None


# Multiplier types POUNCer understands, as N1MM names them in
# Contest.Multiplier1Name / 2 / 3 and as DXLOG columns. Country and zone count
# once per band; a WPX prefix once per contest.
MULT_FIELDS = ("CountryPrefix", "ZN", "WPXPrefix")
PER_CONTEST_MULTS = {"WPXPrefix"}

# Log states reported to the UI (``WorkedLog.log_state``).
LOG_UNKNOWN = "unknown"            # not checked yet
LOG_OK = "ok"                      # a log is open and loaded
LOG_N1MM_NOT_RUNNING = "n1mm_not_running"
LOG_NO_INI = "no_ini"              # N1MM Logger.ini not found
LOG_NONE_OPEN = "no_log"           # the ini names no log
LOG_MISSING = "log_missing"        # the named .s3db file does not exist

# While no log is open, look again this often (seconds) so the UI clears
# quickly once the user opens one.
RETRY_SECONDS = 2.0


class WorkedLog:
    """Cached set of worked (call, band, mode) tuples for the open contest.

    Besides the worked set it tracks whether a log is open at all:
    ``log_open`` / ``log_state`` / ``log_message`` tell the UI to ask the user
    to open a log in N1MM when there is none.

    ``n1mm_running`` is an optional callable returning True/False (or None for
    "can't tell"); when it returns False the log is treated as closed even if
    the ini still names the last one (the ini keeps it after N1MM exits).
    """

    def __init__(
        self,
        db_path: str = "",
        ini_path: str = "",
        refresh_seconds: int = 10,
        n1mm_running: Callable[[], bool | None] | None = None,
    ) -> None:
        self.db_path = db_path.strip()
        self.ini_path = ini_path.strip()
        self.refresh_seconds = max(1, refresh_seconds)
        self._n1mm_running = n1mm_running
        self.contest_nr: int | None = None
        self.contest_name = ""
        self._path: str | None = None
        self._keys: set[tuple[str, str, str]] = set()
        self._signature: tuple | None = None
        self._last_check = 0.0
        self.status = "not loaded"
        self.db_name = ""
        self.call_count = 0
        self.qso_count = 0
        self._band_qsos: dict[str, int] = {}
        # Multipliers of the open contest and what is already worked:
        # {field: {band or "*": set(values)}}
        self.mult_types: tuple[str, ...] = ()
        self._mult_worked: dict[str, dict[str, set]] = {}
        self.log_open = False
        self.log_state = LOG_UNKNOWN
        self.log_message = "Looking for the N1MM log..."

    def _resolve(self) -> tuple[dict | None, tuple[str, str] | None]:
        """Return ``(info, None)`` for an open log or ``(None, (state, msg))``."""
        if self.db_path:
            if not os.path.exists(self.db_path):
                return None, (
                    LOG_MISSING,
                    f"Log file {self.db_path} not found - check [worked] db in config.ini",
                )
            return {
                "path": self.db_path,
                "contest_nr": self.contest_nr,
                "contest_name": self.contest_name,
            }, None
        if self._n1mm_running is not None:
            try:
                running = self._n1mm_running()
            except Exception:  # never let the probe break the reload
                running = None
            if running is False:
                return None, (
                    LOG_N1MM_NOT_RUNNING,
                    "N1MM Logger+ is not running - start it and open a log",
                )
        info = inspect_active(self.ini_path or None)
        if info["ini"] is None:
            return None, (
                LOG_NO_INI,
                "N1MM Logger.ini not found - set [n1mm] ini in config.ini",
            )
        if not info["path"]:
            return None, (
                LOG_NONE_OPEN,
                "No log open in N1MM - open one with File > Open Log",
            )
        if not os.path.exists(info["path"]):
            return None, (
                LOG_MISSING,
                f"Log {os.path.basename(info['path'])} not found - "
                "open a log in N1MM (File > Open Log)",
            )
        return info, None

    def _set_closed(self, state: str, message: str) -> bool:
        """Record that no log is usable. Returns True if the UI must refresh."""
        changed = bool(self._keys) or self.log_open or self.log_state != state
        self._keys = set()
        self._path = None
        self._signature = None
        self.call_count = 0
        self.qso_count = 0
        self._band_qsos = {}
        self.mult_types = ()
        self._mult_worked = {}
        self.db_name = ""
        self.log_open = False
        self.log_state = state
        self.log_message = message
        self.status = "no log open"
        if changed:
            log.info("worked log: %s", message)
        return changed

    def request_recheck(self) -> None:
        """Re-read the ini and reopen the log on the next ``maybe_reload``.

        Backs the "Reconnect" button: no waiting for the refresh timer, and the
        log is re-read even if its change signature looks the same.
        """
        self._last_check = 0.0
        self._signature = None

    def maybe_reload(self) -> bool:
        """Reload if the database or the open contest changed.

        The log's cheap change-signature (main ``.s3db`` plus its ``-wal``
        sidecar) is checked on *every* call, so a QSO logged in N1MM is noticed
        within one GUI tick (~0.5 s) instead of waiting for a timer. Only the
        heavier re-read of ``N1MM Logger.ini`` (to pick up a contest/log switch)
        is throttled to ``refresh_seconds`` - or to ``RETRY_SECONDS`` while no
        log is open, so opening one is noticed quickly.

        Returns True when the worked set or the open/closed state changed.
        """
        now = time.time()
        interval = (
            self.refresh_seconds if self._path
            else min(self.refresh_seconds, RETRY_SECONDS)
        )
        if now - self._last_check >= interval:
            self._last_check = now
            info, problem = self._resolve()
            if problem:
                return self._set_closed(*problem)
            if info["path"] != self._path:
                self._signature = None
            self._path = info["path"]
            self.contest_nr = info.get("contest_nr")
            self.contest_name = info.get("contest_name", "")
        path = self._path
        if path is None:
            return False
        if not os.path.exists(path):
            return self._set_closed(
                LOG_MISSING,
                f"Log {os.path.basename(path)} not found - "
                "open a log in N1MM (File > Open Log)",
            )
        signature = self._db_signature(path, self.contest_nr)
        if signature == self._signature:
            return False
        self._load(path, signature, self.contest_nr, self.contest_name)
        return True

    @staticmethod
    def _db_signature(path: str, contest_nr: object) -> tuple:
        """Change signature of the log, including its WAL sidecar file.

        N1MM runs the SQLite database in WAL mode, so newly logged QSOs land in
        ``<db>-wal`` and the main file's size/mtime stays put. Watching the WAL
        file too is what makes freshly-worked calls show up.
        """
        parts: list[object] = [contest_nr]
        for candidate in (path, path + "-wal"):
            try:
                st = os.stat(candidate)
                parts += [st.st_mtime_ns, st.st_size]
            except OSError:
                parts += [None, None]
        return tuple(parts)

    def _connect(self, path: str) -> sqlite3.Connection:
        uri = pathlib.Path(path).absolute().as_uri() + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.OperationalError:
            con = sqlite3.connect(path, timeout=2.0)
        con.execute("PRAGMA query_only=1")
        return con

    def _load(
        self,
        path: str,
        signature: tuple,
        contest_nr: int | None,
        contest_name: str,
    ) -> None:
        keys: set[tuple[str, str, str]] = set()
        calls: set[str] = set()
        band_qsos: dict[str, int] = {}
        total = 0
        mult_worked: dict[str, dict[str, set]] = {}
        try:
            con = self._connect(path)
            try:
                cols = {r[1] for r in con.execute("PRAGMA table_info(DXLOG)")}
                mult_types = tuple(
                    f for f in self._read_mult_types(con, contest_name) if f in cols
                )
                mult_worked = {f: {} for f in mult_types}
                sql = "SELECT Call, Band, Mode" + "".join(f", {f}" for f in mult_types)
                sql += " FROM DXLOG"
                params: tuple = ()
                if contest_nr is not None:
                    sql += " WHERE ContestNR = ?"
                    params = (contest_nr,)
                for row in con.execute(sql, params):
                    call, band, mode = row[0], row[1], row[2]
                    if not call:
                        continue
                    total += 1
                    label = band_label(band)
                    if not label:
                        continue
                    band_qsos[label] = band_qsos.get(label, 0) + 1
                    c = str(call).strip().upper()
                    keys.add((c, label, normalize_mode(mode)))
                    calls.add(c)
                    for f, value in zip(mult_types, row[3:]):
                        v = normalize_mult(f, value)
                        if v is None:
                            continue
                        where = "*" if f in PER_CONTEST_MULTS else label
                        mult_worked[f].setdefault(where, set()).add(v)
            finally:
                con.close()
        except (sqlite3.Error, OSError) as exc:
            self.status = f"read failed: {exc}"
            log.warning("worked-log read failed for %s: %s", path, exc)
            return
        self._keys = keys
        self.mult_types = tuple(mult_worked)
        self._mult_worked = mult_worked
        self.call_count = len(calls)
        self.qso_count = total
        self._band_qsos = band_qsos
        self._signature = signature
        self.contest_nr = contest_nr
        self.contest_name = contest_name
        base = os.path.basename(path)
        if contest_nr is not None:
            self.db_name = f"{base} #{contest_nr} {contest_name}".strip()
        else:
            self.db_name = base
        self.status = f"{self.call_count} calls"
        self.log_open = True
        self.log_state = LOG_OK
        self.log_message = ""
        log.info(
            "loaded %d worked calls from %s (contest %s %s)",
            self.call_count, path, contest_nr, contest_name,
        )

    @staticmethod
    def _read_mult_types(con: sqlite3.Connection, contest_name: str) -> tuple[str, ...]:
        """The open contest's multiplier fields (N1MM Contest table), e.g.
        CQWWCW -> ("ZN", "CountryPrefix"). Unknown kinds are skipped."""
        if not contest_name:
            return ()
        try:
            row = con.execute(
                "SELECT Multiplier1Name, Multiplier2Name, Multiplier3Name "
                "FROM Contest WHERE Name = ?", (contest_name,),
            ).fetchone()
        except sqlite3.Error:
            return ()
        out = []
        for name in row or ():
            for f in MULT_FIELDS:
                if name and str(name).strip().lower() == f.lower() and f not in out:
                    out.append(f)
        return tuple(out)

    @property
    def zone_is_itu(self) -> bool:
        """IARU HF counts ITU zones; CQ contests count CQ zones."""
        return self.contest_name.upper().startswith("IARU")

    def new_mults(
        self,
        freq_khz: float,
        country_prefix: str | None,
        zone: int | None,
        wpx: str | None,
    ) -> int:
        """How many multipliers a station would be on this band (0, 1 or 2 -
        e.g. new zone + new country = a double mult in CQWW)."""
        band = band_from_khz(freq_khz)
        if not band or not self.mult_types:
            return 0
        values = {"CountryPrefix": country_prefix, "ZN": zone, "WPXPrefix": wpx}
        count = 0
        for f in self.mult_types:
            v = normalize_mult(f, values[f])
            if v is None:
                continue
            where = "*" if f in PER_CONTEST_MULTS else band
            if v not in self._mult_worked.get(f, {}).get(where, set()):
                count += 1
        return count

    def band_qsos(self) -> list[tuple[str, int]]:
        """QSOs in the open contest per band, in band-plan order (low to high).

        Counts every logged row (dupes included), like N1MM's QSO total.
        """
        order = [name for name, _, _ in BANDS]
        return sorted(
            self._band_qsos.items(),
            key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order),
        )

    def is_worked(self, call: str, freq_khz: float, mode: str) -> bool:
        band = band_from_khz(freq_khz)
        if not band:
            return False
        return (call.strip().upper(), band, normalize_mode(mode)) in self._keys

    def worked_keys(self) -> Iterable[tuple[str, str, str]]:
        return frozenset(self._keys)
