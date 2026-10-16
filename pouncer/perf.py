"""CPU, memory and disk use of POUNCer - one line a minute in ``cache/perf.csv``.

So after a contest weekend we can see how POUNCer behaved: is it the CPU,
the memory or the disk, and which part of POUNCer (busy refresh tick, many
spots, many phone requests)? Every ``every_s`` (60 s) one CSV row:

- time, uptime
- POUNCer CPU % (of one core and of the whole PC), PC CPU % (Windows)
- POUNCer memory: working set and private MB, peak working set; PC memory
  used % and free MB (Windows; elsewhere what the OS gives)
- disk: free GB on the drive with the cache, the cache folder's size (MB),
  POUNCer's disk reads / writes in KB since the last row (Windows)
- threads, handles (Windows)
- **N1MM Logger+** (Windows): its CPU %, memory (working set / private),
  disk reads / writes, handles - and the PC: number of processes and the
  three programs that used the most CPU in that minute (``pc_top_cpu``)
- POUNCer's own numbers from ``stats()``: spots in the list, spots / min in,
  RBN spots / min (whole feed), queue length, refresh tick average / max ms,
  phone requests / min, phone pages open, timeline / zones events

Every 15 minutes a short summary also goes to pouncer.log. The CSV rotates
at 5 MB (one old file kept): about a week at one row a minute. Stdlib only
(ctypes on Windows) - nothing extra to install.
"""

from __future__ import annotations

import csv
import logging
import os
import shutil
import sys
import threading
import time

log = logging.getLogger(__name__)

PERF_NAME = "perf.csv"
MAX_BYTES = 5_000_000
LOG_EVERY_S = 900
_WIN = sys.platform == "win32"


# ---- Windows API (ctypes) -------------------------------------------------
if _WIN:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    class _PMC(ctypes.Structure):          # PROCESS_MEMORY_COUNTERS_EX
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]

    class _MEMSTAT(ctypes.Structure):      # MEMORYSTATUSEX
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    class _IOC(ctypes.Structure):          # IO_COUNTERS
        _fields_ = [("ReadOperationCount", ctypes.c_ulonglong), ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong), ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong), ("OtherTransferCount", ctypes.c_ulonglong)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    except OSError:
        _psapi = None
    # 64-bit handles: declare the types, or the pseudo handle (-1) gets cut to 32 bits
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.GetCurrentProcess.argtypes = []
    _k32.GetSystemTimes.argtypes = [ctypes.POINTER(_FILETIME)] * 3
    _k32.GetSystemTimes.restype = wintypes.BOOL
    _k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MEMSTAT)]
    _k32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    _k32.GetProcessIoCounters.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IOC)]
    _k32.GetProcessIoCounters.restype = wintypes.BOOL
    _k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetProcessHandleCount.restype = wintypes.BOOL
    if _psapi is not None:
        _psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
        _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FILETIME)] * 4
    _k32.GetProcessTimes.restype = wintypes.BOOL
    _QUERY = 0x1000                     # PROCESS_QUERY_LIMITED_INFORMATION

    def _ft(ft) -> int:
        return (ft.hi << 32) | ft.lo


def _proc_numbers(pid: int, full: bool) -> dict | None:
    """CPU time (100 ns), and with ``full`` memory / disk / handles, of a
    process (Windows). None if it can't be opened (gone, or protected)."""
    h = _k32.OpenProcess(_QUERY, False, pid)
    if not h:
        return None
    try:
        c, e, k, u = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
        if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
            return None
        out = {"cpu": _ft(k) + _ft(u)}
        if full:
            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            if _psapi is not None and _psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                out["ws_mb"] = pmc.WorkingSetSize / (1024 * 1024)
                out["private_mb"] = pmc.PrivateUsage / (1024 * 1024)
            io = _IOC()
            if _k32.GetProcessIoCounters(h, ctypes.byref(io)):
                out["read"], out["write"] = io.ReadTransferCount, io.WriteTransferCount
            n = wintypes.DWORD()
            if _k32.GetProcessHandleCount(h, ctypes.byref(n)):
                out["handles"] = n.value
        return out
    finally:
        _k32.CloseHandle(h)


