"""Reverse Beacon Network telnet client and spot parser (standard library only)."""

from __future__ import annotations

from . import traffic

import logging
import calendar
import queue
import re
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


_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
_HIST_RE = re.compile(
    r"(?:^|\s)(\d+\.\d+)\s+(\S+)\s+(\d{1,2})-([A-Za-z]{3})-(\d{4})\s+(\d{4})Z\s+(.*?)\s*<([^>]+)>\s*$")


def parse_history(line: str, now: float | None = None) -> Spot | None:
    """One line of the cluster's SH/DX answer (the last spots, sent once
    after login), e.g.
    ``  14025.0  K1ABC    13-Oct-2026 1159Z  CW 23 dB 28 WPM CQ   <S53WW-#>``.
    ``received`` is the spot's own time, so old ones fade / expire as usual."""
    # search, not match: a prompt without a newline can stand in front
    m = _HIST_RE.search(line.rstrip("\r\n"))
    if not m:
        return None
    freq_s, call, day, mon, year, hhmm, comment, spotter = m.groups()
    month = _MONTHS.get(mon.upper())
    if not month:
        return None
    try:
        t = calendar.timegm((int(year), month, int(day), int(hhmm[:2]), int(hhmm[2:]), 0))
        freq = float(freq_s)
    except (ValueError, OverflowError):
        return None
    now = time.time() if now is None else now
    rest = comment.split()
    mode = rest[0] if rest else ""
    snr = wpm = None
    for idx, tok in enumerate(rest):
        if tok == "dB" and idx:
            snr = _to_int(rest[idx - 1])
        elif tok == "WPM" and idx:
            wpm = _to_int(rest[idx - 1])
    spot_type = next((k for k in ("CQ", "NCDXF", "BEACON") if k in rest), "")
    return Spot(dxcall=call, freq_khz=freq, mode=mode, snr=snr, wpm=wpm,
                spot_type=spot_type, spotter=spotter, time_utc=hhmm + "Z",
                received=min(float(t), now))


# Phone (SSB) segments, kHz (IARU Region 1 band plan, contest practice).
SSB_SEGMENTS = (
    (1840, 2000), (3600, 3800), (7060, 7200), (14100, 14350),
    (18110, 18168), (21150, 21450), (24930, 24990), (28300, 29000), (50100, 50500),
)
_MODE_WORDS = {"SSB": "SSB", "USB": "SSB", "LSB": "SSB", "PH": "SSB", "PHONE": "SSB",
               "AM": "SSB", "FM": "SSB",
               "CW": "CW", "RTTY": "RTTY", "FT8": "FT8", "FT4": "FT4"}


def is_phone_freq(khz: float) -> bool:
    return any(lo <= khz <= hi for lo, hi in SSB_SEGMENTS)


def guess_mode(freq_khz: float, comment: str = "") -> str:
    """Mode of a human cluster spot: a mode word in the comment, else the
    band plan (phone segment = SSB, below it = CW)."""
    for word in comment.upper().replace(",", " ").split():
        if word in _MODE_WORDS:
            return _MODE_WORDS[word]
    if is_phone_freq(freq_khz):
        return "SSB"
    return "CW"


def parse_cluster_spot(line: str) -> Spot | None:
    """A human DX-cluster spot, e.g.
    ``DX de S50ABC:   14205.0  K1ABC   59 tnx QSO   1200Z JN76``
    (no mode column - guessed from the comment / band plan)."""
    line = line.rstrip("\r\n")
    if not line.startswith("DX de"):
        return None
    head, _, rest = line[5:].partition(":")
    spotter = head.strip()
    tok = rest.split()
    if not spotter or len(tok) < 2:
        return None
    try:
        freq = float(tok[0])
    except ValueError:
        return None
    dxcall = tok[1]
    comment = tok[2:]
    time_utc = ""
    for idx in range(len(comment) - 1, -1, -1):
        w = comment[idx]
        if len(w) == 5 and w.endswith("Z") and w[:4].isdigit():
            time_utc = w
            comment = comment[:idx]
            break
    text = " ".join(comment)
    return Spot(dxcall=dxcall, freq_khz=freq, mode=guess_mode(freq, text), snr=None,
                wpm=None, spot_type="", spotter=spotter, time_utc=time_utc,
                received=time.time())


