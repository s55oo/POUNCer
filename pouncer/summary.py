"""Contest summary: what POUNCer brought to the open N1MM log.

POUNCer remembers every station you tap (main window, phone, NEXT) in
``cache/taps.jsonl``. The summary reads the open contest from N1MM's log
(read-only) and counts a QSO as **from POUNCer** when you tapped that call
on that band up to 15 minutes before it was logged. Multipliers are worked
out the same way POUNCer colours the tiles (first QSO with a new zone /
country on a band, WPX prefixes once per contest).

Pure logic plus a small file / sqlite reader - no tkinter - so it is
testable anywhere. The phone page shows it at ``/summary``.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import pathlib
import sqlite3
import threading
import time

from .bands import band_from_khz
from . import mults as multsmod
from .worked import PER_CONTEST_MULTS, band_label, normalize_mode, normalize_mult

log = logging.getLogger(__name__)

MATCH_S = 15 * 60          # tap -> QSO within this long counts as from POUNCer
EARLY_S = 60               # QSO logged a bit "before" the tap (clock rounding)
KEEP_DAYS = 14             # taps older than this are dropped
BREAK_S = 30 * 60          # a gap this long between QSOs = off time

MULT_NAMES = {"ZN": "Zone", "CountryPrefix": "Country", "WPXPrefix": "Prefix",
              "Sect": "Section", "Exchange1": "Exchange", "MiscText": "Misc",
              "GridSquare": "Grid", "Continent": "Continent", "CK": "Check", "Prec": "Prec"}


def _core(call: str) -> str:
    """'S5/K1ABC/P' -> 'K1ABC' (the longest part) for matching taps to QSOs."""
    parts = [p for p in str(call or "").strip().upper().split("/") if p]
    return max(parts, key=len) if parts else ""


class TapLog:
    """Every station tapped, kept on disk so a restart doesn't lose them."""

    def __init__(self, cache_dir: str = "cache", clock=time.time) -> None:
        self.path = os.path.join(cache_dir, "taps.jsonl") if cache_dir else ""
        self._clock = clock
        self._lock = threading.Lock()
        self._taps: list[tuple[float, str, str]] = []   # (time, call, band)
        self._by_station: dict[tuple[str, str], list[float]] = {}   # "tried" marks
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        cutoff = self._clock() - KEEP_DAYS * 86400
        kept = []
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                        t, call, band = float(d["t"]), str(d["c"]), str(d["b"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    if t >= cutoff and call and band:
                        kept.append((t, call, band))
        except OSError as exc:
            log.info("taps: %s", exc)
            return
        self._taps = kept
        for t, c, b in kept:
            self._by_station.setdefault((c, b), []).append(t)
        try:   # rewrite without the old ones
            with open(self.path, "w", encoding="utf-8") as fh:
                for t, c, b in kept:
                    fh.write(json.dumps({"t": round(t, 1), "c": c, "b": b}) + "\n")
        except OSError:
            pass

    def record(self, call: str, freq_khz: float) -> None:
        band = band_from_khz(freq_khz)
        call = str(call or "").strip().upper()
        if not call or not band:
            return
        t = self._clock()
        with self._lock:
            self._taps.append((t, call, band))
            self._by_station.setdefault((call, band), []).append(t)
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": round(t, 1), "c": call, "b": band}) + "\n")
        except OSError as exc:
            log.info("taps: %s", exc)

    def tried(self, call: str, band: str | None, within_s: float) -> tuple[int, float] | None:
        """Tapped (= called) this station on this band in the last
        ``within_s`` seconds: (how many times, time of the last tap)."""
        if not band:
            return None
        now = self._clock()
        with self._lock:
            times = self._by_station.get((str(call).strip().upper(), band))
            recent = [t for t in times or () if now - t <= within_s]
        return (len(recent), max(recent)) if recent else None

    def snapshot(self) -> list[tuple[float, str, str]]:
        with self._lock:
            return list(self._taps)


def _ts(text) -> float | None:
    """N1MM's TS ('2026-10-13 06:12:45', UTC) -> epoch seconds."""
    try:
        return float(calendar.timegm(time.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S")))
    except (ValueError, TypeError):
        return None


def read_qsos(path: str, contest_nr, mult_types) -> list[dict]:
    """QSOs of the open contest: {t, call, band, mults: {field: value}}."""
    uri = pathlib.Path(path).absolute().as_uri() + "?mode=ro"   # file:///C:/...
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.OperationalError:
        con = sqlite3.connect(path, timeout=2.0)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(DXLOG)")}
        names = [m["field"] if isinstance(m, dict) else m for m in mult_types]
        fields = [f for f in dict.fromkeys(names) if f and f in cols]
        sql = "SELECT TS, Call, Band, Mode" + "".join(f", {f}" for f in fields) + " FROM DXLOG"
        params: tuple = ()
        if contest_nr is not None:
            sql += " WHERE ContestNR = ?"
            params = (contest_nr,)
        out = []
        rows = con.execute(sql, params).fetchall()   # no open cursor at close (Windows)
        for row in rows:
            t, band = _ts(row[0]), band_label(row[2])
            if t is None or not row[1] or not band:
                continue
            out.append({"t": t, "call": str(row[1]).strip().upper(), "band": band,
                        "mode": row[3], "mults": dict(zip(fields, row[4:]))})
        return out
    finally:
        con.close()


def _hhmm(t: float) -> str:
    return time.strftime("%H:%M", time.gmtime(t))


def build(qsos: list[dict], taps: list[tuple[float, str, str]], mult_types=(),
          now: float | None = None) -> dict:
    """The summary (all times UTC)."""
    now = time.time() if now is None else now
    qsos = sorted(qsos, key=lambda q: q["t"])
    # taps per (call core, band), oldest first
    tapped: dict[tuple[str, str], list[float]] = {}
    for t, call, band in sorted(taps):
        tapped.setdefault((_core(call), band), []).append(t)
    first_q = qsos[0]["t"] if qsos else None
    taps_in = [x for x in taps if first_q is None or x[0] >= first_q - 3600]

    def from_pouncer(q) -> bool:
        for t in tapped.get((_core(q["call"]), q["band"]), ()):
            if t - EARLY_S <= q["t"] <= t + MATCH_S:   # logged soon after the tap
                return True
        return False

    bands: dict[str, dict] = {}
    hours: dict[str, dict] = {}
    # slots (worked.mult_slots: field + scope) or plain field names (old callers)
    slots = [m if isinstance(m, dict) else
             {"field": m, "scope": "contest" if m in PER_CONTEST_MULTS else "band"}
             for m in mult_types]
    slots = [sl for sl in slots if sl.get("field")]
    seen: dict[str, dict[str, set]] = {sl["field"]: {} for sl in slots}
    mults_from_spots, total_mults = [], 0
    pouncer = 0
    for q in qsos:
        via = from_pouncer(q)
        q["via"] = via
        pouncer += via
        b = bands.setdefault(q["band"], {"band": q["band"], "qsos": 0, "pouncer": 0,
                                         "mults": 0, "pouncer_mults": 0, "q10": 0, "q60": 0,
                                         "m60": 0})
        b["qsos"] += 1
        age = now - q["t"]
        if 0 <= age < 600:
            b["q10"] += 1
        if 0 <= age < 3600:
            b["q60"] += 1
        b["pouncer"] += via
        h = time.strftime("%Y-%m-%d %HZ", time.gmtime(q["t"]))
        hr = hours.setdefault(h, {"hour": h, "qsos": 0, "pouncer": 0})
        hr["qsos"] += 1
        hr["pouncer"] += via
        for sl in slots:
            f = sl["field"]
            v = normalize_mult(f, q["mults"].get(f))
            if v is None:
                continue
            where = multsmod.scope_key(sl["scope"], q["band"], normalize_mode(q.get("mode")))
            got = seen[f].setdefault(where, set())
            if v in got:
                continue
            got.add(v)
            total_mults += 1
            b["mults"] += 1
            if 0 <= now - q["t"] < 3600:
                b["m60"] += 1
            if via:
                b["pouncer_mults"] += 1
                mults_from_spots.append({
                    "kind": MULT_NAMES.get(f, f), "value": str(v), "band": q["band"],
                    "call": q["call"], "time": _hhmm(q["t"])})

    # best 60 minutes (sliding), and time on the air
    best, best_at, j = 0, None, 0
    for i, q in enumerate(qsos):
        while q["t"] - qsos[j]["t"] >= 3600:
            j += 1
        if i - j + 1 > best:
            best, best_at = i - j + 1, qsos[j]["t"]
    on_s = sum(b2["t"] - a["t"] for a, b2 in zip(qsos, qsos[1:])
               if b2["t"] - a["t"] < BREAK_S)
    order = ["160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m"]
    tap_qso = {(_core(c), b) for _, c, b in taps_in}
    worked_tapped = {(_core(q["call"]), q["band"]) for q in qsos if q.get("via")}
    return {
        "ok": True,
        "qsos": len(qsos),
        "pouncer": pouncer,
        "pouncer_pct": round(100 * pouncer / len(qsos)) if qsos else 0,
        "taps": len(taps_in),
        "tapped_stations": len(tap_qso),
        "tapped_worked": len(worked_tapped),
        "first": time.strftime("%Y-%m-%d %H:%M", time.gmtime(first_q)) if qsos else "",
        "last": time.strftime("%Y-%m-%d %H:%M", time.gmtime(qsos[-1]["t"])) if qsos else "",
        "on_hours": round(on_s / 3600, 1),
        "rate": round(len(qsos) / (on_s / 3600)) if on_s >= 600 else None,
        "best60": best,
        "best60_at": _hhmm(best_at) if best_at is not None else "",
        "mults": total_mults,
        "q10": sum(b["q10"] for b in bands.values()),
        "q60": sum(b["q60"] for b in bands.values()),
        "m60": sum(b["m60"] for b in bands.values()),
        "pouncer_mults": len(mults_from_spots),
        "bands": sorted(bands.values(), key=lambda b: order.index(b["band"])
                        if b["band"] in order else 99),
        "hours": [hours[k] for k in sorted(hours)],
        "mults_from_spots": mults_from_spots,
    }


def rate_line(summary: dict) -> str:
    """The window's rate line: 'Rate 84/h (10 min) · 72 last hour · 1234 QSOs
    345 mults (+12 this hour)  |  40m 380 Q 90 M (+3)  20m ...'."""
    if not summary.get("ok") or not summary.get("qsos"):
        return "Rate: no QSOs in the open contest yet"
    head = (f"Rate {summary.get('q10', 0) * 6}/h (10 min) \u00b7 {summary.get('q60', 0)} last hour"
            f" \u00b7 {summary['qsos']} QSOs {summary.get('mults', 0)} mults"
            + (f" (+{summary['m60']} this hour)" if summary.get("m60") else ""))
    bands = "   ".join(
        f"{b['band']} {b['qsos']} Q {b['mults']} M" + (f" (+{b['m60']})" if b.get("m60") else "")
        for b in summary.get("bands", []))
    return head + ("    |    " + bands if bands else "")