class ProcessWatch:
    """Windows: N1MM Logger+'s own CPU / memory / disk, and the programs that
    used the most CPU in the last minute (the whole PC: who is busy)."""

    def __init__(self, n1mm_prefix: str = "n1mmlogger", top: int = 3) -> None:
        self.prefix = n1mm_prefix
        self.top = top
        self._cpu: dict[int, int] = {}          # pid -> CPU time at the last sample
        self._io: dict[int, tuple[int, int]] = {}
        self._t = time.monotonic()

    def sample(self, own_pid: int) -> dict:
        from .win32 import process_list
        procs = process_list() or []
        now = time.monotonic()
        dt = max(1e-6, now - self._t)
        self._t = now
        ncpu = os.cpu_count() or 1
        cpu_now: dict[int, int] = {}
        busy: list[tuple[float, str]] = []
        row: dict = {"pc_processes": len(procs)}
        n1mm = [(pid, name) for pid, name in procs if name.lower().replace(" ", "").startswith(self.prefix)]
        for pid, name in procs:
            if pid in (0, 4):                    # Idle / System
                continue
            full = any(pid == p for p, _ in n1mm)
            got = _proc_numbers(pid, full)
            if got is None:
                continue
            cpu_now[pid] = got["cpu"]
            prev = self._cpu.get(pid)
            core = 100 * (got["cpu"] - prev) / (dt * 1e7) if prev is not None else None
            if core is not None and pid != own_pid:
                busy.append((core, name))
            if full:
                row["n1mm_cpu_core_pct"] = round(core, 1) if core is not None else ""
                row["n1mm_cpu_pc_pct"] = round(core / ncpu, 1) if core is not None else ""
                row["n1mm_mem_ws_mb"] = _r(got.get("ws_mb"))
                row["n1mm_mem_private_mb"] = _r(got.get("private_mb"))
                row["n1mm_handles"] = got.get("handles", "")
                if "read" in got:
                    prev_io = self._io.get(pid)
                    if prev_io:
                        row["n1mm_disk_read_kb"] = round((got["read"] - prev_io[0]) / 1024)
                        row["n1mm_disk_write_kb"] = round((got["write"] - prev_io[1]) / 1024)
                    self._io = {pid: (got["read"], got["write"])}
        self._cpu = cpu_now
        row["n1mm_running"] = 1 if n1mm else 0
        busy.sort(reverse=True)
        row["pc_top_cpu"] = " | ".join(f"{name} {pct:.0f}%" for pct, name in busy[:self.top] if pct >= 0.5)
        return row



def system_times() -> tuple[int, int] | None:
    """(idle, total) in 100 ns ticks since boot (Windows), else None."""
    if not _WIN:
        return None
    idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
    if not _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
        return None
    return _ft(idle), _ft(kern) + _ft(user)       # kernel time includes idle


def process_memory() -> dict:
    """Working set / private / peak in MB."""
    if _WIN and _psapi is not None:
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        h = _k32.GetCurrentProcess()
        if _psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            mb = 1024 * 1024
            return {"ws_mb": pmc.WorkingSetSize / mb, "private_mb": pmc.PrivateUsage / mb,
                    "peak_ws_mb": pmc.PeakWorkingSetSize / mb}
        return {}
    try:                                            # Linux / macOS: what we can get
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
        rss_mb = None
        if os.path.exists("/proc/self/statm"):
            with open("/proc/self/statm") as fh:
                rss_mb = int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
        return {"ws_mb": rss_mb if rss_mb is not None else peak_mb, "peak_ws_mb": peak_mb}
    except Exception:  # noqa: BLE001
        return {}


