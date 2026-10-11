"""Reverse Beacon Network telnet client and spot parser (standard library only)."""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger(__name__)

# Spot modes we can follow, in display order.
MODES = ("CW", "RTTY", "FT8", "FT4")
# RBN serves CW/RTTY on one port and FT8/FT4 on another.
_CW_PORT_MODES = {"CW", "RTTY"}
_FT8_PORT_MODES = {"FT8", "FT4"}

IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240


class TelnetFilter:
    """Strips telnet control sequences and answers option negotiation."""

    def __init__(self) -> None:
        self._state = 0
        self._verb = 0

    def feed(self, data: bytes) -> tuple[bytes, bytes]:
        out = bytearray()
        reply = bytearray()
        for b in data:
            if self._state == 0:
                if b == IAC:
                    self._state = 1
                else:
                    out.append(b)
            elif self._state == 1:
                if b == IAC:
                    out.append(IAC)
                    self._state = 0
                elif b in (WILL, WONT, DO, DONT):
                    self._verb = b
                    self._state = 2
                elif b == SB:
                    self._state = 3
                else:
                    self._state = 0
            elif self._state == 2:
                if self._verb == WILL:
                    reply += bytes((IAC, DONT, b))
                elif self._verb == DO:
                    reply += bytes((IAC, WONT, b))
                self._state = 0
            elif self._state == 3:
                if b == IAC:
                    self._state = 4
            elif self._state == 4:
                self._state = 0 if b == SE else 3
        return bytes(out), bytes(reply)


@dataclass(slots=True)
class Spot:
    dxcall: str
    freq_khz: float
    mode: str
    snr: int | None
    wpm: int | None
    spot_type: str
    spotter: str
    time_utc: str
    received: float

    @property
    def key(self) -> tuple[str, int]:
        return (self.dxcall.upper(), int(round(self.freq_khz * 10)))


def ports_for_modes(
    modes: Iterable[str], cw_port: int, ft8_port: int
) -> set[int]:
    """Return the RBN ports needed to follow the given modes."""
    wanted = {m.strip().upper() for m in modes}
    ports: set[int] = set()
    if wanted & _CW_PORT_MODES:
        ports.add(cw_port)
    if wanted & _FT8_PORT_MODES:
        ports.add(ft8_port)
    return ports


def _to_int(token: str) -> int | None:
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


def is_beacon(spot: Spot) -> bool:
    """RBN beacon spot: type BEACON / NCDXF (IBP), or a ".../B" callsign."""
    return (
        spot.spot_type in ("BEACON", "NCDXF")
        or spot.dxcall.upper().endswith("/B")
    )


def parse_spot(line: str) -> Spot | None:
    """Parse one AR-Cluster style RBN spot line, or return None."""
    line = line.rstrip("\r\n")
    if not line.startswith("DX de"):
        return None
    tok = line.split()
    if len(tok) < 6:
        return None
    spotter = tok[2].rstrip(":")
    try:
        freq = float(tok[3])
    except ValueError:
        return None
    dxcall = tok[4]
    mode = tok[5]
    rest = tok[6:]

    snr = None
    wpm = None
    for idx, tok_s in enumerate(rest):
        if tok_s == "dB" and idx:
            snr = _to_int(rest[idx - 1])
        elif tok_s == "WPM" and idx:
            wpm = _to_int(rest[idx - 1])

    spot_type = ""
    for marker in ("CQ", "NCDXF", "BEACON"):
        if marker in rest:
            spot_type = marker
            break

    time_utc = rest[-1] if rest and rest[-1].endswith("Z") else ""

    return Spot(
        dxcall=dxcall,
        freq_khz=freq,
        mode=mode,
        snr=snr,
        wpm=wpm,
        spot_type=spot_type,
        spotter=spotter,
        time_utc=time_utc,
        received=time.time(),
    )


class RBNClient(threading.Thread):
    """Connects to the RBN telnet feed and pushes matching spots to a queue."""

    def __init__(
        self,
        host: str,
        port: int,
        callsign: str,
        spotter_filter: str,
        out_queue: "queue.Queue[Spot]",
        on_status=None,
    ) -> None:
        super().__init__(daemon=True, name="rbn")
        self.host = host
        self.port = port
        self.callsign = callsign
        self.spotter_filter = (spotter_filter or "").upper()
        self.queue = out_queue
        self.on_status = on_status
        self._stop = threading.Event()
        self._sock: socket.socket | None = None

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _status(self, text: str) -> None:
        if self.on_status is not None:
            try:
                self.on_status(text)
            except Exception:
                log.exception("RBN status callback failed")

    def run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                self._connect_and_read()
                backoff = 2
            except Exception as exc:  # noqa: BLE001 - keep the thread alive
                if not self._stop.is_set():
                    log.warning("RBN connection error: %s", exc)
            if self._stop.is_set():
                break
            self._status("disconnected, reconnecting")
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _connect_and_read(self) -> None:
        self._status(f"connecting to {self.host}:{self.port}")
        sock = socket.create_connection((self.host, self.port), timeout=30)
        sock.settimeout(30)
        self._sock = sock
        telnet = TelnetFilter()
        buf = ""
        logged_in = False
        login_deadline = time.monotonic() + 3.0
        while not self._stop.is_set():
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                if not logged_in:
                    self._send_login(sock)
                    logged_in = True
                continue
            if not chunk:
                raise ConnectionError("RBN closed the connection")
            data, reply = telnet.feed(chunk)
            if reply:
                try:
                    sock.sendall(reply)
                except OSError:
                    pass
            if data:
                buf = self._consume(data.decode("latin-1"), buf)
                if not logged_in and "call" in buf.lower():
                    self._send_login(sock)
                    logged_in = True
            if not logged_in and time.monotonic() > login_deadline:
                self._send_login(sock)
                logged_in = True

    def _consume(self, text: str, buf: str) -> str:
        buf += text
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            spot = parse_spot(line)
            if spot is None:
                continue
            if self.spotter_filter and not spot.spotter.upper().startswith(self.spotter_filter):
                continue
            try:
                self.queue.put_nowait(spot)
            except queue.Full:
                pass
        return buf

    def _send_login(self, sock: socket.socket) -> None:
        if not self.callsign:
            self._status("no callsign: set [rbn] callsign in config.ini")
            return
        try:
            sock.sendall((self.callsign + "\r\n").encode("ascii", "ignore"))
            self._status(f"logged in as {self.callsign}")
        except OSError as exc:
            log.warning("failed to send RBN login: %s", exc)
