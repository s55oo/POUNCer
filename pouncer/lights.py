"""Connection lights for the main window: one coloured dot per connection.

green = working (this stream delivers), yellow = on hold (ready, not
needed now: RBN on SSB, the SSB cluster while N1MM sends, the HTTP backup
while telnet works), amber = connecting / partly there, red = not working
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


def rbn_light(status: str, last_spot_age: float | None, role: str,
              http: bool = False) -> tuple[str, str]:
    s0 = (status or "").lower()
    if s0.startswith("paused"):
        return HOLD, "RBN on hold (SSB)"  # SSB contest / every radio on SSB: not needed
    if role == "slave":
        return OK, "RBN via master"
    if http and last_spot_age is not None and last_spot_age < RBN_SPOT_S:
        return OK, "RBN via HTTP"       # telnet down, the HTTP backup delivers
    s = (status or "").lower()
    name = "RBN via backup" if "backup" in s and "trying rbn" not in s else "RBN"
    if last_spot_age is not None and last_spot_age < RBN_SPOT_S:
        return OK, name
    if s.startswith("logged in"):
        return OK, name
    if s.startswith(("starting", "connecting")):
        return WAIT, "RBN"
    return BAD, "RBN"


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


def ssb_light(status: str, role: str = "solo") -> tuple[str, str]:
    """SSB: the DX cluster for human spots (only while needed)."""
    if role == "slave":
        return OK, "SSB via master"
    s = (status or "").lower()
    if s in ("", "off"):
        return OFF, "SSB off"
    if s.startswith("via n1mm"):
        return HOLD, "SSB cluster on hold (N1MM)"   # N1MM's spots instead
    if s.startswith("logged in"):
        return OK, "SSB cluster"
    if s.startswith(("connecting", "starting")):
        return WAIT, "SSB cluster"
    return BAD, "SSB cluster"


def http_light(configured: bool, active: bool, delivering: bool) -> tuple[str, str]:
    """RBN over HTTP backup: on hold while telnet works, green while it
    delivers, red when started but nothing comes."""
    if not configured:
        return OFF, "HTTP backup off"
    if not active:
        return HOLD, "HTTP backup on hold"
    return (OK, "HTTP backup") if delivering else (BAD, "HTTP backup")


def n1mm_spots_light(use: bool, seen: bool, via: bool, quiet_s: float | None,
                     limit_s: float) -> tuple[str, str]:
    """N1MM's spot packets (Broadcast Data > Spots): green while they replace
    our SSB cluster, yellow = sending but not used now, red = stopped."""
    if not use or not seen:
        return OFF, "N1MM spots off"
    if quiet_s is not None and quiet_s > limit_s:
        return BAD, "N1MM spots stopped"
    return (OK, "N1MM spots") if via else (HOLD, "N1MM spots on hold")


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


def update_light(state: str, text: str) -> tuple[str, str]:
    """state from pouncer.update: off/checking/current/downloading/ready/failed."""
    return {"off": OFF, "current": OK, "ready": NEW, "available": NEW}.get(state, WAIT), text