def system_memory() -> dict:
    if _WIN:
        ms = _MEMSTAT()
        ms.dwLength = ctypes.sizeof(_MEMSTAT)
        if _k32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return {"pc_mem_pct": ms.dwMemoryLoad, "pc_free_mb": ms.ullAvailPhys / (1024 * 1024)}
        return {}
    try:
        if os.path.exists("/proc/meminfo"):
            info = {}
            with open("/proc/meminfo") as fh:
                for line in fh:
                    k, v = line.split(":", 1)
                    info[k] = int(v.split()[0])
            total, avail = info["MemTotal"], info.get("MemAvailable", info.get("MemFree", 0))
            return {"pc_mem_pct": round(100 * (total - avail) / total), "pc_free_mb": avail / 1024}
    except Exception:  # noqa: BLE001
        pass
    return {}


def process_io() -> tuple[int, int] | None:
    """(read bytes, write bytes) of POUNCer since start (Windows)."""
    if _WIN:
        io = _IOC()
        if _k32.GetProcessIoCounters(_k32.GetCurrentProcess(), ctypes.byref(io)):
            return io.ReadTransferCount, io.WriteTransferCount
        return None
    try:
        if os.path.exists("/proc/self/io"):
            vals = {}
            with open("/proc/self/io") as fh:
                for line in fh:
                    k, v = line.split(":", 1)
                    vals[k] = int(v)
            return vals.get("rchar", 0), vals.get("wchar", 0)
    except Exception:  # noqa: BLE001
        pass
    return None


def handle_count() -> int | None:
    if not _WIN:
        return None
    n = wintypes.DWORD()
    if _k32.GetProcessHandleCount(_k32.GetCurrentProcess(), ctypes.byref(n)):
        return n.value
    return None


def folder_mb(path: str) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1024 * 1024)


