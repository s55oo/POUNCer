"""Station test (View > Station test): does QSY + callsign entry work?

A quick check after setting up (cables, COM ports, N1MM's Broadcast Data
just moved): per radio N1MM reports, QSY 0.1 kHz up and back (no TX), and
how long N1MM took to report the new frequency; then a test call into the
empty callsign box of its Entry Window, read back, and cleared again. A box
that already holds something is left alone. Pure logic with the actions
passed in, so it is testable anywhere; the app runs it in a thread.
"""

from __future__ import annotations

import time

TEST_CALL = "TEST"
STEP_KHZ = 0.1


def run(radios, snapshot, qsy, wait_for, entry_hwnd=None, read_box=None, set_box=None,
        radio_name=lambda nr: f"radio {nr}", skip_qsy=lambda nr: "", clock=time.time,
        sleep=time.sleep) -> list[dict]:
    """[{radio, ok, lines: [(ok | None, text)]}] - one per radio.

    snapshot(nr) -> {"freq_khz"}; qsy(nr, khz) -> bool (sent); wait_for(nr,
    khz) -> bool (N1MM reported it); entry_hwnd(nr); read_box(hwnd) -> text or
    None; set_box(hwnd, text) -> bool; skip_qsy(nr) -> reason not to QSY."""
    out = []
    if not radios:
        return [{"radio": "-", "ok": False,
                 "lines": [(False, "N1MM reports no radio (Config > Broadcast Data > Radio)")]}]
    for nr in radios:
        lines: list[tuple[bool | None, str]] = []
        snap = snapshot(nr) or {}
        f0 = snap.get("freq_khz")
        if not f0:
            lines.append((False, "no frequency from N1MM"))
        elif skip_qsy(nr):
            lines.append((None, f"QSY not tested: {skip_qsy(nr)}"))
        else:
            target = round(f0 + STEP_KHZ, 3)
            for label, khz in (("QSY up", target), ("QSY back", f0)):
                t0 = clock()
                if not qsy(nr, khz):
                    lines.append((False, f"{label} {khz:.1f}: sending to N1MM failed"))
                    break
                landed = wait_for(nr, khz)
                took = clock() - t0
                lines.append((landed, f"{label} {khz:.1f}: N1MM reported it after {took:.1f} s"
                              if landed else f"{label} {khz:.1f}: N1MM didn't report it "
                              f"({took:.1f} s) - check Broadcast Data > Radio / the CAT link"))
                if not landed:
                    break
        hwnd = entry_hwnd(nr) if entry_hwnd else None
        if read_box is None or set_box is None:
            lines.append((None, "callsign entry: Windows only"))
        elif not hwnd:
            lines.append((False, "callsign entry: N1MM names no Entry Window"))
        else:
            before = read_box(hwnd)
            if before is None:
                lines.append((False, "callsign entry: the focused box can't be read"))
            elif before.strip():
                lines.append((None, f"callsign entry not tested: the box holds "
                                    f"'{before.strip()}' (left as it is)"))
            else:
                t0 = clock()
                set_box(hwnd, TEST_CALL)
                sleep(0.3)
                got = read_box(hwnd)
                ok = (got or "").strip().upper() == TEST_CALL
                set_box(hwnd, "")
                sleep(0.2)
                cleared = not (read_box(hwnd) or "").strip()
                lines.append((ok, f"callsign entry: {TEST_CALL} in the box after "
                                  f"{clock() - t0:.1f} s" if ok else
                              f"callsign entry: the box shows '{got or ''}', not {TEST_CALL}"))
                lines.append((cleared, "box cleared again" if cleared
                              else "box NOT cleared - empty it in N1MM"))
        out.append({"radio": radio_name(nr),
                    "ok": all(ok is not False for ok, _ in lines), "lines": lines})
    return out


def report(results: list[dict]) -> str:
    rows = []
    for r in results:
        rows.append(f"{r['radio']}: {'OK' if r['ok'] else 'PROBLEM'}")
        for ok, text in r["lines"]:
            rows.append(f"   {'✓' if ok else ('✗' if ok is False else '-')} {text}")
    return "\n".join(rows)
