"""Screenshots of the window for the build (GitHub Actions, Windows).

Opens POUNCer with made-up spots (new mults, DX, worked, run history) in
each layout / look and saves a PNG of the window into the folder given -
uploaded as the build's "screenshots" artifact, so the real Windows look
(Segoe UI, DPI) can be checked before a release. Not a test: never fails
the build (the workflow step is continue-on-error).

    python tests/ci_screenshots.py screenshots
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CALLS = ["VE6EMM", "F5BY", "K2BWC", "G2VSR", "W5GW", "LZ5DCP", "EA6RJU", "DL8RWH", "DL7SF",
         "F3FL", "DL5TXM", "G8UNJ", "LU9NX", "ZS7ST", "VK6AK", "VK9DQW", "SP8ILT", "PY7SJL",
         "YO6ZN", "K1IUO", "F9UZ", "OK1RR", "HA8JV", "9A1A", "JA1XYZ", "UA9ABC"]


def shot(out_dir: str, layout: str, look: str) -> str:
    from pouncer.config import load_config
    from pouncer.app import PouncerApp
    from pouncer.rbn import Spot
    from tests.test_so2r import radio_info
    import pouncer.app as appmod

    cfg = load_config("")
    cfg.run_history_dir = tempfile.mkdtemp()
    cfg.look, cfg.layout, cfg.rate_line = look, layout, True
    app = PouncerApp(cfg)
    app._ensure_rbn_clients = lambda: None
    app._tick = lambda: None
    app.state.update(radio_info(1, 14025.0, 1111))
    app.web = types.SimpleNamespace(port=8080, load=lambda: (0, 0))
    appmod.pick_web_address = lambda host: ("10.66.66.5", "ZeroTier")
    app._refresh_web_address()
    random.seed(7)
    dx = {c for c in CALLS if c[0] in "KWVLPJZ" or c.startswith(("VK", "UA9"))}
    mults = {"ZS7ST": 2, "VK6AK": 1, "EA6RJU": 1, "JA1XYZ": 2, "9A1A": 1}
    runs = {c: {"run": random.choice([12, 20, 60, 77, 84, 88, 91, 95]),
                "hours": random.choice([6, 14, 23, 30]), "rec": False} for c in CALLS}
    app._is_dx = lambda call: call in dx
    app._mults = lambda s: mults.get(s.dxcall, 0)
    app._run_fields = lambda s: runs.get(s.dxcall, {})
    worked = {"G2VSR", "F3FL"}
    app.worked.is_worked = lambda call, khz, mode: call in worked
    now = time.time()
    for c in CALLS:
        app.spot_queue.put(Spot(dxcall=c, freq_khz=round(14000 + random.uniform(1, 60), 1),
                                mode="CW", snr=random.randint(6, 38), wpm=random.randint(22, 34),
                                spot_type="CQ", spotter=random.choice(["S53WW", "DL8LAS", "OH6BG"]),
                                time_utc="1200Z", received=now - random.uniform(0, 540)))
    app.spot_queue.put(Spot(dxcall="DL1TUN", freq_khz=14025.0, mode="CW", snr=27, wpm=30,
                            spot_type="CQ", spotter="S53WW", time_utc="1200Z", received=now))
    app.rbn_status = "logged in as S55OO"
    app.root.geometry("1280x820+0+0")
    for _ in range(3):
        app._tick_body()
        app.root.update()
    app.root.after(1500, app.root.quit)
    app.root.mainloop()
    app.root.update()
    path = os.path.join(out_dir, f"{layout}-{look}.png")
    x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
    w, h = app.root.winfo_width(), app.root.winfo_height()
    from PIL import ImageGrab
    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(path)
    if layout == "clean" and look == "dark":          # hover over Runs: the explanation
        rows = app.rows
        xr = next(x0 + 10 for k, x0, x1 in rows._xs if k == "run")
        ev = types.SimpleNamespace(x=xr, y=8, x_root=rows.c.winfo_rootx() + xr,
                                   y_root=rows.c.winfo_rooty() + 8)
        rows.items[0]["run_tip"] = rows.items[0].get("run_tip") or app.run_explain(
            rows.items[0]["call"], {"run": 26, "hours": 18, "rec": False}, True)
        rows._hover(ev)
        for _ in range(5):
            app.root.update()
            time.sleep(0.2)
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(
            os.path.join(out_dir, "runs-tip.png"))
        rows._hide_tip()
    if layout == "clean" and look == "dark":          # traffic console: hover over RBN
        try:
            con = app._console
            con.open()
            con.counts["RBN"] = 42
            for _ in range(3):
                app.root.update()
            dot, lbl = con.chips["RBN"]
            ev = types.SimpleNamespace(x_root=lbl.winfo_rootx() + 5, y_root=lbl.winfo_rooty() + 5)
            con._show_tip(ev, "RBN")
            for _ in range(5):
                app.root.update()
                time.sleep(0.2)
            cw = con.win
            cx, cy = cw.winfo_rootx(), cw.winfo_rooty()
            tw = con._tip
            right = max(cx + cw.winfo_width(), tw.winfo_rootx() + tw.winfo_width())
            bottom = max(cy + cw.winfo_height(), tw.winfo_rooty() + tw.winfo_height())
            ImageGrab.grab(bbox=(cx, cy, right, bottom), all_screens=True).save(
                os.path.join(out_dir, "console-tip.png"))
            con.close()
        except Exception as exc:  # noqa: BLE001
            print(f"console tip screenshot failed: {exc!r}")
    if layout == "clean" and look == "dark":          # the phone page QR window too
        appmod.phone_addresses = lambda host: [("LAN", "192.168.1.20"), ("ZeroTier", "10.66.66.5")]
        app._show_phone_qr()
        win = app._qr_window
        for _ in range(5):
            app.root.update()
            time.sleep(0.2)
        qx, qy = win.winfo_rootx(), win.winfo_rooty()
        ImageGrab.grab(bbox=(qx, qy, qx + win.winfo_width(), qy + win.winfo_height()),
                       all_screens=True).save(os.path.join(out_dir, "phone-qr.png"))
    app.root.destroy()
    return path


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "screenshots"
    os.makedirs(out, exist_ok=True)
    for layout, look in (("clean", "dark"), ("clean", "light"), ("detailed", "dark")):
        try:
            print("saved", shot(out, layout, look))
        except Exception as exc:  # noqa: BLE001 - screenshots never fail the build
            print(f"screenshot {layout}-{look} failed: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
