"""Keep the country file (AD1C's wl_cty.dat, country-files.com) fresh.

Every ``check_days`` (5) POUNCer asks country-files.com whether
``wl_cty.dat`` is newer than its copy (If-Modified-Since - a few bytes when
it isn't). A newer file is checked (it must parse to a few hundred
countries), saved in the cache folder and used at once (``on_new(path)``);
with ``update_n1mm`` it is also copied into N1MM's ``SupportFiles`` so
N1MM uses it from its next start. The time of the last check is kept in
``cty-check.txt`` next to the copy, so a restart doesn't ask again before
5 days are over. Offline / errors: nothing changes, tried again in an hour.
"""

from __future__ import annotations

import email.utils
import logging
import os
import threading
import time
import urllib.error
import urllib.request

from .cty import CtyDB

log = logging.getLogger(__name__)

CTY_URL = "https://www.country-files.com/cty/wl_cty.dat"
MIN_ENTITIES = 300          # a real file has ~340 countries; fewer = a broken download


def newest(paths) -> str:
    """The newest existing file of ``paths`` ('' if none)."""
    found = [p for p in paths if p and os.path.exists(p)]
    return max(found, key=os.path.getmtime) if found else ""


class CtyUpdater:
    def __init__(self, cache_dir: str, n1mm_path: str = "", url: str = CTY_URL,
                 check_days: float = 5, update_n1mm: bool = False, on_new=None,
                 fetch=None, clock=time.time) -> None:
        self.url = url or CTY_URL
        self.path = os.path.join(cache_dir or "cache", "wl_cty.dat")
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
        return self.clock() - self.last_check() >= self.check_days * 86400

    def start(self) -> None:
        def loop():
            while not self._stop.is_set():
                if self.due():
                    self.check()
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
        mine = newest([self.path, self.n1mm_path])
        headers = {"User-Agent": "POUNCer"}
        if mine:
            headers["If-Modified-Since"] = email.utils.formatdate(os.path.getmtime(mine),
                                                                  usegmt=True)
        try:
            status, data = self._get(headers)
        except Exception as exc:  # noqa: BLE001 - offline: try again in an hour
            self.status = f"country file check failed: {exc}"
            log.info("CTY: %s", self.status)
            return False
        self._mark_checked()
        if status == 304 or not data:
            self.status = time.strftime("country file up to date (checked %d.%m. %H:%M)")
            log.info("CTY: %s is current", self.url)
            return False
        try:
            db = CtyDB.from_text(data.decode("latin-1"))
        except Exception as exc:  # noqa: BLE001
            db = None
            log.warning("CTY: download unreadable: %s", exc)
        n = len({e.name for e in getattr(db, "_prefixes", {}).values()}) if db else 0
        if n < MIN_ENTITIES:
            self.status = f"country file download rejected ({n} countries)"
            log.warning("CTY: %s", self.status)
            return False
        self._write(self.path, data)
        if self.update_n1mm and self.n1mm_path:
            try:
                self._write(self.n1mm_path, data)
                log.info("CTY: new wl_cty.dat copied to %s", self.n1mm_path)
            except OSError as exc:
                log.warning("CTY: could not write %s: %s", self.n1mm_path, exc)
        self.status = time.strftime("new country file %d.%m. %H:%M") + f" ({n} countries)"
        log.info("CTY: new wl_cty.dat from %s, %d countries", self.url, n)
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