def spot_from_n1mm(pkt: dict, now: float | None = None) -> Spot:
    """A Spot from n1mm.parse_spot_packet: N1MM's mode (USB / LSB = SSB), else
    a mode word in the comment / the band plan; dB / WPM / CQ from a
    skimmer's comment ("CW 22 dB 28 WPM CQ")."""
    rest = pkt.get("comment", "").split()
    snr = wpm = None
    for idx, tok in enumerate(rest):
        if tok == "dB" and idx:
            snr = _to_int(rest[idx - 1])
        elif tok == "WPM" and idx:
            wpm = _to_int(rest[idx - 1])
    m = _MODE_WORDS.get((pkt.get("mode") or "").upper(), "")
    mode = m or guess_mode(pkt["freq_khz"], pkt.get("comment", ""))
    spot_type = next((k for k in ("CQ", "NCDXF", "BEACON") if k in rest), "")
    return Spot(dxcall=pkt["call"], freq_khz=float(pkt["freq_khz"]), mode=mode, snr=snr,
                wpm=wpm, spot_type=spot_type, spotter=pkt.get("spotter", ""), time_utc="",
                received=time.time() if now is None else now)


class ContinentFilter:
    """Human cluster spots: keep spotters on our continent (or all)."""

    def __init__(self, continent_of=None, home_cont=None, wanted: str = "EU") -> None:
        self.continent_of = continent_of
        self.home_cont = home_cont or (lambda: "EU")
        self.wanted = (wanted or "").strip().upper()
        self.primary = ""

    def accept(self, spotter: str) -> bool:
        if self.wanted in ("", "ALL", "*"):
            return True
        if self.continent_of is None:
            return True
        base = (spotter or "").upper().split("-")[0]
        want = self.home_cont() if self.wanted in ("HOME", "OWN") else self.wanted
        return self.continent_of(base) == want


class SpotterFilter:
    """Which skimmers' spots to keep: the chosen one (S53WW) and, while it has
    been quiet for ``quiet_s`` (offline?), backup skimmers - the listed
    ``backups`` (call prefixes) or, if none are listed, every skimmer on our
    own continent. Switches back as soon as the chosen one spots again."""

    def __init__(self, primary: str, backups=(), continent_of=None, home_cont=None,
                 quiet_s: float = 300, clock=time.time) -> None:
        self.primary = (primary or "").upper()
        self.backups = tuple(b.strip().upper() for b in backups if b.strip())
        self.continent_of = continent_of
        self.home_cont = home_cont or (lambda: "EU")
        self.quiet_s = quiet_s
        self.clock = clock
        self.last_primary = clock()  # start: give the chosen one time to speak

    def backup_active(self) -> bool:
        return bool(self.primary) and self.clock() - self.last_primary > self.quiet_s

    def accept(self, spotter: str) -> bool:
        sp = (spotter or "").upper()
        if not self.primary:
            return True
        if sp.startswith(self.primary):
            self.last_primary = self.clock()
            return True
        if not self.backup_active():
            return False
        if self.backups:
            return any(sp.startswith(b) for b in self.backups)
        if self.continent_of is None:
            return False
        base = sp.split("-")[0]
        return self.continent_of(base) == self.home_cont()


