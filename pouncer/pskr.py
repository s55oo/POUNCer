"""Is that station on FT8 / FT4? Ask PSK Reporter about ONE call.

A spot in an FT8 / FT4 window inside an SSB segment (7074 - 7077 on 40 m,
1840 - 1843, 50313 - 50326) without a mode word may be FT8 or a contester on
SSB. ``PskChecker.ask(call)`` subscribes to that call's reports on PSK
Reporter's MQTT feed (``pskr/filter/v2/+/+/<CALL>/#`` - only that station
transmitting, a few small messages, never the whole feed) for ``watch_s``;
``digital(call, khz)`` then says "FT8" / "FT4" if a report of him came within
3 kHz of the spot, else "". The connection (mqtt.pskreporter.info:1883, no
login) is open only while a call is watched. A minimal MQTT 3.1.1 client
(QoS 0) - no extra library. Errors: nothing is confirmed, the spot stays SSB.
"""

from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
import uuid

from . import traffic

log = logging.getLogger(__name__)

HOST = "mqtt.pskreporter.info"
PORT = 1883
WATCH_S = 120          # how long one call is watched
KEEP_S = 300           # reports kept this long
MAX_CALLS = 8          # at most this many calls watched at once
NEAR_KHZ = 3.5


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("!H", len(b)) + b


def connect_packet(client_id: str, keepalive: int = 60) -> bytes:
    var = _str("MQTT") + bytes([4, 0x02]) + struct.pack("!H", keepalive)
    body = var + _str(client_id)
    return bytes([0x10]) + _varint(len(body)) + body


def subscribe_packet(pid: int, topic: str) -> bytes:
    body = struct.pack("!H", pid) + _str(topic) + bytes([0])
    return bytes([0x82]) + _varint(len(body)) + body


def unsubscribe_packet(pid: int, topic: str) -> bytes:
    body = struct.pack("!H", pid) + _str(topic)
    return bytes([0xA2]) + _varint(len(body)) + body


def topic_for(call: str) -> str | None:
    c = (call or "").strip().upper()
    if not c or any(ch in c for ch in "/+#"):     # a portable call can't be a topic level
        return None
    return f"pskr/filter/v2/+/+/{c}/#"


def parse_publish(first: int, body: bytes) -> tuple[str, bytes] | None:
    """(topic, payload) of a PUBLISH (QoS 0 / 1)."""
    if first >> 4 != 3 or len(body) < 2:
        return None
    n = struct.unpack("!H", body[:2])[0]
    topic = body[2:2 + n].decode("utf-8", "replace")
    rest = body[2 + n:]
    if (first >> 1) & 3:          # QoS > 0: a packet id first
        rest = rest[2:]
    return topic, rest


def report_from(payload: bytes) -> dict | None:
    """PSK Reporter JSON -> {call, khz, mode, t}."""
    try:
        d = json.loads(payload.decode("utf-8"))
        return {"call": str(d["sc"]).upper(), "khz": float(d["f"]) / 1000.0,
                "mode": str(d.get("md", "")).upper(), "t": time.time()}
    except (ValueError, KeyError, TypeError):
        return None


