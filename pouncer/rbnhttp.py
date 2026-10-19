"""RBN over HTTP: a backup feed when RBN's telnet is down or silent.

S53ZO's Azure server keeps one connection to RBN and serves every skimmer
report over HTTPS in pages (``/cors/rbn/live``): the first request asks for
the last minutes (``lookbackSeconds``), each next one only for what came
after the ``cursor`` it got back. No login (so no clash with the SSB
cluster or the telnet backup using the same call), works where telnet is
blocked, and after a dropout it simply catches up from the cursor.

Response: ``{"cursor", "reset", "hasMore", "spots": [{"spotter",
"spotterRaw", "dxCall", "freqKHz", "mode", "snr", "speed", "ts"}]}`` (``ts``
in ms UTC). There is no CQ / beacon type: ``/B`` calls are still dropped
as beacons by ``is_beacon``. Spots go through the same skimmer filter and
queue as the telnet ones.
"""

from __future__ import annotations

from . import traffic

import json
import logging
import queue
import threading
import time
import urllib.parse
import urllib.request

from .rbn import Spot

log = logging.getLogger(__name__)

HTTP_URL = "https://azure.s53m.com/cors/rbn/live"


def to_spot(raw: dict) -> Spot | None:
    try:
        call = str(raw.get("dxCall") or "").strip().upper()
        freq = float(raw["freqKHz"])
        ts = float(raw["ts"]) / 1000.0
    except (KeyError, TypeError, ValueError):
        return None
    if not call or freq <= 0:
        return None
    spotter = str(raw.get("spotterRaw") or raw.get("spotter") or "").strip().upper()
    if spotter and "-" not in spotter:
        spotter += "-#"

    def num(key):
        try:
            v = raw.get(key)
            return None if v is None else int(round(float(v)))
        except (TypeError, ValueError):
            return None
    kind = str(raw.get("type") or raw.get("spotType") or "CQ").strip().upper()
    return Spot(
        dxcall=call, freq_khz=freq,
        mode=str(raw.get("mode") or raw.get("txMode") or "CW").strip().upper(),
        snr=num("snr"), wpm=num("speed"), spot_type=kind, spotter=spotter,
        time_utc=time.strftime("%H%MZ", time.gmtime(ts)), received=ts,
    )


class RBNHttpFeed(threading.Thread):
    """Polls the HTTP feed until ``stop()``; spots -> ``out_queue``."""

    def __init__(self, url: str, spotter_filter, out_queue, on_status=None,
                 lookback_s: int = 600, poll_s: float = 5, limit: int = 2000,
                 modes=("CW", "RTTY"), fetch=None, sleep=None, on_raw=None) -> None:
        super().__init__(daemon=True, name="rbn-http")
        self.url = url
        self.spotter_filter = spotter_filter
        self.queue = out_queue
        self.on_status = on_status
        self.lookback_s = max(0, min(3600, int(lookback_s)))
        self.poll_s = poll_s
        self.limit = limit
        self.modes = {m.upper() for m in modes}
        self._fetch = fetch or self._http_get
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        self.cursor = ""
        self.spots_in = 0
        self.on_raw = on_raw           # every spot, before the filter (band-open counter)
        self.last_ok = 0.0

    def stop(self) -> None:
        self._stop.set()

    def _status(self, text: str) -> None:
        if self.on_status is not None:
            try:
                self.on_status(text)
            except Exception:
                log.exception("RBN HTTP status callback failed")

    def _request_url(self) -> str:
        q = {"limit": self.limit}
        if self.cursor:
            q["cursor"] = self.cursor
        else:
            q["lookbackSeconds"] = self.lookback_s
        return self.url + ("&" if "?" in self.url else "?") + urllib.parse.urlencode(q)

    def poll_once(self) -> bool:
        """One request; True if more pages are waiting (ask again at once)."""
        doc = json.loads(self._fetch(self._request_url()).decode("utf-8"))
        if traffic._sink is not None:
            for raw in doc.get("spots") or ():
                traffic.emit("HTTP", traffic.squeeze(json.dumps(raw), 300))
        if doc.get("cursor"):
            self.cursor = str(doc["cursor"])
        for raw in doc.get("spots") or ():
            spot = to_spot(raw) if isinstance(raw, dict) else None
            if spot is not None and self.on_raw is not None:
                try:
                    self.on_raw(spot)
                except Exception:  # noqa: BLE001
                    pass
            if spot is None or (self.modes and spot.mode not in self.modes):
                continue
            acc = getattr(self.spotter_filter, "accept", None)
            if acc is not None and not acc(spot.spotter):
                continue
            try:
                self.queue.put_nowait(spot)
                self.spots_in += 1
            except queue.Full:
                pass
        self.last_ok = time.time()
        return bool(doc.get("hasMore"))

    def run(self) -> None:
        self._status(f"RBN via HTTP backup {urllib.parse.urlsplit(self.url).hostname}")
        backoff = 10
        while not self._stop.is_set():
            try:
                more = self.poll_once()
                backoff = 10
                if more:
                    continue
                self._sleep(self.poll_s)
            except Exception as exc:  # noqa: BLE001 - keep polling
                log.info("RBN HTTP backup: %s", exc)
                self._status(f"RBN HTTP backup error: {exc}")
                self._sleep(backoff)
                backoff = min(backoff * 2, 120)

    @staticmethod
    def _http_get(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "POUNCer"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
