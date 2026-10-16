"""Pre-contest check: is everything POUNCer needs working?

One line in the main window (and a warning on the phone when something is
wrong): N1MM radio info, the log, the station call, RBN, the country file
(cty), Super Check Partial, the phone page and the PC clock.

``build`` turns plain facts into items {name, state, text}; state is
"ok", "warn", "bad" or "off". ``ClockCheck`` compares the PC clock with a
web server's clock (HTTP ``Date`` header) every 30 minutes.
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
    if rbn in ("ok", "http", "team"):
        add("RBN", "ok", {"ok": "", "http": "via HTTP backup", "team": "via team master"}[rbn])
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
        state = "ok" if a <= CLOCK_OK_S else ("warn" if a <= CLOCK_BAD_S else "bad")
        add("Clock", state, f"{off:+.0f} s" if a >= 1 else "")
    return out


def line(items: list[dict]) -> str:
    """'READY  ✓ N1MM 14025.0  ✓ Log ...' / 'CHECK  ⚠ cty 75 days old ...'."""
    head = {"ok": "READY", "warn": "CHECK", "bad": "NOT READY"}[worst(items)]
    marks = {"ok": "✓", "warn": "⚠", "bad": "✗"}
    parts = []
    for i in items:
        if i["state"] == "off":
            continue
        parts.append(f"{marks[i['state']]} {i['name']}" + (f" {i['text']}" if i["text"] else ""))
    return head + "   " + "   ".join(parts)


class ClockCheck:
    """PC clock against a web server's clock (``Date`` header, 1 s steps),
    in the background every 30 minutes. ``offset`` = seconds the PC is
    behind (+) or ahead (-); None until known."""

    def __init__(self, url: str = CLOCK_URL, every_s: float = 1800, fetch=None) -> None:
        self.url = url
        self.every_s = every_s
        self._fetch = fetch or self._date_header
        self.offset: float | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="clock-check", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def check_once(self) -> float | None:
        t0 = time.time()
        date = self._fetch(self.url)
        t1 = time.time()
        server = email.utils.parsedate_to_datetime(date).timestamp()
        # the header is cut to whole seconds: +0.5 s is the best guess
        self.offset = server + 0.5 - (t0 + t1) / 2
        return self.offset

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception as exc:  # noqa: BLE001 - offline: just unknown
                log.info("clock check: %s", exc)
            self._stop.wait(self.every_s)

    @staticmethod
    def _date_header(url: str) -> str:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "POUNCer"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.headers["Date"]
