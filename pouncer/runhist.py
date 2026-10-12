"""Does a station usually RUN (call CQ) or S&P? From its past contest logs.

Uses S53ZO's public Hamradio-Contest-logs-Archives on GitHub: the ``SH6``
shards (``logs_XX.sqlite``, XX = crc32(CALL) & 0xff) index every log by
callsign; the newest logs of the same contest and mode are downloaded and
their frequencies looked at. A station that rarely runs but is spotted
calling CQ won't be there long - work it now.

Everything runs in one background thread and is cached on disk, so each
shard and each call is fetched only once. No internet / no logs = no info.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import urllib.request
import zlib
from urllib.parse import quote

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://raw.githubusercontent.com/s53zo/Hamradio-Contest-logs-Archives/main/"
MAX_LOGS = 2          # newest logs looked at per call
MIN_QSOS = 30         # fewer QSOs than this = not enough to tell
RUN_KHZ = 0.3         # same frequency (kHz) as one of the last 2 QSOs = running
RARE_RUN_PCT = 30     # runs less than this % of QSOs = "rarely runs" (marked)

# N1MM contest name prefix -> archive contest folder.
CONTESTS = {
    "CQWWRTTY": "CQWWRTTY", "CQWPXRTTY": "CQWPXRTTY", "CQWW": "CQWW",
    "CQWPX": "CQWPX", "CQ160": "CQ160", "ARRLDX": "ARRL", "WAE": "WAE",
    "EUHFC": "EUHFC", "OKOMDX": "OK_OM_DX_Contest", "SPDX": "SPDX_contest",
    "UKRAINDX": "URE", "YUDX": "YU_DX_Contest", "KVP": "ZRS_KVP",
}
_MODES = {"CW": "CW", "SSB": "PH", "PH": "PH", "USB": "PH", "LSB": "PH",
          "RTTY": "RTTY", "FT8": "DIGI", "FT4": "DIGI"}


def shard_name(call: str) -> str:
    return f"logs_{zlib.crc32(call.upper().encode()) & 0xff:02x}.sqlite"


def archive_target(n1mm_contest: str, mode: str) -> tuple[str | None, str]:
    """(archive contest folder or None, archive mode) for N1MM's contest."""
    name = (n1mm_contest or "").upper().replace(" ", "")
    contest = next((v for k, v in CONTESTS.items() if name.startswith(k)), None)
    return contest, _MODES.get((mode or "CW").upper(), "CW")


def pick_logs(rows: list[tuple], contest: str | None, mode: str) -> list[str]:
    """Newest paths: same contest+mode first, else same mode, any contest.

    ``rows`` = (path, contest, year, mode) from a shard.
    """
    real = [r for r in rows if not r[0].startswith("RECONSTRUCTED_LOGS/")]
    for want in ((lambda r: r[1] == contest and r[3] == mode),
                 (lambda r: r[3] == mode)):
        hits = sorted((r for r in real if want(r)), key=lambda r: r[2] or 0, reverse=True)
        if hits:
            return [r[0] for r in hits[:MAX_LOGS]]
    return []


def run_share(cabrillo: str) -> tuple[int, int]:
    """(QSOs made on the same frequency as one of the last two on that
    band, all QSOs). Two back, so a multi-op's second station interleaving
    on the band doesn't hide the runner."""
    run = total = 0
    last: dict[int, list[float]] = {}
    for line in cabrillo.splitlines():
        if not line.startswith("QSO:"):
            continue
        parts = line.split()
        try:
            freq = float(parts[1])
        except (IndexError, ValueError):
            continue
        if freq < 1800:  # band given instead of kHz - can't tell
            continue
        band = int(freq // 1000)
        prev = last.setdefault(band, [])
        total += 1
        if any(abs(freq - p) <= RUN_KHZ for p in prev):
            run += 1
        prev.append(freq)
        del prev[:-2]
    return run, total


class RunHistory:
    """Background lookups of how often a call runs; ``get()`` never blocks."""

    def __init__(self, cache_dir: str, fetch=None, base_url: str = ARCHIVE_URL) -> None:
        self.cache_dir = cache_dir
        self.base_url = base_url
        self._fetch = fetch or self._http_get
        self._file = os.path.join(cache_dir, "runhist.json")
        self._lock = threading.Lock()
        self._results: dict[str, int | None] = {}  # "CALL|CONTEST|MODE" -> run %
        self._asked: set[str] = set()
        self._queue: queue.Queue = queue.Queue()
        try:
            with open(self._file, encoding="utf-8") as fh:
                self._results = json.load(fh)
        except (OSError, ValueError):
            pass
        threading.Thread(target=self._worker, name="runhist", daemon=True).start()

    def get(self, call: str, n1mm_contest: str, mode: str) -> int | None:
        """Run % for the call (None = unknown / not looked up yet)."""
        contest, amode = archive_target(n1mm_contest, mode)
        key = f"{call.upper()}|{contest or '*'}|{amode}"
        with self._lock:
            if key in self._results:
                return self._results[key]
            if key not in self._asked:
                self._asked.add(key)
                self._queue.put((key, call.upper(), contest, amode))
        return None

    def _worker(self) -> None:
        while True:
            key, call, contest, mode = self._queue.get()
            try:
                pct = self._lookup(call, contest, mode)
            except Exception as exc:  # network etc.: try again next time
                log.info("run history %s: %s", call, exc)
                with self._lock:
                    self._asked.discard(key)
                continue
            with self._lock:
                self._results[key] = pct
                data = dict(self._results)
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(self._file, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
            except OSError:
                pass

    def _lookup(self, call: str, contest: str | None, mode: str) -> int | None:
        shard = os.path.join(self.cache_dir, "SH6", shard_name(call))
        if not os.path.exists(shard):
            data = self._fetch(self.base_url + "SH6/" + shard_name(call))
            os.makedirs(os.path.dirname(shard), exist_ok=True)
            with open(shard + ".part", "wb") as fh:
                fh.write(data)
            os.replace(shard + ".part", shard)
        con = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT path, contest, year, mode FROM logs WHERE callsign = ?", (call,)
            ).fetchall()
        finally:
            con.close()
        run = total = 0
        for path in pick_logs(rows, contest, mode):
            r, t = run_share(self._fetch(self.base_url + quote(path)).decode("latin-1", "replace"))
            run, total = run + r, total + t
        return round(100 * run / total) if total >= MIN_QSOS else None

    @staticmethod
    def _http_get(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "POUNCer"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
