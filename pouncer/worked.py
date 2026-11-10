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
import calendar
import os
import pathlib
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from typing import Callable, Iterable

from . import mults as multsmod
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


def same_station(call: str, station_call: str) -> bool:
    """True when ``call`` is our own station (also S55OO/P, 9A/S55OO)."""
    if not call or not station_call:
        return False

    def core(c: str) -> str:
        parts = [p for p in c.strip().upper().split("/") if p]
        return max(parts, key=len) if parts else ""

    return call.strip().upper() == station_call.strip().upper() or core(call) == core(station_call)


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


# Multiplier types (see mults.py: DXLOG columns, slots from N1MM's Contest
# table, learnt from the IsMultiplier flags). Kept for older callers.
MULT_FIELDS = multsmod.CANDIDATES
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
# backstop: re-read the log this often even when its files look unchanged
FORCE_RELOAD_S = 30.0


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
        self.contest_nr: int | None = None      # the contest counted (after fallbacks)
        self.contest_name = ""
        self._wanted_nr: int | None = None      # what N1MM / the ini named
        # -> what N1MM broadcast as open (AppInfo / contact packets), or None
        self.app_info: Callable[[], dict | None] | None = None
        self.contact_events = None   # n1mm.RadioState.contact_events (QSOs from packets)
        self._app_seen = None
        self.contest_source = ""                # "N1MM", "ini" or "latest QSO"
        self.ini_stale_nr: int | None = None    # newest QSOs are in this other contest
        self._path: str | None = None
        self._keys: set[tuple[str, str, str]] = set()
        self._signature: tuple | None = None
        self._last_check = 0.0
        self.status = "not loaded"
        self.db_name = ""
        # Our call in the open log (N1MM Config > Change Your Station Data)
        self.station_call = ""
        self.call_count = 0
        self.qso_count = 0
        self._band_qsos: dict[str, int] = {}
        # Multipliers of the open contest and what is already worked:
        # {field: {band or "*": set(values)}}
        self.mult_types: tuple[str, ...] = ()
        self._mult_worked: dict[str, dict[str, set]] = {}
        # the slots (mults.resolve_slots) and what the log / call history know
        # about a station's exchange (section, DOK, province ...)
        self.mult_slots: list[dict] = []
        self._call_vals: dict[str, dict[str, object]] = {}
        self._callhist: dict[str, dict[str, object]] = {}
        self._callhist_sig = None
        self._slot_cache: tuple | None = None
        self.log_open = False
        self.log_state = LOG_UNKNOWN
        self.log_message = "Looking for the N1MM log..."
        # read the log in a thread (v0.7.176, set by the app): a big log no
        # longer stalls the window; the result is taken in maybe_reload
        self.background = False
        self._job: dict | None = None
        self._gen = 0                    # +1 each time a re-read is asked for

    def _resolve(self) -> tuple[dict | None, tuple[str, str] | None]:
        """Return ``(info, None)`` for an open log or ``(None, (state, msg))``."""
        if self.db_path:
            if not os.path.exists(self.db_path):
                return None, (
                    LOG_MISSING,
                    f"Log file {self.db_path} not found - check [worked] db in config.ini",
                )
            return self._apply_app({
                "path": self.db_path,
                "contest_nr": self._wanted_nr,
                "contest_name": self.contest_name,
            }, fixed=True), None
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
        info = self._apply_app(info)
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
        self.mult_slots = []
        self._call_vals = {}
        self.db_name = ""
        self.station_call = ""
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
        self._invalidate()

    def _invalidate(self) -> None:
        """Read the log again on the next check (also when a read is running:
        what it brings may be older than what N1MM just wrote)."""
        self._signature = None
        self._gen += 1

    def _apply_app(self, info: dict, fixed: bool = False) -> dict:
        """What N1MM broadcast (AppInfo: database + contest; QSO packets: the
        contest) wins over the ini - N1MM writes the ini only now and then,
        so after opening another contest the ini can still name the old one."""
        app = self.app_info() if self.app_info is not None else None
        if not app or app.get("contest_nr") is None:
            return dict(info, source="ini")
        info = dict(info)
        db = (app.get("dbname") or "").strip()
        if db and not fixed:
            if os.path.isabs(db):
                cand = db
            else:
                folder = (os.path.dirname(info["path"]) if info.get("path")
                          else os.path.join(os.path.dirname(info.get("ini") or ""), "Databases"))
                cand = os.path.join(folder, os.path.basename(db))
            if os.path.exists(cand):
                info["path"] = cand
        if fixed and db and info.get("path") and \
                os.path.basename(db).lower() != os.path.basename(info["path"]).lower():
            return dict(info, source="ini")     # [worked] db is another file
        info["contest_nr"] = app.get("contest_nr")
        info["contest_name"] = app.get("contest_name") or info.get("contest_name", "")
        info["source"] = "N1MM"
        return info

    def maybe_reload(self, wait: bool = False) -> bool:
        """Reload if the database or the open contest changed.

        The log's cheap change-signature (main ``.s3db`` plus its ``-wal``
        sidecar) is checked on *every* call, so a QSO logged in N1MM is noticed
        within one GUI tick (~0.5 s) instead of waiting for a timer. Only the
        heavier re-read of ``N1MM Logger.ini`` (to pick up a contest/log switch)
        is throttled to ``refresh_seconds`` - or to ``RETRY_SECONDS`` while no
        log is open, so opening one is noticed quickly.

        With ``background`` the read itself runs in a thread and its result
        is taken here on a later call; ``wait`` waits for it (Reconnect).

        Returns True when the worked set or the open/closed state changed.
        """
        done = self._take_job()
        now = time.time()
        interval = (
            self.refresh_seconds if self._path
            else min(self.refresh_seconds, RETRY_SECONDS)
        )
        app = self.app_info() if self.app_info is not None else None
        stamp = (app.get("dbname"), app.get("contest_nr")) if app else None
        if stamp != self._app_seen:              # N1MM opened another contest: now
            self._app_seen = stamp
            self._last_check = 0.0
        contacts = app.get("contacts") if app else None
        if contacts != getattr(self, "_contacts_seen", None):
            # N1MM broadcast a logged / changed / deleted QSO (Broadcast Data >
            # Contacts): read the log now, whatever the files' times say, and
            # take the QSO from the packet itself at once (v0.7.171)
            self._contacts_seen = contacts
            self._invalidate()
            self._take_contacts()
        if now - self._last_check >= interval:
            self._last_check = now
            info, problem = self._resolve()
            if problem:
                return self._set_closed(*problem)
            if info["path"] != self._path:
                self._invalidate()
            self._path = info["path"]
            self._wanted_nr = info.get("contest_nr")
            self.contest_name = info.get("contest_name", "")
            self._source = info.get("source", "ini")
        path = self._path
        if path is None:
            return done
        if not os.path.exists(path):
            return self._set_closed(
                LOG_MISSING,
                f"Log {os.path.basename(path)} not found - "
                "open a log in N1MM (File > Open Log)",
            )
        if self._job is not None:               # one read at a time
            return self._wait_job() or done if wait else done
        signature = self._db_signature(path, self._wanted_nr)
        if signature == self._signature and now - getattr(self, "_loaded_at", now) < FORCE_RELOAD_S:
            return done
        reason = ("first" if self._signature is None and not getattr(self, "_loaded_at", 0)
                  else "packet/recheck" if self._signature is None
                  else "files changed" if signature != self._signature else "30 s")
        if not self.background:
            self._reason = reason
            self._load(path, signature, self._wanted_nr, self.contest_name)
            return True
        job = {"path": path, "sig": signature, "gen": self._gen, "reason": reason,
               "t": time.monotonic(), "res": None}
        args = (path, self._wanted_nr, self.contest_name, getattr(self, "_source", "ini"))

        def run() -> None:
            try:
                job["res"] = self._read(*args)
            except Exception:  # noqa: BLE001 - a broken read must not kill the thread silently
                log.exception("worked: log read failed (thread)")
        job["thread"] = threading.Thread(target=run, name="worked-log", daemon=True)
        self._job = job
        job["thread"].start()
        if wait:
            return self._wait_job() or done
        return done

    def _wait_job(self, timeout: float = 15.0) -> bool:
        job = self._job
        if job is not None:
            job["thread"].join(timeout)
        return self._take_job()

    def _take_job(self) -> bool:
        """The thread's read is done: take it (on the window's thread).
        True = the worked set changed."""
        job = self._job
        if job is None or job["thread"].is_alive():
            return False
        self._job = None
        res = job["res"]
        self.read_ms = round((time.monotonic() - job["t"]) * 1000)
        if job["path"] != self._path:
            return False                        # another log since
        if res is None:                         # the thread crashed (in pouncer.log)
            self.status = "read failed: see pouncer.log"
            return True
        self._reason = job["reason"]
        self._apply(res, job["sig"])
        if job["gen"] != self._gen:
            self._signature = None              # asked again while reading: read again
        return True

    @staticmethod
    def _db_signature(path: str, contest_nr: object) -> tuple:
        """Change signature of the log, including its WAL sidecar file.

        N1MM runs the SQLite database in WAL mode, so newly logged QSOs land in
        ``<db>-wal`` and the main file's size/mtime stays put.

        v0.7.169: on Windows ``os.stat`` by NAME (Python 3.12+ uses
        GetFileInformationByName) returns the directory entry, which NTFS
        updates only now and then for a file another program keeps open - so
        a QSO logged in N1MM was often not noticed (the list kept the
        station). Now each file is opened and asked through its handle
        (fstat: current), and the first 48 bytes of ``<db>-shm`` are read -
        SQLite's WAL index header, whose change counter and frame count move
        on every commit, even when the WAL file's size stays the same.
        """
        parts: list[object] = [contest_nr]
        for candidate in (path, path + "-wal"):
            try:
                with open(candidate, "rb") as fh:
                    st = os.fstat(fh.fileno())
                parts += [st.st_mtime_ns, st.st_size]
            except OSError:
                parts += [None, None]
        try:
            with open(path + "-shm", "rb") as fh:
                parts.append(fh.read(48))
        except OSError:
            parts.append(None)
        return tuple(parts)

    def _connect(self, path: str) -> sqlite3.Connection:
        """A connection to a fresh COPY of the log (v0.7.170), else the log.

        On Windows a read-only connection to N1MM's live log kept seeing
        the log as it was when POUNCer started: logged QSOs (in the -wal file)
        never showed up, even though they were in the file. A copy of the
        ``.s3db`` and its ``-wal`` (no ``-shm``) is opened alone, so SQLite
        rebuilds the index from the WAL itself and sees every committed QSO;
        a half-written last frame fails its checksum and is simply ignored
        (the next change reads again). N1MM's files are only read."""
        con = None
        try:
            copy = self._copy_log(path)
            con = sqlite3.connect(copy, timeout=2.0)
            con.execute("PRAGMA query_only=1")
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            if not getattr(self, "_copy_said", False):
                log.info("worked: reading copies of the log in %s", os.path.dirname(copy))
                self._copy_said = True
            return con
        except (OSError, sqlite3.Error) as exc:
            if con is not None:
                con.close()
            if str(exc) != getattr(self, "_copy_err", ""):
                log.warning("worked: log copy failed (%s) - reading %s directly", exc, path)
                self._copy_err = str(exc)
        uri = pathlib.Path(path).absolute().as_uri() + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.OperationalError:
            con = sqlite3.connect(path, timeout=2.0)
        con.execute("PRAGMA query_only=1")
        return con

    def _copy_log(self, path: str) -> str:
        """Copy ``path`` + ``path-wal`` into POUNCer's temp folder under a NEW
        name each time (v0.7.171): Windows keeps an old copy's ``-shm`` mapped
        / locked a while, and a stale index reused under the same name made
        SQLite show the old log. Older copies are removed when they can be."""
        folder = os.path.join(tempfile.gettempdir(), f"POUNCer-log-{os.getpid()}")
        if os.path.exists(folder) and not os.path.isdir(folder):
            folder += "-copies"
        os.makedirs(folder, exist_ok=True)
        self._copy_n = getattr(self, "_copy_n", 0) + 1
        base = os.path.basename(path) or "log.s3db"
        dst = os.path.join(folder, f"{self._copy_n}-{base}")
        for name in os.listdir(folder):                     # earlier copies
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
        shutil.copyfile(path, dst)
        wal = path + "-wal"
        size = 0
        if os.path.exists(wal):
            shutil.copyfile(wal, dst + "-wal")
            size = os.path.getsize(dst + "-wal")
        self._copy_info = f"copy {os.path.basename(dst)} (wal {size} bytes)"
        return dst

    def _load(
        self,
        path: str,
        signature: tuple,
        contest_nr: int | None,
        contest_name: str,
    ) -> None:
        """Read the log and take it at once (no background thread)."""
        self._apply(self._read(path, contest_nr, contest_name,
                               getattr(self, "_source", "ini")), signature)

    def _read(self, path: str, contest_nr: int | None, contest_name: str,
              source: str) -> dict:
        """The slow part - copy and query the log. Touches nothing the window
        reads, so it can run in a thread (v0.7.176); ``_apply`` takes the
        result."""
        keys: set[tuple[str, str, str]] = set()
        calls: set[str] = set()
        band_qsos: dict[str, int] = {}
        total = 0
        mult_worked: dict[str, dict[str, set]] = {}
        slots: list[dict] = []
        call_vals: dict[str, dict] = {}
        station = ""
        try:
            con = self._connect(path)
            try:
                try:  # Change Your Station Data: our call in this log
                    row = con.execute("SELECT Call FROM Station LIMIT 1").fetchone()
                    station = str(row[0] or "").strip().upper() if row else ""
                except sqlite3.Error:
                    station = ""
                cols = {r[1] for r in con.execute("PRAGMA table_info(DXLOG)")}
                newest_nr, newest_ts = None, ""
                if "ContestNR" in cols and "TS" in cols:
                    row = con.execute(
                        "SELECT ContestNR, TS FROM DXLOG WHERE ContestNR IS NOT NULL "
                        "ORDER BY TS DESC LIMIT 1").fetchone()
                    if row and row[0] is not None:
                        newest_nr, newest_ts = int(row[0]), str(row[1] or "")
                if contest_nr is None and newest_nr is not None:
                    # nobody named the contest: never count every contest of
                    # the database - take the one with the newest QSO
                    contest_nr = newest_nr
                    source = "latest QSO"
                names = self._read_mult_names(con, contest_name)
                lower = {c.lower(): c for c in cols}
                cand = [f for f in multsmod.CANDIDATES if f in cols]
                flag_cols = [lower.get(f"ismultiplier{i}") for i in (1, 2, 3)]
                sql = "SELECT Call, Band, Mode" + "".join(f", {f}" for f in cand)
                sql += "".join(f", {f}" if f else ", 0" for f in flag_cols)
                sql += " FROM DXLOG"
                params: tuple = ()
                if contest_nr is not None:
                    sql += " WHERE ContestNR = ?"
                    params = (contest_nr,)
                if "TS" in cols:
                    sql += " ORDER BY TS"
                rows = []
                call_vals: dict[str, dict] = {}
                for row in con.execute(sql, params).fetchall():
                    call, band, mode = row[0], row[1], row[2]
                    if not call:
                        continue
                    total += 1
                    label = band_label(band)
                    if not label:
                        continue
                    band_qsos[label] = band_qsos.get(label, 0) + 1
                    c = str(call).strip().upper()
                    m = normalize_mode(mode)
                    keys.add((c, label, m))
                    calls.add(c)
                    values = {f: multsmod.normalize(f, v)
                              for f, v in zip(cand, row[3:3 + len(cand)])}
                    flags = tuple(bool(x) for x in row[3 + len(cand):])
                    rows.append({"call": c, "band": label, "mode": m, "values": values,
                                 "flags": flags})
                    known = call_vals.setdefault(c, {})
                    for f, v in values.items():
                        if v is not None:
                            known[f] = v           # the newest QSO's value wins
                # slots: re-learnt when the contest changes or every 25 QSOs
                ck = (path, contest_nr, contest_name, tuple(names), len(rows) // 25)
                if self._slot_cache and self._slot_cache[0] == ck:
                    slots = self._slot_cache[1]
                else:
                    slots = multsmod.resolve_slots(names, set(cand), rows)
                    self._slot_cache = (ck, slots)
                mult_types = tuple(s["field"] for s in slots if s["field"])
                mult_worked = {f: {} for f in mult_types}
                for r in rows:
                    for sl in slots:
                        f = sl["field"]
                        v = r["values"].get(f) if f else None
                        if v is None:
                            continue
                        where = multsmod.scope_key(sl["scope"], r["band"], r["mode"])
                        mult_worked[f].setdefault(where, set()).add(v)
            finally:
                con.close()
        except (sqlite3.Error, OSError) as exc:
            return {"path": path, "error": str(exc)}
        callhist = None
        if any(s["field"] and s["field"] not in multsmod.FROM_CALL for s in slots):
            callhist = self._read_callhist(path, self._callhist_sig)
        return {"path": path, "error": "", "keys": keys, "calls": calls,
                "band_qsos": band_qsos, "total": total, "mult_worked": mult_worked,
                "slots": slots, "call_vals": call_vals, "station": station,
                "contest_nr": contest_nr, "contest_name": contest_name, "source": source,
                "newest_nr": newest_nr, "newest_ts": newest_ts, "callhist": callhist,
                "copy": getattr(self, "_copy_info", "")}

    def _apply(self, res: dict, signature: tuple) -> None:
        """Take a ``_read`` result: the worked set, mults, counts, status."""
        path = res["path"]
        if res["error"]:
            self.status = f"read failed: {res['error']}"
            log.warning("worked-log read failed for %s: %s", path, res["error"])
            return
        keys, calls, band_qsos, total = res["keys"], res["calls"], res["band_qsos"], res["total"]
        mult_worked, slots, call_vals = res["mult_worked"], res["slots"], res["call_vals"]
        station, contest_nr, contest_name = res["station"], res["contest_nr"], res["contest_name"]
        source, newest_nr, newest_ts = res["source"], res["newest_nr"], res["newest_ts"]
        callhist, copy_info = res["callhist"], res["copy"]
        self._keys = keys
        if getattr(self, "_extra", None):
            self._extra -= keys                 # the log has them now
        if getattr(self, "_gone", None):
            self._gone &= {k[:2] for k in keys}   # deleted ones the log still shows
        self.station_call = station
        self.mult_types = tuple(mult_worked)
        self._mult_worked = mult_worked
        self.mult_slots = slots
        self._call_vals = call_vals
        if callhist is not None:
            self._callhist, self._callhist_sig = callhist
        self.call_count = len(calls)
        self.qso_count = total
        self._band_qsos = band_qsos
        self._signature = signature
        self.contest_nr = contest_nr
        self.contest_name = contest_name
        self.contest_source = source
        # ini out of date? QSOs of the last 24 h are in ANOTHER contest than
        # the ini names (a freshly opened, still empty contest is fine)
        self.ini_stale_nr = None
        if source == "ini" and newest_nr is not None and newest_nr != contest_nr:
            try:
                t = calendar.timegm(time.strptime(newest_ts[:19], "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                t = 0
            if time.time() - t < 86400:
                self.ini_stale_nr = newest_nr
        base = os.path.basename(path)
        if contest_nr is not None:
            self.db_name = f"{base} #{contest_nr} {contest_name}".strip()
        else:
            self.db_name = base
        self.status = f"{self.call_count} calls"
        self.log_open = True
        self.log_state = LOG_OK
        self.log_message = ""
        self._loaded_at = time.time()
        said = (self.call_count, self.qso_count, path, contest_nr)
        (log.info if said != getattr(self, "_said", None) else log.debug)(
            "loaded %d worked calls from %s (contest %s %s)",
            self.call_count, path, contest_nr, contest_name,
        )
        self._said = said
        reason = getattr(self, "_reason", "")
        self.last_read = {"t": round(self._loaded_at), "reason": reason, "qsos": self.qso_count,
                          "calls": self.call_count, "newest": newest_ts,
                          "copy": copy_info}
        if reason != "30 s" or self._loaded_at - getattr(self, "_read_said", 0) > 600:
            log.info("worked: read (%s): %d QSOs of contest %s, newest in the log %s, %s",
                     reason, self.qso_count, contest_nr, newest_ts or "-",
                     copy_info or "live file")
            self._read_said = self._loaded_at

    @staticmethod
    def _read_mult_names(con: sqlite3.Connection, contest_name: str) -> list:
        """Multiplier1Name..3 of the open contest in N1MM's Contest table
        (["ZN", "CountryPrefix", "N/A"] for CQWWCW); [] if unknown."""
        if not contest_name:
            return []
        try:
            row = con.execute(
                "SELECT Multiplier1Name, Multiplier2Name, Multiplier3Name "
                "FROM Contest WHERE Name = ?", (contest_name,),
            ).fetchone()
        except sqlite3.Error:
            return []
        return list(row) if row else []

    def _read_callhist(self, log_path: str, have_sig) -> tuple | None:
        """N1MM's call history (Databases\\N1MM Admin.s3db, table CallHist):
        section, exchange, grid ... of stations not worked yet. Re-read when
        the file changes: (calls, signature), None = unchanged."""
        path = os.path.join(os.path.dirname(log_path), "N1MM Admin.s3db")
        try:
            st = os.stat(path)
        except OSError:
            return ({}, None) if have_sig is not None else None
        sig = (path, st.st_mtime_ns, st.st_size)
        if sig == have_sig:
            return None
        out: dict[str, dict] = {}
        try:
            con = self._connect(path)
            try:
                cols = {r[1] for r in con.execute("PRAGMA table_info(CallHist)")}
                use = [c for c in multsmod.CALLHIST if c in cols]
                if "Call" in cols and use:
                    for row in con.execute(
                            "SELECT Call" + "".join(f", {c}" for c in use) + " FROM CallHist"):
                        if not row[0]:
                            continue
                        vals = {}
                        for c, v in zip(use, row[1:]):
                            f = multsmod.CALLHIST[c]
                            nv = multsmod.normalize(f, v)
                            if nv is not None:
                                vals[f] = nv
                        if vals:
                            out[str(row[0]).strip().upper()] = vals
            finally:
                con.close()
        except (sqlite3.Error, OSError) as exc:
            log.info("call history not read: %s", exc)
        log.info("call history: %d calls with exchange data", len(out))
        return out, sig

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

    def station_value(self, field: str, call: str | None, from_call: dict) -> tuple:
        """(value, source) of one multiplier field for a spotted station:
        country / zone / prefix / continent from cty.dat and the call (a zone
        the log has for this call wins); exchange fields (section, DOK ...)
        from the log - the station worked on another band - else N1MM's call
        history; else (None, "unknown")."""
        c = (call or "").strip().upper()
        logged = self._call_vals.get(c, {}).get(field) if c else None
        if field == "ZN" and logged is not None:      # the zone logged for this call wins
            return logged, "log"
        if field in multsmod.FROM_CALL:
            v = multsmod.normalize(field, from_call.get(field))
            return v, ("cty.dat" if v is not None else "unknown")
        if logged is not None:
            return logged, "log"
        hist = self._callhist.get(c, {}).get(field) if c else None
        if hist is not None:
            return hist, "call history"
        return None, "unknown"

    def _spot_mults(self, freq_khz, country_prefix, zone, wpx, call, continent, mode):
        band = band_from_khz(freq_khz)
        base = {"CountryPrefix": country_prefix, "ZN": zone, "WPXPrefix": wpx,
                "Continent": continent}
        m = normalize_mode(mode) if mode else None
        out = []
        for sl in self.mult_slots:
            f = sl["field"]
            if f is None:
                out.append((sl, None, "unknown", None, None))
                continue
            v, src = self.station_value(f, call, base)
            scope = sl["scope"]
            if scope == "bandmode" and not m:
                scope = "band"
            key = multsmod.scope_key(scope, band, m)
            new = None if v is None or not band else \
                v not in self._mult_worked.get(f, {}).get(key, set())
            out.append((sl, v, src, new, scope))
        return out

    def new_mults(
        self,
        freq_khz: float,
        country_prefix: str | None,
        zone: int | None,
        wpx: str | None,
        call: str | None = None,
        continent: str | None = None,
        mode: str | None = None,
    ) -> int:
        """How many multipliers a station would be (0, 1, 2 ... - e.g. new
        zone + new country = a double mult in CQWW). Exchange multipliers
        (section, DOK ...) count when the log or the call history knows the
        station's value."""
        if not band_from_khz(freq_khz) or not self.mult_slots:
            return 0
        return sum(1 for _sl, _v, _src, new, _sc in self._spot_mults(
            freq_khz, country_prefix, zone, wpx, call, continent, mode) if new)

    def mult_detail(
        self,
        freq_khz: float,
        country_prefix: str | None,
        zone: int | None,
        wpx: str | None,
        call: str | None = None,
        continent: str | None = None,
        mode: str | None = None,
    ) -> list[dict]:
        """Why ``new_mults`` says what it says (phone debug): per multiplier
        slot its column, the station's value and where it came from, where it
        counts (band / band and mode / whole contest), how POUNCer knows the
        rule (N1MM's contest table or learnt from the log) and whether new."""
        band = band_from_khz(freq_khz)
        out = []
        for sl, v, src, new, scope in self._spot_mults(
                freq_khz, country_prefix, zone, wpx, call, continent, mode):
            sc = scope or sl["scope"]
            where = "contest" if sc == "contest" else (
                f"{band or '?'} {normalize_mode(mode)}" if sc == "bandmode" and mode else (band or "?"))
            out.append({"type": sl["field"] or (sl.get("name") or "?"), "n": sl["n"],
                        "value": v, "where": where, "new": new, "source": src,
                        "how": sl["how"], "agree": sl.get("agree"),
                        "flagged": sl.get("flagged"), "scope": sc})
        return out

    def mult_summary(self) -> str:
        """'CQ zone per band + country per band' (window, phone, debug)."""
        return multsmod.describe(self.mult_slots, "ITU" if self.zone_is_itu else "CQ")

    def band_qsos(self) -> list[tuple[str, int]]:
        """QSOs in the open contest per band, in band-plan order (low to high).

        Counts every logged row (dupes included), like N1MM's QSO total.
        """
        order = [name for name, _, _ in BANDS]
        return sorted(
            self._band_qsos.items(),
            key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order),
        )

    def _take_contacts(self) -> None:
        """N1MM's contact packets (contactinfo / contactreplace = logged,
        contactdelete = gone): worked at once, before the log file shows it."""
        events = getattr(self, "contact_events", None)
        if events is None:
            return
        extra = self.__dict__.setdefault("_extra", set())
        gone = self.__dict__.setdefault("_gone", set())
        for n, kind, call, band, mode, khz, nr in events(getattr(self, "_event_n", 0)):
            self._event_n = n
            if not call or (nr is not None and self._wanted_nr is not None
                            and nr != self._wanted_nr):
                continue
            label = band_label(band) or (band_from_khz(khz) if khz else None)
            if not label:
                continue
            if kind == "contactdelete":           # (no mode in it): that call on that band
                for k in [k for k in extra if k[:2] == (call, label)]:
                    extra.discard(k)
                gone.add((call, label))
            else:
                extra.add((call, label, normalize_mode(mode)))
                gone.discard((call, label))
            log.info("worked: N1MM %s %s %s %s", kind, call, label, normalize_mode(mode))

    def is_worked(self, call: str, freq_khz: float, mode: str) -> bool:
        band = band_from_khz(freq_khz)
        if not band:
            return False
        key = (call.strip().upper(), band, normalize_mode(mode))
        if key in getattr(self, "_extra", ()):
            return True
        return key in self._keys and key[:2] not in getattr(self, "_gone", ())

    def worked_keys(self) -> Iterable[tuple[str, str, str]]:
        return frozenset(self._keys)