class PskChecker:
    def __init__(self, host: str = HOST, port: int = PORT, watch_s: float = WATCH_S,
                 clock=time.time, connector=None) -> None:
        self.host, self.port, self.watch_s = host, port, watch_s
        self.clock = clock
        self._connector = connector or (lambda: socket.create_connection((host, port), 10))
        self._lock = threading.Lock()
        self._want: dict[str, float] = {}      # call -> watch until
        self._near: dict[str, list[float]] = {}  # call -> spot kHz (stop early once confirmed)
        self._reports: dict[str, list[dict]] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.state = "idle"                      # idle / connected / error

    # ---- what the app calls ------------------------------------------------
    def ask(self, call: str, khz: float | None = None) -> None:
        """Watch this call's PSK Reporter reports for a while (any thread);
        stops as soon as an FT8 / FT4 report near ``khz`` came."""
        c = (call or "").upper()
        if not topic_for(c):
            return
        with self._lock:
            now = self.clock()
            if khz is not None:
                self._near.setdefault(c, []).append(float(khz))
                del self._near[c][:-4]
            if c in self._want and self._want[c] > now:
                return
            if len([1 for t in self._want.values() if t > now]) >= MAX_CALLS:
                return
            self._want[c] = now + self.watch_s
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name="pskr", daemon=True)
                self._thread.start()

    def digital(self, call: str, khz: float) -> str:
        """'FT8' / 'FT4' if PSK Reporter reported him near ``khz`` lately."""
        now = self.clock()
        with self._lock:
            reps = list(self._reports.get((call or "").upper(), ()))
        for r in reversed(reps):
            if now - r["t"] <= KEEP_S and abs(r["khz"] - float(khz)) <= NEAR_KHZ \
                    and r["mode"] in ("FT8", "FT4"):
                return r["mode"]
        return ""

    def add_report(self, r: dict) -> None:
        with self._lock:
            lst = self._reports.setdefault(r["call"], [])
            lst.append(r)
            del lst[:-20]
            # confirmed FT8 / FT4 near a spot of his: stop watching him now
            if r["mode"] in ("FT8", "FT4") and any(
                    abs(r["khz"] - k) <= NEAR_KHZ for k in self._near.get(r["call"], ())):
                self._want.pop(r["call"], None)
                self._near.pop(r["call"], None)

    def stop(self) -> None:
        self._stop.set()

    # ---- the connection ----------------------------------------------------
    def _run(self) -> None:
        try:
            sock = self._connector()
        except OSError as exc:
            log.info("PSK Reporter: no connection (%s)", exc)
            self.state = "error"
            with self._lock:
                self._want.clear()
            return
        subscribed: dict[str, int] = {}
        pid = 0
        try:
            sock.settimeout(1.0)
            sock.sendall(connect_packet("pouncer-" + uuid.uuid4().hex[:8]))
            traffic.emit("PSKR >", f"connect {self.host}:{self.port}")
            self.state = "connected"
            buf = b""
            last_ping = self.clock()
            while not self._stop.is_set():
                now = self.clock()
                with self._lock:
                    want = {c for c, t in self._want.items() if t > now}
                    for c in [c for c, t in self._want.items() if t <= now]:
                        del self._want[c]
                for c in sorted(want - set(subscribed)):
                    pid = pid % 65000 + 1
                    sock.sendall(subscribe_packet(pid, topic_for(c)))
                    subscribed[c] = pid
                    traffic.emit("PSKR >", f"subscribe {topic_for(c)}")
                for c in sorted(set(subscribed) - want):
                    pid = pid % 65000 + 1
                    sock.sendall(unsubscribe_packet(pid, topic_for(c)))
                    del subscribed[c]
                    traffic.emit("PSKR >", f"unsubscribe {c}")
                if not subscribed:
                    break                                  # nothing to watch: hang up
                if now - last_ping > 30:
                    sock.sendall(b"\xc0\x00")
                    last_ping = now
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                buf = self._packets(buf)
        except OSError as exc:
            log.info("PSK Reporter connection: %s", exc)
            self.state = "error"
        finally:
            try:
                sock.sendall(b"\xe0\x00")                  # DISCONNECT
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
            if self.state == "connected":
                self.state = "idle"

    def _packets(self, buf: bytes) -> bytes:
        """Handle every whole packet in ``buf``; return the rest."""
        while len(buf) >= 2:
            first, n, mult, i = buf[0], 0, 1, 1
            while True:
                if i >= len(buf):
                    return buf
                b = buf[i]
                n += (b & 0x7F) * mult
                mult *= 128
                i += 1
                if not b & 0x80:
                    break
            if len(buf) < i + n:
                return buf
            body, buf = buf[i:i + n], buf[i + n:]
            got = parse_publish(first, body)
            if got:
                r = report_from(got[1])
                if r:
                    self.add_report(r)
                    traffic.emit("PSKR", f"{r['call']} {r['khz']:.1f} {r['mode']}")
        return buf
