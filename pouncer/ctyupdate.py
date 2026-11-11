"""Keep the country file fresh: S53M's cty.dat, every Friday (v0.7.187).

The standard AD1C ``cty.dat`` (continents, countries, zones - the list CQWW
uses) comes from S53M's server. It is downloaded every Friday (``weekly``):
at the first start after the most recent Friday 00:00 UTC, and checked
hourly while POUNCer runs; "changed since our copy?" (If-Modified-Since) so
an unchanged file costs a few bytes. A newer file is checked (it must parse
to a few hundred countries), saved in the cache folder under its own name
and used at once (``on_new(path)``). ``update_n1mm`` copies it into N1MM's
``SupportFiles`` only when it IS a wl_cty.dat (N1MM needs that variant -
the full cty.dat lacks its WAE entries). The time of the last check is kept
in ``cty-check.txt``. Offline / errors: nothing changes, tried again in an
hour.
"""

from __future__ import annotations

import email.utils
import logging
import os
import threading
import time
import urllib.error
import urllib.request

from . import weekly
from .cty import CtyDB

log = logging.getLogger(__name__)

CTY_URL = "https://azure.s53m.com/cors/cty.dat"
MIN_ENTITIES = 300          # a real file has ~340 countries; fewer = a broken download


def cache_name(url: str) -> str:
    """The cache file's name: the URL's file name (cty.dat / wl_cty.dat)."""
    name = os.path.basename((url or "").split("?")[0]) or "cty.dat"
    return name if name.lower().endswith(".dat") else "cty.dat"


def _validate(data: bytes) -> int:
    db = CtyDB.from_text(data.decode("latin-1"))
    count = len({e.name for e in db._prefixes.values()})
    if count < MIN_ENTITIES:
        raise ValueError(f"country file download rejected ({count} countries)")
    return count


def newest(paths) -> str:
    """The newest existing file of ``paths`` ('' if none)."""
    found = [p for p in paths if p and os.path.exists(p)]
    return max(found, key=os.path.getmtime) if found else ""


class CtyUpdater:
    def __init__(self, cache_dir: str, n1mm_path: str = "", url: str = CTY_URL,
                 check_days: float = 5, update_n1mm: bool = False, on_new=None,
                 fetch=None, clock=time.time) -> None:
        self.url = url or CTY_URL
        self.path = os.path.join(cache_dir or "cache", cache_name(self.url))
        self.check_path = os.path.join(cache_dir or "cache", "cty-check.txt")
        self.n1mm_path = n1mm_path
        self.check_days = max(1.0, float(check_days))
        self.update_n1mm = update_n1mm
        self.on_new = on_new
        self._fetch = fetch                   # tests: (url, headers) -> (status, bytes)
        self.clock = clock
        self.status = ""
        self._stop = threading.Event()

    # ---- when -----------------------------------------------------------------------
    def last_check(self) -> float:
        try:
            with open(self.check_path, encoding="ascii") as fh:
                return float(fh.read().strip() or 0)
        except (OSError, ValueError):
            return 0.0

    def due(self) -> bool:
        """A Friday has passed since the last check (or there is no copy)."""
        if not os.path.exists(self.path):
            return True
        return weekly.due(self.last_check(), self.clock())

    def start(self) -> None:
        def loop():
            while not self._stop.is_set():
                if self.due():
                    try:
                        self.check()
                    except Exception as exc:  # a bad local file must not end future checks
                        self.status = f"country file check failed: {exc}"
                        log.exception("CTY: %s", self.status)
                self._stop.wait(3600)
        threading.Thread(target=loop, name="cty-update", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # ---- the check ------------------------------------------------------------------
    def _get(self, headers: dict) -> tuple[int, bytes]:
        if self._fetch is not None:
            return self._fetch(self.url, headers)
        req = urllib.request.Request(self.url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return 304, b""
            raise

    def check(self) -> bool:
        """Ask for a newer file; True if one was installed."""
        same_kind = (os.path.basename(self.n1mm_path).casefold()
                     == os.path.basename(self.path).casefold())
        copy_n1mm = self.update_n1mm and bool(self.n1mm_path) and same_kind
        try:
            mine = self.path if os.path.exists(self.path) else ""
            headers = {"User-Agent": "POUNCer"}
            if mine:
                headers["If-Modified-Since"] = email.utils.formatdate(os.path.getmtime(mine),
                                                                      usegmt=True)
            status, data = self._get(headers)
            if status == 304:
                # A previous valid cache install may have failed to copy into
                # N1MM. Recover even across a restart / unchanged server reply,
                # without overwriting an independently newer N1MM file.
                if (copy_n1mm and mine and
                        (not os.path.exists(self.n1mm_path) or
                         os.path.getmtime(self.path) >= os.path.getmtime(self.n1mm_path))):
                    with open(self.path, "rb") as fh:
                        cached = fh.read()
                    _validate(cached)
                    self._write(self.n1mm_path, cached)
                self._mark_checked()
                self.status = time.strftime("country file up to date (checked %d.%m. %H:%M)")
                log.info("CTY: %s is current", self.url)
                return False
            if status != 200 or not data:
                raise ValueError(f"country file download rejected (HTTP {status}, empty or invalid response)")
            n = _validate(data)
            self._write(self.path, data)
        except Exception as exc:  # noqa: BLE001 - offline: try again in an hour
            self.status = f"country file check failed: {exc}"
            log.info("CTY: %s", self.status)
            return False
        copy_error = None
        if self.update_n1mm and self.n1mm_path and not same_kind:
            log.info("CTY: %s not copied into N1MM (it uses %s)", os.path.basename(self.path),
                     os.path.basename(self.n1mm_path))
        if copy_n1mm:
            try:
                self._write(self.n1mm_path, data)
                log.info("CTY: new wl_cty.dat copied to %s", self.n1mm_path)
            except OSError as exc:
                copy_error = exc
                log.warning("CTY: could not write %s: %s", self.n1mm_path, exc)
        if copy_error is None:
            self._mark_checked()
        self.status = time.strftime("new country file %d.%m. %H:%M") + f" ({n} countries)"
        if copy_error is not None:
            self.status += f"; N1MM copy failed, retry in an hour: {copy_error}"
        log.info("CTY: new %s from %s, %d countries", os.path.basename(self.path), self.url, n)
        if self.on_new is not None:
            try:
                self.on_new(self.path)
            except Exception:  # noqa: BLE001
                log.exception("CTY reload")
        return True

    def _mark_checked(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.check_path) or ".", exist_ok=True)
            with open(self.check_path, "w", encoding="ascii") as fh:
                fh.write(f"{self.clock():.0f}")
        except OSError:
            pass

    @staticmethod
    def _write(path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".new"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
