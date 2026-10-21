"""Pre-contest check: is everything POUNCer needs working?

One line in the main window (and a warning on the phone when something is
wrong): N1MM radio info, the log, the station call, RBN, the country file
(cty), Super Check Partial, the phone page and the PC clock.

``build`` turns plain facts into items {name, state, text}; state is
"ok", "warn", "bad" or "off". ``ClockCheck`` compares the PC clock with a
time server (NTP, UDP 123; a web ``Date`` header only as a fallback) every
30 minutes.
"""

from __future__ import annotations

import email.utils
import logging
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

CLOCK_URL = "https://api.github.com"
CTY_OLD_DAYS = 60       # country files: N1MM can download fresh ones
SCP_OLD_DAYS = 30
CLOCK_OK_S = 5          # contest logs: a few seconds are fine ...
CLOCK_BAD_S = 60        # ... a minute off is not
ORDER = {"bad": 0, "warn": 1, "ok": 2, "off": 3}


def worst(items: list[dict]) -> str:
    states = [i["state"] for i in items if i["state"] != "off"]
    return min(states, key=ORDER.get) if states else "ok"


def build(f: dict) -> list[dict]:
    """Check items from facts (missing facts = not known yet)."""
    out = []

    def add(name, state, text=""):
        out.append({"name": name, "state": state, "text": text})

    age = f.get("n1mm_age")
    if age is not None and age < 30:
        add("N1MM", "ok", f"{f['n1mm_freq']:.1f}" if f.get("n1mm_freq") else "")
    else:
        add("N1MM", "bad", "no radio info (N1MM: Config > Broadcast Data > Radio)")

    if f.get("log_open"):
        # N1MM writes its ini only now and then: after opening another contest
        # it can still name the old one. Warn only when that shows (today's
        # QSOs are in another contest); AppInfo itself comes only at N1MM's
        # start or a contest open, so POUNCer started later reads the ini.
        stale = f.get("log_stale_nr")
        if f.get("log_source") == "ini" and stale is not None:
            add("Log", "warn", (f.get("log_name") or "") +
                f" - but today's QSOs are in contest #{stale}: N1MM's ini is out of date."
                " Re-open the contest in N1MM, or tick Config > Broadcast Data > App Info")
        else:
            add("Log", "ok", f.get("log_name") or "")
    else:
        add("Log", "bad", f.get("log_msg") or "not open")

    add("Call", "ok", f["call"]) if f.get("call") else add("Call", "bad", "no station call")

    rbn = f.get("rbn")            # "ok" / "http" / "team" / text of the problem
    if rbn in ("ok", "http", "team", "paused"):
        add("RBN", "ok", {"ok": "", "http": "via HTTP backup", "team": "via team master",
                          "paused": "paused - radio on SSB"}[rbn])
    else:
        add("RBN", "bad", rbn or "no spots")

    if not f.get("cty_loaded"):
        add("cty", "bad", "no country file - mults / DX unknown")
    else:
        days = f.get("cty_days")
        if days is not None and days > CTY_OLD_DAYS:
            add("cty", "warn", f"{days:.0f} days old - N1MM: Tools > Download latest country files")
        else:
            add("cty", "ok", "" if days is None else f"{days:.0f} d")

    if not f.get("scp_enabled"):
        add("SCP", "off")
    elif not f.get("scp_loaded"):
        add("SCP", "warn", "not loaded - no ? marks")
    else:
        days = f.get("scp_days")
        add("SCP", "warn" if days is not None and days > SCP_OLD_DAYS else "ok",
            "" if days is None else f"{days:.0f} d" + (" old" if days > SCP_OLD_DAYS else ""))

    web = f.get("web")            # "ok" / "off" / "bad"
    add("Phone", web or "off", "" if web == "ok" else ("off" if web in (None, "off") else "web page not running"))

    off = f.get("clock_offset")
    if off is None:
        add("Clock", "off", "not checked")
    else:
        a = abs(off)
        # the web fallback is only good to a second or two: more slack
        ok_s = CLOCK_OK_S if f.get("clock_source", "NTP").startswith("NTP") else CLOCK_OK_S * 2
        state = "ok" if a <= ok_s else ("warn" if a <= CLOCK_BAD_S else "bad")
        if a < 1:
            text = ""
        else:
            # offset = how far the PC is BEHIND: + = PC slow, - = PC fast
            text = f"PC {a:.0f} s {'slow' if off > 0 else 'fast'}"
        add("Clock", state, text)
    return out


