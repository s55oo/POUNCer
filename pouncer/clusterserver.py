"""POUNCer as a small DX cluster: N1MM's Telnet window (or any cluster
client) connects to it and gets the spots POUNCer collects.

**Relay** (``upstream`` set, the default): for each client POUNCer opens a
connection to the real cluster (azure.s53m.com:8000), logs in with the
client's call and passes everything through both ways - the cluster's
spots, announcements, its prompts, and the client's commands (``DX ...``
spots, filters, ``sh/dx``) - and adds POUNCer's own spots in between, not
the ones the client just got from the cluster. ``on_upstream_line`` gets
every ``DX de`` line from the cluster (POUNCer's SSB source while N1MM is
connected this way). If the cluster can't be reached the client still gets
POUNCer's spots and a note; it is tried again every minute.

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
import re
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


DX_RE = re.compile(r"^DX de\s+(\S+?):?\s+(\d+(?:\.\d+)?)\s+(\S+)\s*(.*?)\s*(?:(\d{4})Z.*)?$")


def parse_dx(line: str) -> dict | None:
    """A cluster's 'DX de' line -> N1MM-like spot packet (call, freq_khz,
    spotter, comment) for the app; None for anything else."""
    m = DX_RE.match(line.strip())
    if not m:
        return None
    try:
        freq = float(m.group(2))
    except ValueError:
        return None
    return {"call": m.group(3).upper(), "freq_khz": freq, "spotter": m.group(1).rstrip(":"),
            "comment": m.group(4).strip(), "action": "add", "mode": ""}


class _Client:
    def __init__(self, sock: socket.socket, addr) -> None:
        self.sock = sock
        self.addr = addr
        self.call = ""
        self.lock = threading.Lock()
        self.up: socket.socket | None = None      # relay: the connection to the cluster
        self.up_ok = False
        self.mid_line = False                     # the last bytes sent ended mid-line
        self.recent: dict[tuple[str, int], float] = {}   # spots it got from the cluster
        self.closed = False

    def send(self, text: str) -> bool:
        return self.send_raw(text.encode("ascii", "replace"))

    def send_raw(self, data: bytes, own_line: bool = False) -> bool:
        """Send; our own line never lands in the middle of a cluster line."""
        try:
            with self.lock:
                if own_line and self.mid_line:
                    data = b"\r\n" + data
                self.sock.sendall(data)
                if not own_line:
                    self.mid_line = not data.endswith((b"\n", b"\r"))
            return True
        except OSError:
            return False


class ClusterServer:
    def __init__(self, port: int = 10000, host: str = "0.0.0.0", node: str = "POUNCER",
                 version: str = "", upstream: tuple[str, int] | None = None,
                 on_upstream_line=None, retry_s: float = 60.0, on_relay_state=None) -> None:
        self.port, self.host = int(port), host
        self.upstream = upstream if upstream and upstream[0] else None
        self.on_upstream_line = on_upstream_line
        self.on_relay_state = on_relay_state   # (host, port, call) / None: one login per node
        self.retry_s = retry_s
        self._relay_calls: dict[str, int] = {}
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

    def relay_connected(self) -> bool:
        """A client is connected through to the cluster right now."""
        with self._lock:
            return any(c.up_ok for c in self.clients)

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
            c.closed = True
            with self._lock:
                if c in self.clients:
                    self.clients.remove(c)
            for s in (sock, c.up):
                if s is not None:
                    try:
                        s.shutdown(socket.SHUT_RDWR)     # wakes the relay's recv at once
                    except OSError:
                        pass
                    try:
                        s.close()
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
            if self.upstream:
                c.send(f"Hello {c.call}, this is {self.node} (POUNCer {self.version}): the "
                       f"cluster {self.upstream[0]}:{self.upstream[1]} plus POUNCer's spots.\r\n")
                threading.Thread(target=self._relay, args=(c,), name="cluster-relay",
                                 daemon=True).start()
            else:
                c.send(f"Hello {c.call}, this is {self.node} (POUNCer {self.version}) - spots "
                       f"from POUNCer's sources.\r\n{self._prompt(c)}")
            return True
        cmd = line.lower()
        traffic.emit("server", f"{c.call}: {line[:60]}")
        if c.up_ok and c.up is not None:            # relay: everything to the cluster
            try:
                c.up.sendall(line.encode("ascii", "replace") + b"\r\n")
            except OSError:
                c.up_ok = False
            return cmd not in ("bye", "quit", "q", "b", "exit")
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

    # ---- relay: the real cluster behind POUNCer -------------------------------------
    def _relay(self, c: _Client) -> None:
        """Connect the client through to the cluster (in the client's call)
        and pass its lines on; again after a lost connection."""
        host, port = self.upstream
        while not c.closed and not self._stop.is_set():
            try:
                up = socket.create_connection((host, port), timeout=15)
                up.settimeout(5.0)
            except OSError as exc:
                c.send_raw(f"*** POUNCer: the cluster {host}:{port} can't be reached ({exc}) - "
                           f"POUNCer's spots only, trying again in "
                           f"{int(self.retry_s)} s\r\n".encode("ascii", "replace"), own_line=True)
                traffic.emit("relay", f"{host}:{port} not reachable: {exc}")
                if self._wait(c, self.retry_s):
                    return
                continue
            c.up = up
            self._relay_state(c.call, +1)        # POUNCer's own login there steps aside
            traffic.emit("relay", f"{c.call} -> {host}:{port}")
            log.info("cluster relay: %s connected to %s:%d", c.call, host, port)
            logged_in, started, buf = False, time.monotonic(), b""
            try:
                while not c.closed and not self._stop.is_set():
                    try:
                        data = up.recv(4096)
                    except socket.timeout:
                        data = None
                    if data == b"":
                        break                                   # the cluster closed it
                    if data:
                        if not logged_in and (b"login" in data.lower() or b"call" in data.lower()):
                            up.sendall(c.call.encode("ascii", "replace") + b"\r\n")
                            logged_in, c.up_ok = True, True
                        buf += strip_telnet(data)
                        buf = self._lines_from_cluster(c, buf)
                        if c.closed:
                            return
                    if not logged_in and time.monotonic() - started > 5:
                        up.sendall(c.call.encode("ascii", "replace") + b"\r\n")   # no prompt seen
                        logged_in, c.up_ok = True, True
            except OSError:
                pass
            finally:
                c.up_ok = False
                try:
                    up.close()
                except OSError:
                    pass
                self._relay_state(c.call, -1)
            if c.closed or self._stop.is_set():
                return
            c.send_raw(f"*** POUNCer: the connection to {host}:{port} was lost - connecting "
                       "again\r\n".encode("ascii"), own_line=True)
            traffic.emit("relay", f"{host}:{port} lost")
            if self._wait(c, 10):
                return

    def _relay_state(self, call: str, step: int) -> None:
        """Tell the app which logins the relay holds on the node (first one
        up / last one down per call)."""
        with self._lock:
            n = self._relay_calls.get(call, 0) + step
            self._relay_calls[call] = max(0, n)
        if self.on_relay_state is None or n not in (0, 1) or (n == 1 and step < 0):
            return
        try:
            self.on_relay_state(self.upstream[0], self.upstream[1], call, n == 1)
        except Exception:  # noqa: BLE001
            log.exception("relay state")

    def _wait(self, c: _Client, seconds: float) -> bool:
        """Sleep, but True at once when the client or the server is gone."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if c.closed or self._stop.is_set():
                return True
            time.sleep(0.5)
        return False

    def _lines_from_cluster(self, c: _Client, buf: bytes) -> bytes:
        """The cluster's data -> the client, line by line: everything passes
        (spots of people, announcements, prompts, answers) except the
        **skimmer** spots ('DX de S53WW-#:') - those the client gets from
        POUNCer, from the source chosen with Spots: RBN | Telnet | HTTP and
        POUNCer's skimmer choice. Every DX line goes to the app; the ones
        passed on are noted (POUNCer doesn't send the same spot again)."""
        out = bytearray()
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            raw += b"\n"
            line = raw.decode("ascii", "replace").strip()
            if not line:
                out += raw
                continue
            traffic.emit("relay", line[:120])
            at = line.find("DX de ")
            if at > 0:          # after a prompt with no newline ("... >DX de ...")
                out += raw[:raw.find(b"DX de ")]
                raw = raw[raw.find(b"DX de "):]
                line = line[at:]
            pkt = parse_dx(line)
            skimmer = pkt is not None and pkt["spotter"].endswith("-#")
            if not skimmer:
                out += raw
            if pkt is None:
                continue
            if not skimmer:
                now = time.time()
                with self._lock:
                    c.recent[(pkt["call"], int(round(pkt["freq_khz"] * 10)))] = now
                    if len(c.recent) > 3000:
                        for k in [k for k, t in c.recent.items() if now - t > 300]:
                            del c.recent[k]
            if self.on_upstream_line is not None:
                try:
                    self.on_upstream_line(line)
                except Exception:  # noqa: BLE001
                    log.exception("relay line")
        # the rest without a newline: a prompt ('login:', 'S55OO de NODE >')
        # goes on now; the start of a DX line waits for its end
        head = buf.lstrip()[:6]
        if buf and not (b"DX de "[:len(head)] == head):
            out += buf
            buf = b""
        if out and not c.send_raw(bytes(out)):
            c.closed = True
        return buf[-4096:]

    def _had_it(self, c: _Client, key: tuple[str, int], now: float) -> bool:
        """The client got this call / frequency (±0.3 kHz) from the cluster
        in the last 5 minutes."""
        call, f10 = key
        return any(now - c.recent.get((call, f10 + d), 0.0) < 300 for d in range(-3, 4))

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
            # not to a client that just got this spot from the cluster itself
            clients = [c for c in self.clients if c.call and not self._had_it(c, key, now)]
        data = (line + "\r\n").encode("ascii", "replace")
        gone = [c for c in clients if not c.send_raw(data, own_line=True)]
        if gone:
            with self._lock:
                for c in gone:
                    if c in self.clients:
                        self.clients.remove(c)
        self.sent += 1
        return True

    def echo(self, call: str, freq_khz: float, tolerance_khz: float = 0.3) -> bool:  # noqa: D401
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
