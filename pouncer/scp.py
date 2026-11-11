"""Super Check Partial (SCP): calls known to be active in contests.

A spotted call that is not in the SCP list is often a skimmer error - one
letter wrong ("busted") - so the spot gets a "?" and you can decide whether
the tap is worth it.

The list comes from S53M's server as ``MASTER.DTA`` (v0.7.187; the binary
format of CT / WriteLog: an index of 4-byte offsets, then the calls, each
ending in a zero byte, each call stored once per search group - ~50 000
different calls). A text ``MASTER.SCP`` URL (one call per line) still works.
It is downloaded every Friday (``weekly``): at the first start after the
most recent Friday 00:00 UTC, and checked hourly while POUNCer runs; the
server is asked "changed since our copy?" so an unchanged list costs a few
bytes. ``update_n1mm`` writes N1MM a normal text MASTER.SCP (never the
binary file). N1MM's own MASTER.SCP is only a last resort, before the first
download ever (e.g. a first start offline).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import email.utils
import time
import struct
import urllib.error
import urllib.request

from . import weekly

log = logging.getLogger(__name__)

SCP_URL = "https://azure.s53m.com/cors/MASTER.DTA"
MIN_CALLS = 10000          # a real MASTER.SCP has ~50 000; fewer = broken download
_CALL = re.compile(r"^[A-Z0-9/]{3,}$")
_SIMPLE = re.compile(r"^[A-Z0-9]*\d[A-Z0-9]*[A-Z][A-Z0-9]*$|^[A-Z0-9]*[A-Z][A-Z0-9]*\d[A-Z0-9]*$")
# suffixes that don't change who it is
_PORTABLE = {"P", "M", "MM", "AM", "QRP", "A", "B", "LH", "LGT", "J", "R", "T"}


def parse(text: str) -> set[str]:
    calls = set()
    for line in text.splitlines():
        line = line.strip().upper()
        if not line or line.startswith("#"):
            continue
        if _CALL.match(line):
            calls.add(line)
    return calls


def parse_dta(data: bytes) -> set[str]:
    """MASTER.DTA: little-endian 4-byte offsets (the first one = where the
    calls start), then the calls, each ending in a zero byte."""
    if len(data) < 8:
        return set()
    first = struct.unpack_from("<I", data, 0)[0]
    if first < 4 or first % 4 or first >= len(data):
        return set()
    offsets = struct.unpack_from(f"<{first // 4}I", data, 0)
    if any(b < a for a, b in zip(offsets, offsets[1:])) or offsets[-1] > len(data):
        return set()                    # not an index: not a MASTER.DTA
    calls = set()
    for raw in data[first:].split(b"\0"):
        c = raw.decode("latin-1").strip().upper()
        if c and _CALL.match(c):
            calls.add(c)
    return calls


def is_dta(name: str) -> bool:
    return name.upper().split("?")[0].endswith(".DTA")


def parse_bytes(data: bytes, name: str) -> set[str]:
    """A downloaded / saved list: MASTER.DTA (binary) or MASTER.SCP (text)."""
    if is_dta(name):
        return parse_dta(data)
    return parse(data.decode("latin-1", "replace"))


def base_calls(call: str) -> list[str]:
    """The call and its home call(s): K1ABC/P -> K1ABC; EA8/K1ABC -> K1ABC;
    K1ABC/VE3 -> K1ABC (the part that looks like a full call)."""
    call = call.strip().upper()
    out = [call]
    parts = [p for p in call.split("/") if p]
    if len(parts) > 1:
        cands = [p for p in parts if p not in _PORTABLE and _SIMPLE.match(p)]
        # the longest part with letters and a digit is the home call
        cands.sort(key=len, reverse=True)
        out += cands[:1]
    return out


class ScpList:
    """Background-loaded SCP set; ``busted(call)`` never blocks."""

    def __init__(self, cache_dir: str, n1mm_ini: str = "", url: str = SCP_URL,
                 refresh_days: float = 7, update_n1mm: bool = False, fetch=None,
                 n1mm_dirs=()) -> None:
        self.cache_dir = cache_dir
        self.url = url
        self.refresh_days = refresh_days
        self.update_n1mm = update_n1mm
        # fetch(url) -> bytes (old style, tests) or the conditional getter below
        self._fetch = fetch
        self.last_check = ""             # "up to date" / "new list" + time
        self.path = os.path.join(cache_dir, "MASTER.DTA" if is_dta(url or "") else "MASTER.SCP")
        self.check_path = os.path.join(cache_dir, "scp-check.txt")
        self.clock = time.time
        dirs = [os.path.dirname(n1mm_ini)] if n1mm_ini else []
        dirs += list(n1mm_dirs)
        self.n1mm_files = [os.path.join(d, "SupportFiles", "MASTER.SCP") for d in dirs if d]
        self._calls: frozenset[str] = frozenset()
        self.status = "SCP: not loaded"
        self.source = ""
        self._lock = threading.Lock()

    # ---- use ------------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return bool(self._calls)

    def count(self) -> int:
        return len(self._calls)

    def busted(self, call: str) -> bool:
        """True = not in the SCP list (maybe a skimmer error). False when the
        list isn't loaded (no marks rather than wrong marks)."""
        calls = self._calls
        if not calls or not call:
            return False
        return not any(c in calls for c in base_calls(call))

    # ---- load / update ----------------------------------------------------

    def start(self) -> None:
        """Load the copy we have; download when a Friday has passed since the
        last download (checked at start and every hour)."""
        def loop():
            self.load_and_update()
            while True:
                time.sleep(3600)
                if self.url and self.due():
                    self.update()
        threading.Thread(target=loop, name="scp", daemon=True).start()

    def due(self) -> bool:
        if not os.path.exists(self.path):
            return True
        return weekly.due(weekly.read_stamp(self.check_path), self.clock())

    def load_and_update(self) -> None:
        self._load_best()
        if self.url and self.due():
            self.update()                # cheap: 304 when the copy is current

    def _stale(self) -> bool:
        try:
            age = time.time() - os.path.getmtime(self.path)
        except OSError:
            return True
        return age > self.refresh_days * 86400

    def _load_best(self) -> None:
        """Our downloaded copy (MASTER.DTA, or an older MASTER.SCP download),
        else - before the first download ever - N1MM's own MASTER.SCP."""
        older = os.path.join(self.cache_dir, "MASTER.SCP")
        for path in dict.fromkeys([self.path, older, *self.n1mm_files]):
            if self._load(path):
                return

    def _load(self, path: str) -> bool:
        try:
            with open(path, "rb") as fh:
                calls = parse_bytes(fh.read(), path)
        except OSError:
            return False
        if len(calls) < MIN_CALLS:
            return False
        with self._lock:
            self._calls = frozenset(calls)
        when = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
        self.source = path
        self.status = (f"SCP: {len(calls)} calls ({when}"
                       f"{', N1MM copy' if path in self.n1mm_files else ''})")
        return True

    def _etag_path(self) -> str:
        return self.path + ".etag"

    def _conditional_get(self) -> tuple[int, bytes, str, str]:
        """(status, body, Last-Modified, ETag); 304 = our copy is current."""
        if self._fetch is not None:                 # tests / old style: always the file
            return 200, self._fetch(self.url), "", ""
        headers = {"User-Agent": "POUNCer"}
        if os.path.exists(self.path):
            headers["If-Modified-Since"] = email.utils.formatdate(
                os.path.getmtime(self.path), usegmt=True)
            try:
                with open(self._etag_path(), encoding="ascii") as fh:
                    tag = fh.read().strip()
                if tag:
                    headers["If-None-Match"] = tag
            except OSError:
                pass
        req = urllib.request.Request(self.url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return (resp.status, resp.read(), resp.headers.get("Last-Modified") or "",
                        resp.headers.get("ETag") or "")
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return 304, b"", "", ""
            raise

    def update(self) -> bool:
        """Ask the server for a newer MASTER.SCP; True if a new good list was
        installed (False also when ours is current - 304)."""
        try:
            status, data, modified, etag = self._conditional_get()
            if status == 304:
                weekly.write_stamp(self.check_path, self.clock())
                self.last_check = time.strftime("up to date (checked %H:%M)")
                log.info("SCP: %s is current", self.url)
                if self.loaded:
                    self.status = self.status.split(" - ")[0] + " - " + self.last_check
                return False
            calls = parse_bytes(data, self.url)
            if len(calls) < MIN_CALLS:
                raise ValueError(f"only {len(calls)} calls - not a "
                                 f"{'MASTER.DTA' if is_dta(self.url) else 'MASTER.SCP'}")
            os.makedirs(self.cache_dir, exist_ok=True)
            self._write(self.path, data)
            # our copy gets the server's date: the next "changed since?" is exact
            if modified:
                try:
                    t = email.utils.parsedate_to_datetime(modified).timestamp()
                    os.utime(self.path, (t, t))
                except (TypeError, ValueError, OSError):
                    pass
            try:
                if etag:
                    with open(self._etag_path(), "w", encoding="ascii") as fh:
                        fh.write(etag)
                elif os.path.exists(self._etag_path()):
                    os.remove(self._etag_path())
            except OSError:
                pass
        except Exception as exc:  # offline etc.: keep what we have
            log.info("SCP download from %s failed: %s", self.url, exc)
            if not self.loaded:
                self.status = f"SCP: download failed ({exc})"
            return False
        weekly.write_stamp(self.check_path, self.clock())
        self._load(self.path)
        log.info("SCP: new list from %s, %d calls", self.url, len(calls))
        if self.update_n1mm:
            # N1MM reads a text MASTER.SCP: one call per line, never the binary DTA
            if is_dta(self.url):
                data = ("\n".join(sorted(calls)) + "\n").encode("latin-1")
            for path in self.n1mm_files:
                if os.path.isdir(os.path.dirname(path)):
                    try:
                        self._write(path, data)
                        log.info("SCP: N1MM's %s updated", path)
                    except OSError as exc:
                        log.info("SCP: could not update %s: %s", path, exc)
                    break
        return True

    @staticmethod
    def _write(path: str, data: bytes) -> None:
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)


