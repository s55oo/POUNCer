"""HamQTH.com over HTTP - the backup when POUNCer's telnet connections die
(the HamQTH button). Data provided by HamQTH.com (free, no key, no login).

Two public JSON APIs (https://www.hamqth.com/developers.php), each polled
politely (every ``poll_s``, 20 s - HamQTH asks for 15 s at least):

* ``/api/v2/rbn`` - RBN skimmer reports: ``{"spots": [{"dxcall", "band",
  "mode", "freq", "age" (s), "skimmers": [{"call", "snr"}]}]}``; one row per
  heard signal, every skimmer that heard it. Used while RBN / Telnet (the
  skimmer telnet connections) are down. Each skimmer -> one spot
  ('<skimmer>-#'), through the same skimmer filter as RBN's.
* ``/api/v2/spots`` - DX cluster spots of people: ``{"spots": [{"spotter",
  "dxcall", "freq", "band", "mode", "comment", "time" (ISO, minutes),
  ...}]}``. Used while the Cluster telnet connection is down.
"""

from __future__ import annotations

import calendar
import json
import logging
import queue
import threading
import time
import urllib.parse
import urllib.request

from . import traffic
from .rbn import Spot, guess_mode

log = logging.getLogger(__name__)

BASE = "https://www.hamqth.com/api/v2"
NEW_S = 30          # an RBN row heard within this many seconds = a new report


def _utc(text) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return None


def rbn_spots(row: dict, now: float) -> list[Spot]:
    """One /rbn row -> a Spot per skimmer that heard it."""
    try:
        call = str(row.get("dxcall") or "").strip().upper()
        freq = float(row.get("freq"))
        age = float(row.get("age") or 0)
    except (TypeError, ValueError):
        return []
    if not call or freq <= 0:
        return []
    heard = now - max(0.0, age)
    mode = str(row.get("mode") or "CW").upper()
    out = []
    for sk in row.get("skimmers") or ():
        if not isinstance(sk, dict) or not sk.get("call"):
            continue
        try:
            snr = int(round(float(sk.get("snr"))))
        except (TypeError, ValueError):
            snr = None
        out.append(Spot(dxcall=call, freq_khz=freq, mode=mode, snr=snr, wpm=None, spot_type="",
                        spotter=str(sk["call"]).upper().rstrip("-#") + "-#",
                        time_utc=time.strftime("%H%MZ", time.gmtime(heard)),
                        received=heard, source="hamqth"))
    return out


def cluster_spot(row: dict, now: float) -> Spot | None:
    """One /spots row (a person's spot) -> Spot; None for a skimmer's."""
    try:
        call = str(row.get("dxcall") or "").strip().upper()
        spotter = str(row.get("spotter") or "").strip().upper()
        freq = float(row.get("freq"))
    except (TypeError, ValueError):
        return None
    if not call or not spotter or freq <= 0 or spotter.endswith("-#"):
        return None
    ts = _utc(row.get("time")) or now
    comment = str(row.get("comment") or "")
    given = str(row.get("mode") or "").upper()
    mode = given if given in ("CW", "SSB", "RTTY", "FT8", "FT4") else guess_mode(freq, comment)
    return Spot(dxcall=call, freq_khz=freq, mode=mode, snr=None, wpm=None,
                spot_type="", spotter=spotter, time_utc=time.strftime("%H%MZ", time.gmtime(ts)),
                received=min(ts, now), source="hamqth")


class HamQTHFeed(threading.Thread):
    """kind 'rbn' (skimmer spots, ``filt`` = the skimmer filter) or
    'spots' (people's spots, ``filt`` = the spotter-continent filter)."""

    def __init__(self, kind: str, filt, out_queue, modes=("CW",), poll_s: float = 20.0,
                 on_raw=None, fetch=None, sleep=None, version: str = "") -> None:
        super().__init__(daemon=True, name=f"hamqth-{kind}")
        self.kind = kind
        self.filt = filt
        self.queue = out_queue
        self.modes = tuple(m.upper() for m in modes)
        self.poll_s = max(15.0, poll_s)
        self.on_raw = on_raw
        self.version = version
        self._fetch = fetch or self._http_get
        self._stop_ev = threading.Event()
        self._sleep = sleep or self._stop_ev.wait
        self.seen: dict = {}
        self.first = True
        self.spots_in = 0
        self.last_ok = 0.0
        self.status_text = "starting"

    def url(self) -> str:
        if self.kind == "rbn":
            q = {"modes": ",".join(m for m in self.modes if m in ("CW", "RTTY")) or "CW",
                 "maxAge": 300 if self.first else 90, "format": "json"}
            return f"{BASE}/rbn?" + urllib.parse.urlencode(q)
        return f"{BASE}/spots?" + urllib.parse.urlencode({"limit": 200 if self.first else 60,
                                                          "format": "json"})

    def stop(self) -> None:
        self._stop_ev.set()

    def poll_once(self) -> int:
        doc = json.loads(self._fetch(self.url()).decode("utf-8"))
        rows = doc.get("spots") if isinstance(doc, dict) else doc
        now = time.time()
        new = 0
        for row in reversed(rows or []):
            if not isinstance(row, dict):
                continue
            if self.kind == "rbn":
                if not self.first and float(row.get("age") or 0) > NEW_S:
                    continue                     # heard before the last poll
                spots = rbn_spots(row, now)
            else:
                key = (row.get("spotter"), row.get("dxcall"), row.get("freq"), row.get("time"))
                if key in self.seen:
                    continue
                self.seen[key] = now
                sp = cluster_spot(row, now)
                if sp is None or (self.first and now - sp.received > 900):
                    continue
                spots = [sp]
            for sp in spots:
                if traffic._sink is not None:
                    traffic.emit("HAMQTH", f"{sp.spotter} {sp.freq_khz:.1f} {sp.dxcall} "
                                           f"{sp.mode} {sp.snr if sp.snr is not None else ''}")
                if self.on_raw is not None:
                    try:
                        self.on_raw(sp)
                    except Exception:  # noqa: BLE001
                        pass
                if self.kind == "rbn" and sp.mode not in self.modes:
                    if sp.mode in traffic.DIGITAL:
                        traffic.digi_out("HAMQTH", sp, f"dropped: mode {sp.mode} not in "
                                         f"Spots modes {','.join(self.modes)}")
                    continue
                acc = getattr(self.filt, "accept", None)
                if acc is not None and not acc(sp.spotter):
                    continue
                try:
                    self.queue.put_nowait(sp)
                    self.spots_in += 1
                    new += 1
                except queue.Full:
                    pass
        if len(self.seen) > 3000:
            self.seen = {k: t for k, t in self.seen.items() if now - t < 1800}
        self.first = False
        self.last_ok = time.time()
        self.status_text = f"{self.spots_in} spots, every {int(self.poll_s)} s"
        return new

    def run(self) -> None:
        backoff = 30
        while not self._stop_ev.is_set():
            try:
                self.poll_once()
                backoff = 30
                self._sleep(self.poll_s)
            except Exception as exc:  # noqa: BLE001 - keep polling
                self.status_text = f"error: {exc}"
                log.info("HamQTH %s: %s", self.kind, exc)
                self._sleep(backoff)
                backoff = min(backoff * 2, 300)

    def _http_get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={
            "User-Agent": f"POUNCer/{self.version} (contest spot backup)",
            "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
