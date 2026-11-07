"""POUNCer as a small DX cluster: N1MM's Telnet window (or any cluster
client) connects to it and gets the spots POUNCer collects.

``[cluster] enabled = true`` (Settings > Cluster server), TCP ``port`` 10000.
A client gets ``login:``, sends its call, and from then on every spot as a
standard ``DX de`` line (DXSpider style). It may send ``sh/dx`` (the last
spots again) and ``bye``; every other command just gets the prompt back -
nothing a client sends changes anything in POUNCer (read-only).

Spots that came **from N1MM** are never served (no loop). And the app asks
``echo(call, freq)`` for every spot N1MM broadcasts: one we served in the
last minutes is our own coming back (N1MM's telnet is connected here) and
is ignored - otherwise POUNCer would take it for N1MM's own SSB spots and
close its SSB cluster, its source.
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from collections import deque

from . import traffic

log = logging.getLogger(__name__)

MAX_CLIENTS = 10
HISTORY = 30            # sh/dx: the last spots again
ECHO_S = 600            # a spot N1MM sends back within this time = our own
DUP_S = 60              # the same call / frequency / spotter again within this: not resent


def dx_line(spotter: str, freq_khz: float, call: str, comment: str, time_utc: str) -> str:
    """One spot in DXSpider's format, e.g.
    ``DX de S53WW-#:    14025.0  DL1ABC       CW 23 dB 28 WPM CQ             1200Z``"""
    hhmm = (time_utc or time.strftime("%H%MZ", time.gmtime())).rstrip("Zz")[:4]
    head = f"DX de {spotter[:9] + ':':<10}"
    return f"{head}{freq_khz:>9.1f}  {call[:12]:<12} {comment[:30]:<30} {hhmm}Z"


def spot_comment(spot) -> str:
    """'CW 23 dB 28 WPM CQ' from a Spot's fields (what RBN's lines carry)."""
    parts = [spot.mode or ""]
    if spot.snr is not None:
        parts.append(f"{spot.snr} dB")
    if spot.wpm is not None:
        parts.append(f"{spot.wpm} WPM")
    if spot.spot_type:
        parts.append(spot.spot_type)
    return " ".join(p for p in parts if p)


def strip_telnet(data: bytes) -> bytes:
    """Drop telnet option negotiation (IAC ...) from what a client sends."""
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 255:                       # IAC
            if i + 1 < len(data) and data[i + 1] in (251, 252, 253, 254):
                i += 3                     # WILL / WONT / DO / DONT + option
            else:
                i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


class _Client:
    def __init__(self, sock: socket.socket, addr) -> None:
        self.sock = sock
        self.addr = addr
        self.call = ""
        self.lock = threading.Lock()

    def send(self, text: str) -> bool:
        try:
            with self.lock:
                self.sock.sendall(text.encode("ascii", "replace"))
            return True
        except OSError:
            return False