HEADS = {"ok": "READY", "warn": "CHECK", "bad": "NOT READY"}
MARKS = {"ok": "✓", "warn": "⚠", "bad": "✗"}


def segments(items: list[dict]) -> list[tuple[str, str]]:
    """The check line as (text, state) pieces, each in its own colour in the
    window: the head in the worst state, then every item in its own state
    (N1MM red while RBN and SCP stay green)."""
    out = [(HEADS[worst(items)], worst(items))]
    for i in items:
        if i["state"] == "off":
            continue
        out.append((f"{MARKS[i['state']]} {i['name']}" + (f" {i['text']}" if i["text"] else ""),
                    i["state"]))
    return out


def line(items: list[dict]) -> str:
    """'READY  ✓ N1MM 14025.0  ✓ Log ...' / 'CHECK  ⚠ cty 75 days old ...'."""
    return "   ".join(t for t, _ in segments(items))


NTP_SERVERS = ("pool.ntp.org", "time.windows.com")
NTP_PORT = 123
_NTP_DELTA = 2208988800            # 1900-01-01 -> 1970-01-01, seconds


def ntp_offset(host: str, port: int = NTP_PORT, timeout: float = 3.0, sock_factory=None) -> float:
    """SNTP (RFC 4330) query: seconds the PC clock is BEHIND the server
    (+ = PC slow, - = PC fast), from the four timestamps, so the network
    delay cancels out. Raises OSError / ValueError on no / bad answer."""
    import socket
    import struct
    make = sock_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
    sock = make()
    try:
        sock.settimeout(timeout)
        packet = bytearray(48)
        packet[0] = 0x23                       # LI 0, version 4, mode 3 (client)
        t1 = time.time()
        sec = int(t1 + _NTP_DELTA)
        frac = int((t1 % 1) * 2 ** 32)
        struct.pack_into("!II", packet, 40, sec, frac)     # transmit timestamp
        sock.sendto(bytes(packet), (host, port))
        data, _ = sock.recvfrom(512)
        t4 = time.time()
    finally:
        sock.close()
    if len(data) < 48:
        raise ValueError("short NTP answer")
    mode, stratum = data[0] & 0x07, data[1]
    if mode not in (4, 5) or not 1 <= stratum <= 15:
        raise ValueError(f"bad NTP answer (mode {mode}, stratum {stratum})")

    def ts(off):
        a, b = struct.unpack_from("!II", data, off)
        return a - _NTP_DELTA + b / 2 ** 32
    t2, t3 = ts(32), ts(40)                    # server receive / transmit
    return ((t2 - t1) + (t3 - t4)) / 2


class ClockCheck:
    """PC clock against a time server, in the background every 30 minutes.

    First NTP (``pool.ntp.org``, then ``time.windows.com``; milliseconds,
    like a time-sync program). Only if no NTP server answers: a web
    server's ``Date`` header (whole seconds, and corrected by ``Age`` - a
    cached answer's Date is older than now, that read "PC 24 s fast"
    before). ``offset`` = seconds the PC is behind (+) or ahead (-);
    ``source`` = "NTP <host>" or "web"; None until known."""

    def __init__(self, url: str = CLOCK_URL, every_s: float = 1800, fetch=None,
                 ntp=None, servers=NTP_SERVERS) -> None:
        self.url = url
        self.every_s = every_s
        self._fetch = fetch or self._date_header
        self._ntp = ntp if ntp is not None else ntp_offset
        self.servers = tuple(servers)
        self.offset: float | None = None
        self.source = ""
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="clock-check", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def check_once(self) -> float | None:
        for host in self.servers:
            try:
                self.offset = float(self._ntp(host))
                self.source = f"NTP {host}"
                return self.offset
            except Exception as exc:  # noqa: BLE001 - next server / the web
                log.info("clock check: NTP %s: %s", host, exc)
        t0 = time.time()
        date, age = self._fetch(self.url)
        t1 = time.time()
        server = email.utils.parsedate_to_datetime(date).timestamp() + float(age or 0)
        # the header is cut to whole seconds: +0.5 s is the best guess
        self.offset = server + 0.5 - (t0 + t1) / 2
        self.source = "web"
        return self.offset

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception as exc:  # noqa: BLE001 - offline: just unknown
                log.info("clock check: %s", exc)
            self._stop.wait(self.every_s)

    @staticmethod
    def _date_header(url: str) -> tuple[str, float]:
        req = urllib.request.Request(url, method="HEAD", headers={
            "User-Agent": "POUNCer", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            try:
                age = float(resp.headers.get("Age") or 0)
            except ValueError:
                age = 0.0
            return resp.headers["Date"], age
