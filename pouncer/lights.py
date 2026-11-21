"""Connection lights for the main window: one coloured dot per connection.

green = working (this stream delivers), yellow = on hold (ready, not
needed now: SPOTS (RBN) on SSB with no Telnet delivering, a Telnet on the same
server as RBN), amber = connecting / partly there, red = not working
(connection problem), grey = switched off. Pure logic (no tkinter), so it is
testable anywhere; ``app.py`` draws the dots.
"""

from __future__ import annotations

OK, WAIT, BAD, OFF, NEW, HOLD = "ok", "wait", "bad", "off", "new", "hold"

COLORS = {OK: "#2e7d32", WAIT: "#fb8c00", BAD: "#c62828", OFF: "#9e9e9e",
          NEW: "#1565c0",   # NEW (blue) = an update is ready
          HOLD: "#fdd835"}  # on hold: ready, not needed now

N1MM_QUIET_S = 120   # no radio info this long = N1MM probably closed
RBN_SPOT_S = 120     # a spot this recently = RBN works whatever the status says


VIA = {"ok": "RBN", "telnet": "Telnet #1", "telnet2": "Telnet #2", "telnet3": "Telnet #3",
       "telnet4": "Telnet #4", "telnet5": "Telnet #5",
       "http": "HTTP", "hamqth": "HamQTH"}


def rbn_light(status: str, last_spot_age: float | None, role: str,
              http: bool = False, working: str = "") -> tuple[str, str]:
    """The SPOTS light (called RBN up to v0.7.202): green as long as ONE source
    brings spots - ``working`` = ``app._spot_source_working()`` ("ok" = RBN,
    "telnet" / "telnet2" / "telnet3" / "telnet4" / "telnet5", "http", "hamqth") - else from
    RBN's own status."""
    s0 = (status or "").lower()
    # RBN paused for SSB, but a Telnet still delivers (dual mode: people's SSB
    # and skimmer CW spots go on): that one is the light, green (v0.7.244)
    if s0.startswith("paused") and role != "slave" and working in VIA and working != "ok":
        return OK, f"SPOTS via {VIA[working]}"
    if s0.startswith("paused"):
        return HOLD, "SPOTS on hold (SSB)"  # SSB contest / every radio on SSB: not needed
    if role == "slave":
        return OK, "SPOTS via master"
    if working in VIA:
        return OK, f"SPOTS via {VIA[working]}"
    if http and last_spot_age is not None and last_spot_age < RBN_SPOT_S:
        return OK, "SPOTS via HTTP"     # telnet down, the HTTP backup delivers
    s = (status or "").lower()
    name = "SPOTS via backup" if "backup" in s and "trying rbn" not in s else "SPOTS"
    if last_spot_age is not None and last_spot_age < RBN_SPOT_S:
        return OK, name
    if s.startswith("logged in"):
        return OK, name
    if s.startswith("via n1mm"):
        return OK, "SPOTS via N1MM's login"   # one login on the node, shared with N1MM
    if s.startswith("off"):
        return OFF, "SPOTS: no source on"
    if s.startswith("standby"):
        return (OK if http else HOLD), ("SPOTS via HTTP" if http else "SPOTS standby (HTTP)")
    if s.startswith(("starting", "connecting")):
        return WAIT, "SPOTS"
    return BAD, "SPOTS"


def n1mm_light(radio_age: float | None) -> tuple[str, str]:
    if radio_age is None:
        return BAD, "N1MM"
    return (OK if radio_age < N1MM_QUIET_S else WAIT), "N1MM"


def log_light(log_open: bool, log_state: str) -> tuple[str, str]:
    if log_open:
        return OK, "Log"
    return (WAIT if log_state == "unknown" else BAD), "Log"


def team_light(role: str, pouncers: int,
               mismatch: list[tuple[str, str]] | None = None) -> tuple[str, str]:
    """role: off / solo / master / slave. mismatch: (ip, version) of team
    POUNCers on another version than this one -> amber with who it is."""
    if role in ("master", "slave"):
        if mismatch:
            who = ", ".join(f"{ip} on {ver}" for ip, ver in mismatch)
            return WAIT, f"Team {role} ({pouncers}): {who}"
        return OK, f"Team {role} ({pouncers})"
    if role == "solo":
        return WAIT, "Team: alone"
    return OFF, "Team off"


def telnet_light(states, role: str = "solo") -> tuple[str, str]:
    """The Telnet #1 - #5 sources (v0.7.213), one light: ``states`` =
    [(n, status)] of the ticked ones; status = the connection's text, "same"
    (the same server as one before it: nothing extra) or "" (not set)."""
    if role == "slave":
        return OK, "Telnet via master"
    if not states:
        return OFF, "Telnet off"
    good, bad, wait = [], [], []
    for n, st in states:
        s = (st or "").lower()
        if s == "same":
            continue
        if s.startswith("logged in"):
            good.append(n)
        elif s.startswith(("connecting", "starting")) or not s:
            wait.append(n)
        else:
            bad.append(n)
    used = sorted(good + bad + wait)
    name = "Telnet " + "+".join(f"#{n}" for n in used) if used else "Telnet"
    if not used:
        return HOLD, "Telnet: same server as RBN (nothing extra)"
    if good and not bad and not wait:
        return OK, name
    if good or wait:
        down = bad + wait
        return WAIT, name + (" (" + ", ".join(f"#{n}" for n in sorted(down)) + " not yet)"
                             if good and down else "")
    return BAD, name


def http_light(configured: bool, active: bool, delivering: bool) -> tuple[str, str]:
    """The 'HTTP' source (RBN over HTTP): off when not switched on, green
    while it delivers, red when running but nothing comes."""
    if not configured:
        return OFF, "HTTP off"
    if not active:
        return WAIT, "HTTP"
    return (OK, "HTTP") if delivering else (BAD, "HTTP")


def web_light(enabled: bool, running: bool) -> tuple[str, str]:
    if not enabled:
        return OFF, "Phone off"
    return (OK if running else BAD), "Phone"


def warm_light(enabled: bool, status: str) -> tuple[str, str]:
    if not enabled:
        return OFF, "Warm-up off"
    if "known in advance" in status:     # (one of CW / SSB may still be missing)
        return OK, "Warm-up"
    if "failed" in status:
        return BAD, "Warm-up"
    return WAIT, "Warm-up"


def hamfiles_light(cty_loaded: bool, scp_on: bool, scp_loaded: bool) -> tuple[str, str]:
    """HamFiles: the country file (cty.dat) and Super Check Partial (MASTER.DTA).
    Green = both loaded (or SCP switched off and cty.dat loaded), amber = one
    missing, red = both missing."""
    missing = ([] if cty_loaded else ["cty.dat"]) + ([] if scp_loaded or not scp_on else ["MASTER.DTA"])
    if not missing:
        return OK, "HamFiles"
    if len(missing) == 2:
        return BAD, "HamFiles NOK"
    return WAIT, "HamFiles: no " + missing[0]


def update_light(state: str, text: str) -> tuple[str, str]:
    """state from pouncer.update: off/checking/current/downloading/ready/failed."""
    return {"off": OFF, "current": OK, "ready": NEW, "available": NEW}.get(state, WAIT), text