class ClusterServer:
    def __init__(self, port: int = 10000, host: str = "0.0.0.0", node: str = "POUNCER",
                 version: str = "") -> None:
        self.port, self.host = int(port), host
        self.node = node
        self.version = version
        self.clients: list[_Client] = []
        self._lock = threading.Lock()
        self._history: deque[str] = deque(maxlen=HISTORY)
        self._served: dict[tuple[str, int], float] = {}     # (call, freq*10) -> when
        self._dups: dict[tuple[str, int, str], float] = {}
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self.error = ""
        self.sent = 0

    # ---- start / stop -----------------------------------------------------------
    def start(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32":     # Windows: no second server on the same port
                s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 0) or
                             socket.SO_REUSEADDR, 1)
            else:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(5)
            s.settimeout(1.0)
        except OSError as exc:
            s.close()
            self.error = f"TCP {self.port} busy? {exc}"
            log.warning("cluster server: %s", self.error)
            return False
        self._sock = s
        threading.Thread(target=self._accept_loop, name="cluster-server", daemon=True).start()
        log.info("cluster server listening on %s:%d", self.host, self.port)
        traffic.emit("server", f"listening on TCP {self.port}")
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        with self._lock:
            clients, self.clients = self.clients, []
        for c in clients:
            try:
                c.sock.close()
            except OSError:
                pass

    def client_count(self) -> int:
        with self._lock:
            return sum(1 for c in self.clients if c.call)

    # ---- clients ------------------------------------------------------------------
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                full = len(self.clients) >= MAX_CLIENTS
            if full:
                try:
                    sock.sendall(b"Sorry, too many connections.\r\n")
                    sock.close()
                except OSError:
                    pass
                continue
            threading.Thread(target=self._serve, args=(sock, addr), name="cluster-client",
                             daemon=True).start()

    def _prompt(self, c: _Client) -> str:
        return f"{c.call or 'N0CALL'} de {self.node} >\r\n"

    def _serve(self, sock: socket.socket, addr) -> None:
        c = _Client(sock, addr)
        sock.settimeout(None)
        with self._lock:
            self.clients.append(c)
        log.info("cluster server: %s:%s connected", *addr)
        try:
            c.send(f"POUNCer {self.version} DX cluster\r\n\r\nlogin: ")
            buf = b""
            while not self._stop.is_set():
                data = sock.recv(1024)
                if not data:
                    break
                buf += strip_telnet(data)
                while b"\n" in buf or b"\r" in buf:
                    cut = min(i for i in (buf.find(b"\n"), buf.find(b"\r")) if i >= 0)
                    line = buf[:cut].decode("ascii", "replace").strip()
                    buf = buf[cut + 1:]
                    if not line:
                        continue
                    if not self._command(c, line):
                        return
                if len(buf) > 4096:
                    buf = b""
        except OSError:
            pass
        finally:
            with self._lock:
                if c in self.clients:
                    self.clients.remove(c)
            try:
                sock.close()
            except OSError:
                pass
            log.info("cluster server: %s (%s) left", c.call or "?", addr[0])
            traffic.emit("server", f"{c.call or '?'} {addr[0]} left")

    def _command(self, c: _Client, line: str) -> bool:
        """One line from a client; False = close the connection."""
        if not c.call:
            c.call = line.split()[0].upper()[:12]
            traffic.emit("server", f"{c.call} {c.addr[0]} logged in")
            log.info("cluster server: %s logged in from %s", c.call, c.addr[0])
            c.send(f"Hello {c.call}, this is {self.node} (POUNCer {self.version}) - spots from "
                   f"POUNCer's sources.\r\n{self._prompt(c)}")
            return True
        cmd = line.lower()
        traffic.emit("server", f"{c.call}: {line[:60]}")
        if cmd in ("bye", "quit", "q", "b", "exit"):
            c.send("73\r\n")
            return False
        if cmd.startswith(("sh/dx", "show/dx", "sh dx")):
            with self._lock:
                last = list(self._history)
            c.send("".join(reversed([f"{x}\r\n" for x in last])) + self._prompt(c))
            return True
        c.send(self._prompt(c))         # set/..., filters, ...: accepted, nothing changes
        return True

    # ---- spots ----------------------------------------------------------------------
    def publish(self, spot) -> bool:
        """Send a spot to every logged-in client (any thread). Never one from
        N1MM; the same call / frequency / spotter once a minute."""
        if getattr(spot, "source", "") == "n1mm" or self._sock is None:
            return False
        now = time.time()
        key = (spot.dxcall.upper(), int(round(spot.freq_khz * 10)))
        dup = key + (spot.spotter.upper(),)
        with self._lock:
            if now - self._dups.get(dup, 0.0) < DUP_S:
                return False
            self._dups[dup] = now
            self._served[key] = now
            if len(self._served) > 5000:
                for k in [k for k, t in self._served.items() if now - t > ECHO_S]:
                    del self._served[k]
                for k in [k for k, t in self._dups.items() if now - t > DUP_S]:
                    del self._dups[k]
            line = dx_line(spot.spotter, spot.freq_khz, spot.dxcall, spot_comment(spot),
                           spot.time_utc)
            self._history.append(line)
            clients = [c for c in self.clients if c.call]
        gone = [c for c in clients if not c.send(line + "\r\n")]
        if gone:
            with self._lock:
                for c in gone:
                    if c in self.clients:
                        self.clients.remove(c)
        self.sent += 1
        return True

    def echo(self, call: str, freq_khz: float, tolerance_khz: float = 0.3) -> bool:
        """Is this (from N1MM's broadcast) a spot we served lately - our own
        coming back through N1MM's telnet?"""
        now = time.time()
        c = call.upper()
        f10 = int(round(freq_khz * 10))
        tol = int(round(tolerance_khz * 10))
        with self._lock:
            for df in range(-tol, tol + 1):
                t = self._served.get((c, f10 + df))
                if t is not None and now - t < ECHO_S:
                    return True
        return False
