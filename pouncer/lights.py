"""Connection lights for the main window: one coloured dot per connection.

green = connected / working, amber = waiting or only partly there,
red = not working, grey = switched off. Pure logic (no tkinter), so it is
testable anywhere; ``app.py`` draws the dots.
"""

from __future__ import annotations

OK, WAIT, BAD, OFF, NEW = "ok", "wait", "bad", "off", "new"

COLORS = {OK: "#2e7d32", WAIT: "#f9a825", BAD: "#c62828", OFF: "#9e9e9e",
          NEW: "#1565c0"}  # NEW (blue) = an update is ready

N1MM_QUIET_S = 120   # no radio info this long = N1MM probably closed
RBN_SPOT_S = 120     # a spot this recently = RBN works whatever the status says


def rbn_light(status: str, last_spot_age: float | None, role: str) -> tuple[str, str]:
    if role == "slave":
        return OK, "RBN via master"
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
    if s.startswith("logged in"):
        return OK, "SSB cluster"
    if s.startswith(("connecting", "starting")):
        return WAIT, "SSB cluster"
    return BAD, "SSB cluster"


def web_light(enabled: bool, running: bool) -> tuple[str, str]:
    if not enabled:
        return OFF, "Phone off"
    return (OK if running else BAD), "Phone"


def warm_light(enabled: bool, status: str) -> tuple[str, str]:
    if not enabled:
        return OFF, "Warm-up off"
    if "failed" in status:
        return BAD, "Warm-up"
    if "known in advance" in status:
        return OK, "Warm-up"
    return WAIT, "Warm-up"


def update_light(state: str, text: str) -> tuple[str, str]:
    """state from pouncer.update: off/checking/current/downloading/ready/failed."""
    return {"off": OFF, "current": OK, "ready": NEW, "available": NEW}.get(state, WAIT), text
