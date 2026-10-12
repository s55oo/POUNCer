"""Does a station usually RUN (call CQ) or S&P? From its past contest logs.

Uses S53ZO's public Hamradio-Contest-logs-Archives on GitHub: the ``SH6``
shards (``logs_XX.sqlite``, XX = crc32(CALL) & 0xff) index every log by
callsign; its logs of the last 6 years of the same contest and mode (else
its other contests in that mode) are downloaded and their frequencies and
times looked at: how much of it was RUN, and how many
hours the station was on the air. A station that rarely runs, or is only on
for a few hours, but is spotted calling CQ won't be there long - work it now.
Stations that never sent a log are covered by the archive's reconstructed
logs (built from other stations' logs; a lower bound, marked ``rec``).

Lookups run in background threads and are cached on disk, so each shard and
each call is fetched only once. No internet / no logs = no info.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

log = logging.getLogger(__name__)

# Warm-up list (server/warmlist.py, built by GitHub Actions in POUNCer's repo)
WARMUP_URL = "https://github.com/s55oo/POUNCer/releases/download/warmup/warm.sqlite"
ARCHIVE_URL = "https://raw.githubusercontent.com/s53zo/Hamradio-Contest-logs-Archives/main/"
YEARS_BACK = 6        # logs of the last 6 years (+ this year) are used
MAX_LOGS = 6          # at most this many logs per call (downloaded in parallel)
FETCH_PARALLEL = 12   # downloads at once over all lookups
WORKERS = 8           # lookups in parallel (a new band = many new calls)
MIN_QSOS = 30         # fewer QSOs than this = not enough to tell
RUN_KHZ = 0.3         # same frequency (kHz) as one of the last 2 QSOs = running
RARE_RUN_PCT = 30     # runs less than this % of QSOs = "rarely runs" (marked)
RARE_HOURS = 6        # on the air less than this many hours = rare (marked)
OFF_MINUTES = 30      # a pause longer than this between QSOs = off time

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


def pick_logs(
    rows: list[tuple], contest: str | None, mode: str, this_year: int | None = None
) -> tuple[list[str], bool]:
    """(paths, reconstructed?) of the logs to measure, newest first.

    Only the last YEARS_BACK years. Same contest + mode first (e.g. CQWW CW
    2020-2025, up to MAX_LOGS); if the station has none, its other contests
    in the same mode (other CW contests). Submitted logs only; reconstructed
    logs (built from other stations' logs) only when it never sent one.

    ``rows`` = (path, contest, year, mode) from a shard.
    """
    import datetime

    first = (this_year or datetime.date.today().year) - YEARS_BACK
    recent = [r for r in rows if (r[2] or 0) >= first]
    real = [r for r in recent if not r[0].startswith("RECONSTRUCTED_LOGS/")]
    rebuilt = [r for r in recent if r[0].startswith("RECONSTRUCTED_LOGS/")]
    for pool, rec in ((real, False), (rebuilt, True)):
        for want in ((lambda r: r[1] == contest and r[3] == mode),
                     (lambda r: r[3] == mode)):
            hits = sorted((r for r in pool if want(r)), key=lambda r: r[2] or 0, reverse=True)
            if hits:
                return [r[0] for r in hits[:MAX_LOGS]], rec
    return [], False


def on_air_hours(cabrillo: str) -> float:
    """Hours on the air: time between QSOs, pauses over OFF_MINUTES = off."""
    import datetime

    times = []
    for line in cabrillo.splitlines():
        if line.startswith("QSO:"):
            parts = line.split()
            try:
                times.append(datetime.datetime.strptime(parts[3] + parts[4], "%Y-%m-%d%H%M"))
            except (IndexError, ValueError):
                continue
    times.sort()
    minutes = sum(
        gap for gap in ((b - a).total_seconds() / 60 for a, b in zip(times, times[1:]))
        if gap <= OFF_MINUTES
    )
    return round(minutes / 60, 1)


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

    def __init__(
        self, cache_dir: str, fetch=None, base_url: str = ARCHIVE_URL, server: str = "",
        run_list: str = "",
    ) -> None:
        self.cache_dir = cache_dir
        self.base_url = base_url
        self._fetch = fetch or self._http_get
        # v3: last 6 years of logs (older entries were from one log only)
        self._file = os.path.join(cache_dir, "runhist3.json")
        self._lock = threading.Lock()
        # "CALL|CONTEST|MODE" -> {"run": %, "hours": h, "rec": bool} or None
        self._results: dict[str, dict | None] = {}
        self._asked: set[str] = set()
        self._queue: queue.Queue = queue.Queue()
        try:
            with open(self._file, encoding="utf-8") as fh:
                self._results = {  # older caches stored just the run %
                    k: ({"run": v} if isinstance(v, int) else v)
                    for k, v in json.load(fh).items()
                }
        except (OSError, ValueError):
            pass
        # Optional run list server (server/runlist.py): whole contest list in
        # one request, so most calls are known before they are ever spotted.
        self.server = server.rstrip("/")
        self.on_learn = None  # callback(key, value) after a lookup (team sharing)
        self.warm_status = ""  # e.g. "23456 stations known in advance"
        self._lists: dict[tuple, threading.Event] = {}
        self._save_lock = threading.Lock()
        self._fetch_slots = threading.BoundedSemaphore(FETCH_PARALLEL)
        self._shard_locks: dict[str, threading.Lock] = {}
        if run_list:
            self.load_run_list(run_list)
        for i in range(WORKERS):
            threading.Thread(target=self._worker, name=f"runhist{i}", daemon=True).start()

    def warm_up(self, url: str = WARMUP_URL, max_age_days: float = 30) -> None:
        """In the background: load the ready-made warm-up list (run % and hours
        for every station of the big CW contests, built on GitHub) from the
        cache, and download a fresh one if it is missing or older than
        ``max_age_days``."""
        def run() -> None:
            path = os.path.join(self.cache_dir, "warm.sqlite")
            if os.path.exists(path):
                self.load_run_list(path)
            fresh = os.path.exists(path) and (
                time.time() - os.path.getmtime(path) < max_age_days * 86400)
            if fresh:
                return
            try:
                data = self._fetch(url)
                if not data.startswith(b"SQLite format 3"):
                    raise ValueError("not a SQLite file")
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(path + ".part", "wb") as fh:
                    fh.write(data)
                os.replace(path + ".part", path)
            except Exception as exc:  # offline etc.: keep what we have
                self.warm_status = f"warm-up download failed: {exc}"
                log.info("warm-up download from %s failed: %s", url, exc)
                return
            self.load_run_list(path)

        threading.Thread(target=run, name="warmup", daemon=True).start()

    def load_run_list(self, path: str) -> int:
        """Load a ready-made run list (runlist.sqlite from server/runlist.py):
        newest year per call, contest and mode. Those calls are known at once,
        no download. Returns how many were loaded."""
        if not os.path.exists(path):
            return 0
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                tables = {r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "warm" in tables:  # warm-up list: aggregated, with hours
                    rows = [(c, ct, m, {"run": r, "hours": h, "rec": bool(rec)})
                            for c, ct, m, r, h, rec in con.execute(
                                "SELECT call, contest, mode, run, hours, rec FROM warm")]
                else:                 # runlist.sqlite (server/runlist.py)
                    rows = [(c, ct, m, {"run": r}) for c, ct, m, r in con.execute(
                        "SELECT call, contest, mode, run FROM runs r WHERE year ="
                        " (SELECT MAX(year) FROM runs WHERE call = r.call"
                        " AND contest = r.contest AND mode = r.mode)")]
            finally:
                con.close()
        except sqlite3.Error as exc:
            log.warning("run list %s unreadable: %s", path, exc)
            return 0
        with self._lock:
            for call, contest, mode, value in rows:
                self._results.setdefault(f"{call.upper()}|{contest}|{mode}", value)
        self.warm_status = f"{len(rows)} stations known in advance"
        log.info("run list %s: %d entries", path, len(rows))
        return len(rows)

    def merge(self, data: dict) -> int:
        """Take results learned elsewhere (team peers); never overwrites."""
        added = 0
        with self._lock:
            for key, value in data.items():
                if isinstance(key, str) and key.count("|") == 2 and key not in self._results:
                    if value is None or isinstance(value, dict):
                        self._results[key] = value
                        added += 1
        if added:
            self._save()
        return added

    def snapshot(self) -> dict:
        """Everything known (for a team peer that just joined)."""
        with self._lock:
            return dict(self._results)

    def get(self, call: str, n1mm_contest: str, mode: str) -> dict | None:
        """{"run": %, "hours": h or None, "rec": bool} (None = unknown / not
        looked up yet)."""
        contest, amode = archive_target(n1mm_contest, mode)
        key = f"{call.upper()}|{contest or '*'}|{amode}"
        with self._lock:
            if key in self._results:
                return self._results[key]
            if self.server and contest and (contest, amode) not in self._lists:
                self._lists[(contest, amode)] = threading.Event()
                threading.Thread(target=self._load_list, args=(contest, amode),
                                 daemon=True).start()
            if key not in self._asked:
                self._asked.add(key)
                self._queue.put((key, call.upper(), contest, amode))
        return None

    def _load_list(self, contest: str, mode: str) -> None:
        """Whole run list for a contest + mode from the run list server."""
        try:
            data = json.loads(self._fetch(
                f"{self.server}/runs?contest={quote(contest)}&mode={quote(mode)}"
            ))
            with self._lock:
                for call, pct in data.items():
                    self._results[f"{call.upper()}|{contest}|{mode}"] = {"run": pct}
            log.info("run list %s %s: %d calls from %s", contest, mode, len(data), self.server)
            self._save()
        except Exception as exc:  # server down: per-call lookups still work
            log.info("run list server %s: %s", self.server, exc)
        finally:
            self._lists[(contest, mode)].set()

    def _worker(self) -> None:
        while True:
            key, call, contest, mode = self._queue.get()
            loading = self._lists.get((contest, mode))
            if loading is not None:  # list on its way: don't fetch twice
                loading.wait(15)
                with self._lock:
                    if key in self._results:
                        continue
            try:
                pct = self._lookup(call, contest, mode)
            except Exception as exc:  # network etc.: try again next time
                log.info("run history %s: %s", call, exc)
                with self._lock:
                    self._asked.discard(key)
                continue
            with self._lock:
                self._results[key] = pct
            self._save()
            if self.on_learn is not None:  # tell the team
                try:
                    self.on_learn(key, pct)
                except Exception:
                    pass

    def _save(self) -> None:
        with self._save_lock:
            with self._lock:
                data = dict(self._results)
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(self._file + ".part", "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                os.replace(self._file + ".part", self._file)
            except OSError:
                pass

    def _lookup(self, call: str, contest: str | None, mode: str) -> dict | None:
        name = shard_name(call)
        shard = os.path.join(self.cache_dir, "SH6", name)
        with self._lock:
            shard_lock = self._shard_locks.setdefault(name, threading.Lock())
        with shard_lock:  # two calls in one shard: download it once
            if not os.path.exists(shard):
                data = self._fetch(self.base_url + "SH6/" + name)
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
        paths, rec = pick_logs(rows, contest, mode)
        if not paths:
            return None

        def measure(path: str) -> tuple[int, int, float]:
            with self._fetch_slots:
                text = self._fetch(self.base_url + quote(path)).decode("latin-1", "replace")
            r, t = run_share(text)
            return r, t, on_air_hours(text)

        with ThreadPoolExecutor(max_workers=len(paths)) as pool:
            stats = [st for st in pool.map(measure, paths) if st[1] > 0]
        run = sum(st[0] for st in stats)
        total = sum(st[1] for st in stats)
        if total < MIN_QSOS:
            return None
        # run %: over all QSOs of those logs; hours: average per contest
        return {"run": round(100 * run / total),
                "hours": round(sum(st[2] for st in stats) / len(stats), 1),
                "rec": rec, "logs": len(stats)}

    @staticmethod
    def _http_get(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "POUNCer"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