class _BackToPrimary(Exception):
    """Backup has run long enough: try RBN's own telnet again."""


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
        history: int = 30,
        backup: tuple[str, int] | None = None,
        backup_commands: Iterable[str] = (),
        silent_s: float = 120,
        retry_primary_s: float = 600,
        kind: str = "rbn",
        modes: Iterable[str] = (),
        name: str = "rbn",
        on_raw=None,
    ) -> None:
        super().__init__(daemon=True, name="rbn")
        # every live spot before the skimmer filter (band-open counter)
        self.on_raw = on_raw
        # Backup telnet server (a DX cluster passing on the RBN skimmer
        # spots, e.g. azure.s53m.com:8000): used when RBN's own telnet
        # fails twice in a row or sends nothing for ``silent_s``; after
        # ``retry_primary_s`` RBN is tried again.
        self.backup = backup if backup and backup[0] else None
        self.backup_commands = tuple(c for c in backup_commands if c.strip())
        self.silent_s = silent_s
        self.retry_primary_s = retry_primary_s
        self.on_backup = False
        self._last_dx = time.monotonic()
        self._login_at = 0.0
        # "cluster": human DX-cluster spots (SSB); only ``modes`` are kept
        self.kind = kind
        self.modes = {m.upper() for m in modes}
        self.name = name
        # after login ask for the last spots (SH/DX), so the list isn't empty
        self.history = max(0, int(history or 0))
        self.host = host
        self.port = port
        self.callsign = callsign
        # a prefix string (S53WW) or a SpotterFilter (with backup skimmers)
        self.spotter_filter = (
            spotter_filter if hasattr(spotter_filter, "accept")
            else (spotter_filter or "").upper()
        )
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
        fails = 0               # RBN failures in a row
        while not self._stop.is_set():
            back_to_rbn = False
            try:
                if self.on_backup:
                    self._connect_and_read(*self.backup, backup=True)
                else:
                    self._connect_and_read(self.host, self.port)
                backoff = 2
            except _BackToPrimary:
                back_to_rbn = True
            except Exception as exc:  # noqa: BLE001 - keep the thread alive
                if not self._stop.is_set():
                    log.warning("RBN connection error (%s): %s",
                                "backup" if self.on_backup else "RBN", exc)
            if self._stop.is_set():
                break
            if self.on_backup:
                # time to try RBN again, or the backup failed too
                self.on_backup = False
                fails = 0
                self._status("trying RBN again")
                self._stop.wait(1)
                continue
            fails += 1
            if self.backup and fails >= 2:
                self.on_backup = True
                self._status(f"RBN down - switching to backup {self.backup[0]}:{self.backup[1]}")
                self._stop.wait(1)
                continue
            self._status("disconnected, reconnecting")
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _connect_and_read(self, host: str | None = None, port: int | None = None,
                          backup: bool = False) -> None:
        host, port = host or self.host, port or self.port
        self._status(f"connecting to {host}:{port}" + (" (backup)" if backup else ""))
        sock = socket.create_connection((host, port), timeout=30)
        sock.settimeout(1.0)   # wake up every second (login / SH/DX timing)
        self._sock = sock
        telnet = TelnetFilter()
        buf = ""
        logged_in = False
        login_deadline = time.monotonic() + 3.0
        self._history_lines = 0
        hist_at = None          # when to ask for the last spots
        hist_step = 0           # 0: filtered on our skimmer, 1: plain, 2: done
        started = time.monotonic()
        self._last_dx = started
        cmds_sent = not (backup or self.kind == "cluster")
        while not self._stop.is_set():
            now = time.monotonic()
            if backup and now - started > self.retry_primary_s:
                raise _BackToPrimary()
            if not backup and self.backup and logged_in and now - self._last_dx > self.silent_s:
                raise ConnectionError(f"RBN sent no spots for {int(self.silent_s)} s")
            if logged_in and not cmds_sent and now > self._login_at + 1:
                for cmd in self.backup_commands:     # e.g. set/skimmer
                    try:
                        sock.sendall((cmd.strip() + "\r\n").encode("ascii", "ignore"))
                    except OSError:
                        pass
                cmds_sent = True
            if logged_in and self.history and hist_step < 2:
                now = time.monotonic()
                if hist_at is None:
                    hist_at = now + 2.0
                elif now >= hist_at:
                    if hist_step == 0 or not self._history_lines:
                        self._ask_history(sock, filtered=(hist_step == 0))
                    hist_step += 1
                    hist_at = now + 5.0   # no answer to the filtered one: plain
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                if not logged_in and time.monotonic() > login_deadline:
                    self._send_login(sock)
                    logged_in = True
                continue
            if not chunk:
                raise ConnectionError("the server closed the connection")
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
            if traffic._sink is not None:            # console open: show the raw line
                traffic.emit(self.name or self.kind, line.rstrip("\r"))
            at = line.find("DX de ")
            if at > 0:            # a prompt without a newline in front of it
                line = line[at:]
            if line.startswith("DX de"):
                self._last_dx = time.monotonic()   # the feed is alive
            if self.kind == "cluster":
                spot = parse_cluster_spot(line)
            else:
                spot = parse_spot(line)
            if spot is not None and self.on_raw is not None:
                try:
                    self.on_raw(spot)
                except Exception:  # noqa: BLE001 - never stop the feed
                    pass
            if spot is None:
                spot = parse_history(line)
                if spot is None:
                    continue
                self._history_lines += 1
                if self.kind == "cluster":
                    spot.mode = guess_mode(spot.freq_khz, spot.mode)
            if self.modes and spot.mode.upper() not in self.modes:
                continue
            if hasattr(self.spotter_filter, "accept"):
                if not self.spotter_filter.accept(spot.spotter):
                    continue
            elif self.spotter_filter and not spot.spotter.upper().startswith(self.spotter_filter):
                continue
            try:
                self.queue.put_nowait(spot)
            except queue.Full:
                pass
        return buf

    def _primary(self) -> str:
        f = self.spotter_filter
        return (getattr(f, "primary", f) or "").upper()

    def _ask_history(self, sock: socket.socket, filtered: bool) -> None:
        """SH/DX: the last spots - first only our skimmer's (AR-Cluster
        ``spotter=``), then, if that gave nothing, the plain last N."""
        primary = self._primary()
        if filtered and primary:
            spotter = primary if "-" in primary else primary + "-#"
            cmd = f"sh/dx/{max(self.history, 100)} spotter={spotter}"
        else:
            cmd = f"sh/dx/{self.history}"
        try:
            sock.sendall((cmd + "\r\n").encode("ascii", "ignore"))
            traffic.emit(f"{self.name or self.kind} >", cmd)
            log.info("RBN: %s", cmd)
        except OSError as exc:
            log.warning("RBN %s failed: %s", cmd, exc)

    def _send_login(self, sock: socket.socket) -> None:
        self._login_at = time.monotonic()
        if not self.callsign:
            self._status("no callsign: set [rbn] callsign in config.ini")
            return
        try:
            sock.sendall((self.callsign + "\r\n").encode("ascii", "ignore"))
            traffic.emit(f"{self.name or self.kind} >", self.callsign)
            self._status(f"logged in as {self.callsign}" +
                         (f" (backup {self.backup[0]})" if self.on_backup and self.backup else ""))
        except OSError as exc:
            log.warning("failed to send RBN login: %s", exc)


