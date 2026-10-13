"""Pre-contest self-test (main window button, exe only).

Checks, one by one, the things that otherwise only show up in the first
hour of a contest: N1MM radio info and log, the RBN feed and its SH/DX
history at start, the RBN / backup / SSB servers answering, the decimal
sign of a typed frequency, a test call put into Entry Window A and B (and
the box put back as it was), and - only when asked - a QSY 1 kHz up and
back on each radio.

The checks are plain functions (values in, ``(status, detail)`` out) so
they can be tested without N1MM, Windows or the network; ``app.py`` feeds
them and shows the result in a small window. Nothing is ever sent to N1MM
that transmits, and no passwords are used or shown.
"""

from __future__ import annotations

import socket
import time

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
MARK = {PASS: "OK", WARN: "CHECK", FAIL: "FAIL", SKIP: "-"}
COLORS = {PASS: "#2e7d32", WARN: "#b26a00", FAIL: "#c62828", SKIP: "#757575"}

TEST_CALL = "TEST"
QSY_STEP_KHZ = 1.0


def _tag(nr: int) -> str:
    return "AB"[nr - 1] if nr in (1, 2) else str(nr)


# ---- N1MM --------------------------------------------------------------

def check_radios(snaps: dict[int, dict]) -> tuple[str, str]:
    """snaps: radio nr -> RadioState snapshot (freq_khz, mode, entry_window_hwnd)."""
    if not snaps:
        return FAIL, ("no radio info from N1MM - is N1MM running, and Config > Broadcast "
                      "Data > Radio checked (port 12060)?")
    parts, status = [], PASS
    for nr in sorted(snaps):
        s = snaps[nr] or {}
        f = s.get("freq_khz")
        text = f"{_tag(nr)} {f:.1f} {s.get('mode') or '?'}" if f is not None else f"{_tag(nr)} no freq"
        if not s.get("entry_window_hwnd"):
            text += " (no Entry Window handle)"
            status = WARN
        if f is None:
            status = WARN
        parts.append(text)
    if len(snaps) == 1:
        parts.append("one radio (SO1R)")
    return status, ", ".join(parts)


def check_log(log_open: bool, state: str) -> tuple[str, str]:
    if log_open:
        return PASS, "open (worked stations and mults are read)"
    if state == "unknown":
        return WARN, "not known yet"
    return FAIL, "no N1MM log open - open the contest log in N1MM, then Reconnect log"


# ---- RBN / clusters -----------------------------------------------------

def check_feed(status: str, spot_age: float | None, role: str,
               on_backup: bool = False) -> tuple[str, str]:
    """The running RBN connection."""
    if role == "slave":
        return PASS, "team slave: spots come from the master"
    s = (status or "").lower()
    via = " via the backup server" if on_backup else ""
    if spot_age is not None and spot_age < 120:
        return (WARN if on_backup else PASS), f"spots coming{via} (last {int(spot_age)} s ago)"
    if s.startswith("logged in"):
        return WARN, f"{status}{via}, but no spot of the skimmer yet (quiet band, or wrong skimmer?)"
    if s.startswith(("connecting", "starting")):
        return WARN, f"{status} - not logged in yet"
    return FAIL, status or "not connected"


def check_history(wanted: int, lines: int | None, role: str) -> tuple[str, str]:
    """SH/DX at login: did RBN answer with last spots we could read?"""
    if role == "slave":
        return SKIP, "team slave"
    if not wanted:
        return SKIP, "off ([rbn] history = 0)"
    if lines is None:
        return WARN, "not connected yet"
    if lines:
        return PASS, f"{lines} past spots read at login"
    return WARN, ("no past spots read at login - RBN didn't answer SH/DX, or its lines "
                  "look different (send the Diagnostics log)")


def check_login_clash(rbn_call: str, backup_host: str, backup_port: int,
                      ssb_host: str, ssb_port: int, ssb_login: str, ssb_mode: str) -> tuple[str, str]:
    """RBN backup and SSB on the same cluster with the same call: the
    cluster may throw one out when the other logs in."""
    if ssb_mode == "off" or not ssb_host or not backup_host:
        return SKIP, "not both used"
    same_server = (backup_host.lower(), int(backup_port)) == (ssb_host.lower(), int(ssb_port))
    if not same_server:
        return PASS, "different servers"
    ssb_call = (ssb_login or rbn_call or "").upper()
    if ssb_call and ssb_call == (rbn_call or "").upper():
        return WARN, (f"RBN backup and SSB both log in to {ssb_host} as {ssb_call}; if both "
                      f"are needed at once one may be thrown out - set [ssb] login = "
                      f"{ssb_call}-2")
    return PASS, f"SSB logs in as {ssb_call}"