class PerfMonitor:
    """Samples every ``every_s`` in a background thread and writes perf.csv.

    ``stats()`` -> dict of POUNCer's own counters (added as columns)."""

    BASE = ["time", "uptime_min", "cpu_proc_core_pct", "cpu_proc_pc_pct", "cpu_pc_pct",
            "mem_ws_mb", "mem_private_mb", "mem_peak_ws_mb", "pc_mem_pct", "pc_free_mb",
            "disk_free_gb", "cache_mb", "disk_read_kb", "disk_write_kb", "threads", "handles",
            # N1MM Logger+ and the PC as a whole (Windows)
            "n1mm_running", "n1mm_cpu_core_pct", "n1mm_cpu_pc_pct", "n1mm_mem_ws_mb",
            "n1mm_mem_private_mb", "n1mm_disk_read_kb", "n1mm_disk_write_kb", "n1mm_handles",
            "pc_processes", "pc_top_cpu"]

    def __init__(self, cache_dir: str, stats=None, every_s: float = 60, clock=time.time) -> None:
        self.cache_dir = cache_dir or "cache"
        self.path = os.path.join(self.cache_dir, PERF_NAME)
        self.stats = stats
        self.every_s = every_s
        self._clock = clock
        self._stop = threading.Event()
        self._start = clock()
        self._last_wall = time.monotonic()
        self._last_cpu = time.process_time()
        self._last_sys = system_times()
        self._last_io = process_io()
        self._last_log = 0.0
        self._cache_mb = 0.0
        self._cache_checked = 0.0
        self.last: dict = {}
        self.columns: list[str] | None = None
        self._procs = ProcessWatch() if _WIN else None
        if self._procs is not None:
            try:
                self._procs.sample(os.getpid())       # the first CPU times to compare with
            except Exception as exc:  # noqa: BLE001
                log.debug("perf procs: %s", exc)

    def start(self) -> None:
        threading.Thread(target=self._run, name="perf", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.every_s):
            try:
                self.write(self.sample())
            except Exception as exc:  # noqa: BLE001 - monitoring must never hurt
                log.debug("perf: %s", exc)

    def sample(self) -> dict:
        now = self._clock()
        wall = time.monotonic()
        cpu = time.process_time()
        dt = max(1e-6, wall - self._last_wall)
        proc_core = 100 * (cpu - self._last_cpu) / dt
        ncpu = os.cpu_count() or 1
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
               "uptime_min": round((now - self._start) / 60, 1),
               "cpu_proc_core_pct": round(proc_core, 1),
               "cpu_proc_pc_pct": round(proc_core / ncpu, 1)}
        self._last_wall, self._last_cpu = wall, cpu
        st = system_times()
        if st and self._last_sys:
            idle, total = st[0] - self._last_sys[0], st[1] - self._last_sys[1]
            row["cpu_pc_pct"] = round(100 * (1 - idle / total), 1) if total > 0 else ""
        self._last_sys = st
        mem = process_memory()
        row["mem_ws_mb"] = _r(mem.get("ws_mb"))
        row["mem_private_mb"] = _r(mem.get("private_mb"))
        row["mem_peak_ws_mb"] = _r(mem.get("peak_ws_mb"))
        sm = system_memory()
        row["pc_mem_pct"] = sm.get("pc_mem_pct", "")
        row["pc_free_mb"] = _r(sm.get("pc_free_mb"), 0)
        try:
            row["disk_free_gb"] = round(shutil.disk_usage(os.path.abspath(self.cache_dir)).free / 1e9, 1)
        except OSError:
            row["disk_free_gb"] = ""
        if now - self._cache_checked > 600:            # walking the folder: every 10 min
            self._cache_mb = folder_mb(self.cache_dir)
            self._cache_checked = now
        row["cache_mb"] = round(self._cache_mb, 1)
        io = process_io()
        if io and self._last_io:
            row["disk_read_kb"] = round((io[0] - self._last_io[0]) / 1024)
            row["disk_write_kb"] = round((io[1] - self._last_io[1]) / 1024)
        self._last_io = io
        row["threads"] = threading.active_count()
        row["handles"] = handle_count() or ""
        if self._procs is not None:                    # N1MM + the busiest programs
            try:
                row.update(self._procs.sample(os.getpid()))
            except Exception as exc:  # noqa: BLE001
                row["procs_error"] = repr(exc)[:80]
        if self.stats is not None:
            try:
                for k, v in (self.stats() or {}).items():
                    row[k] = v
            except Exception as exc:  # noqa: BLE001
                row["stats_error"] = repr(exc)[:80]
        self.last = row
        return row

    def write(self, row: dict) -> None:
        if self.columns is None:
            self.columns = self.BASE + [k for k in row if k not in self.BASE]
        os.makedirs(self.cache_dir, exist_ok=True)
        if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
            try:
                os.replace(self.path, self.path + ".1")
            except OSError:
                pass
        new = not os.path.exists(self.path)
        if not new:                                     # header of an older version?
            with open(self.path, encoding="utf-8") as fh:
                old_header = fh.readline().strip()
            if old_header != ",".join(self.columns):  # (closed first: Windows can't rename an open file)
                new = True
                try:
                    os.replace(self.path, self.path + ".1")
                except OSError:
                    pass
        with open(self.path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=self.columns, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in self.columns})
        if self._clock() - self._last_log >= LOG_EVERY_S:
            self._last_log = self._clock()
            log.info("perf: POUNCer CPU %s%% of a core, memory %s MB (peak %s); N1MM CPU %s%%, "
                     "memory %s MB; PC CPU %s%%, memory %s%%, busiest: %s; "
                     "disk free %s GB, cache %s MB, threads %s, tick avg %s ms / max %s ms, "
                     "spots %s, RBN %s/min, phone %s req/min",
                     row.get("cpu_proc_core_pct"), row.get("mem_ws_mb"), row.get("mem_peak_ws_mb"),
                     row.get("n1mm_cpu_core_pct", "-"), row.get("n1mm_mem_ws_mb", "-"),
                     row.get("cpu_pc_pct", "?"), row.get("pc_mem_pct"), row.get("pc_top_cpu", "-"),
                     row.get("disk_free_gb"), row.get("cache_mb"), row.get("threads"), row.get("tick_avg_ms"),
                     row.get("tick_max_ms"), row.get("spots"), row.get("rbn_per_min"),
                     row.get("web_req_per_min"))


def _r(v, nd: int = 1):
    return "" if v is None else round(v, nd)