class OnFreqTracker:
    """How long each station has been calling on one frequency (from RBN).

    Same call and band, re-spotted within ``stay_khz`` of where it was and
    within ``gap_s`` of the last spot = still there; otherwise it moved or
    came back = a new start.
    """

    def __init__(self, stay_khz: float = 0.5, gap_s: float = 600) -> None:
        self.stay_khz = stay_khz
        self.gap_s = gap_s
        # (call, band) -> [freq kHz, first heard there, last heard there]
        self._d: dict[tuple[str, str], list[float]] = {}

    def add(self, spot: "Spot", band: str | None) -> None:
        if not band or is_beacon(spot):
            return
        key = (spot.dxcall.upper(), band)
        now = spot.received
        e = self._d.get(key)
        if e and abs(e[0] - spot.freq_khz) <= self.stay_khz and now - e[2] <= self.gap_s:
            e[0], e[2] = spot.freq_khz, now
        else:
            self._d[key] = [spot.freq_khz, now, now]

    def minutes(self, call: str, band: str | None, freq_khz: float, now: float) -> int | None:
        """Minutes on this frequency (None: not tracked / it moved away)."""
        e = self._d.get((call.upper(), band))
        if not e or abs(e[0] - freq_khz) > self.stay_khz:
            return None
        return int((now - e[1]) // 60)

    def first_heard(self, call: str, band: str | None, freq_khz: float) -> float | None:
        """When it was first heard on this frequency (epoch), None if not tracked."""
        e = self._d.get((call.upper(), band))
        if not e or abs(e[0] - freq_khz) > self.stay_khz:
            return None
        return e[1]

    def prune(self, cutoff: float) -> None:
        for key in [k for k, e in self._d.items() if e[2] < cutoff]:
            del self._d[key]