def probe(host: str, port: int, timeout: float = 6.0,
          connect=socket.create_connection) -> tuple[str, str]:
    """Connect, read the greeting, close - without logging in (so a
    running connection with the same call is never disturbed)."""
    if not host:
        return SKIP, "not set"
    t0 = time.monotonic()
    try:
        sock = connect((host, int(port)), timeout=timeout)
    except OSError as exc:
        return FAIL, f"{host}:{port} not reachable ({exc.__class__.__name__}: {exc})"
    ms = int((time.monotonic() - t0) * 1000)
    data = b""
    try:
        sock.settimeout(0.5)
        end = time.monotonic() + min(timeout, 4.0)
        while time.monotonic() < end and len(data) < 2048:
            try:
                chunk = sock.recv(1024)
            except socket.timeout:
                if data:
                    break
                continue
            if not chunk:
                break
            data += chunk
            low = data.lower()
            if b"call" in low or b"login" in low:
                break
    except OSError:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
    text = data.decode("latin-1", "replace").lower()
    if "call" in text or "login" in text:
        return PASS, f"{host}:{port} answers and asks for the call ({ms} ms)"
    if data:
        return PASS, f"{host}:{port} answers ({ms} ms)"
    return WARN, f"{host}:{port} connects ({ms} ms) but sent no login prompt"


# ---- typing ------------------------------------------------------------

def check_decimal(sep: str, sample: str) -> tuple[str, str]:
    if sep not in (".", ","):
        return WARN, f"Windows decimal sign unknown ('{sep}'); 14025.3 is typed as \"{sample}\""
    return PASS, f"Windows decimal sign '{sep}': 14025.3 is typed as \"{sample}\""


def box_roundtrip(get_text, set_text, test: str = TEST_CALL) -> tuple[str, str]:
    """Put a test call into the focused Entry Window box, read it back,
    then put back what was there. get_text() -> str | None (None = no
    box / not Windows), set_text(text) -> bool."""
    before = get_text()
    if before is None:
        return FAIL, "no text box with the cursor in that Entry Window"
    if not set_text(test):
        return FAIL, "could not put text into the box"
    time.sleep(0.15)
    got = get_text()
    restored = set_text(before)
    note = "" if restored else " (could NOT put the old text back)"
    was = f' (it held "{before}", put back)' if before else ""
    if got is not None and got.strip().upper() == test.upper():
        return (PASS if restored else WARN), f'"{test}" in and read back{was}{note}'
    return FAIL, f'put "{test}" in but the box shows "{got}"{note}'


def qsy_roundtrip(get_freq, move, f0: float, step: float = QSY_STEP_KHZ,
                  wait_s: float = 4.0, sleep=time.sleep, clock=time.monotonic) -> tuple[str, str]:
    """Move the radio ``step`` kHz up and back; wait each time until N1MM
    reports it. get_freq() -> kHz | None, move(khz) -> bool."""
    def go(target: float) -> float | None:
        if not move(target):
            return None
        t0 = clock()
        while clock() - t0 < wait_s:
            f = get_freq()
            if f is not None and abs(f - target) <= 0.15:
                return clock() - t0
            sleep(0.05)
        return None

    up = go(f0 + step)
    back = go(f0)
    if up is None and back is None:
        return FAIL, f"the radio did not move (still {get_freq()})"
    if up is None:
        return FAIL, f"no move to {f0 + step:.1f} reported by N1MM"
    if back is None:
        return FAIL, f"moved up ({up * 1000:.0f} ms) but did not come back to {f0:.1f} - check the radio!"
    slow = max(up, back) > 1.5
    return (WARN if slow else PASS), (
        f"{f0 + step:.1f} in {up * 1000:.0f} ms, back to {f0:.1f} in {back * 1000:.0f} ms"
        + (" - slow: raise qsy_wait_ms" if slow else ""))


# ---- running and reporting -----------------------------------------------

def run(checks, report, stop=lambda: False) -> list[tuple[str, str, str]]:
    """checks: list of (name, fn) with fn() -> (status, detail); report(name,
    status, detail) is called after each. A crashing check is a FAIL."""
    out = []
    for name, fn in checks:
        if stop():
            break
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001 - one broken check must not stop the rest
            status, detail = FAIL, f"error: {exc!r}"
        out.append((name, status, detail))
        report(name, status, detail)
    return out


def summary(results) -> str:
    n = {k: 0 for k in MARK}
    for _name, status, _d in results:
        n[status] = n.get(status, 0) + 1
    if n[FAIL]:
        head = f"{n[FAIL]} FAILED"
    elif n[WARN]:
        head = f"all working, {n[WARN]} to check"
    else:
        head = "all OK"
    return f"{head} ({n[PASS]} OK, {n[WARN]} check, {n[FAIL]} failed, {n[SKIP]} skipped)"


def as_text(results, version: str = "") -> str:
    lines = [f"POUNCer {version} self-test {time.strftime('%Y-%m-%d %H:%M')}".replace("  ", " ")]
    for name, status, detail in results:
        lines.append(f"[{MARK[status]:>5}] {name}: {detail}")
    lines.append(summary(results))
    return "\n".join(lines)
