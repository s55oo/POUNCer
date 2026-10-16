"""Pre-contest check line and the 'already tried' marks."""

import email.utils
import tempfile
import types
import unittest

from pouncer import checks
from pouncer.summary import TapLog

GOOD = {"n1mm_age": 2, "n1mm_freq": 14025.0, "log_open": True, "log_name": "S55OO_26.s3db",
        "call": "S55OO", "rbn": "ok", "cty_loaded": True, "cty_days": 12, "scp_enabled": True,
        "scp_loaded": True, "scp_days": 3, "web": "ok", "clock_offset": 0.4}


def by_name(items):
    return {i["name"]: i for i in items}


class BuildTests(unittest.TestCase):
    def test_all_good_is_ready(self) -> None:
        items = checks.build(GOOD)
        self.assertEqual(checks.worst(items), "ok")
        text = checks.line(items)
        self.assertTrue(text.startswith("READY"))
        for part in ("✓ N1MM 14025.0", "✓ Log S55OO_26.s3db", "✓ Call S55OO",
                     "✓ cty 12 d", "✓ SCP 3 d", "✓ Phone", "✓ Clock"):
            self.assertIn(part, text)

    def test_old_files_and_clock_warn(self) -> None:
        items = by_name(checks.build(dict(GOOD, cty_days=75, scp_days=40, clock_offset=-12,
                                          clock_source="NTP pool.ntp.org")))
        self.assertEqual(items["cty"]["state"], "warn")
        self.assertIn("Download latest country files", items["cty"]["text"])
        self.assertEqual(items["SCP"]["state"], "warn")
        self.assertEqual(items["Clock"]["state"], "warn")
        self.assertEqual(items["Clock"]["text"], "PC 12 s fast")
        self.assertTrue(checks.line(list(items.values())).startswith("CHECK"))

    def test_broken_things_are_bad(self) -> None:
        f = dict(GOOD, n1mm_age=None, log_open=False, log_msg="log not found", call="",
                 rbn="connecting...", cty_loaded=False, web="bad", clock_offset=300)
        items = by_name(checks.build(f))
        for name in ("N1MM", "Log", "Call", "RBN", "cty", "Phone", "Clock"):
            self.assertEqual(items[name]["state"], "bad", name)
        self.assertEqual(items["Log"]["text"], "log not found")
        self.assertTrue(checks.line(list(items.values())).startswith("NOT READY"))

    def test_log_from_ini_warns_only_when_out_of_date(self) -> None:
        ok = by_name(checks.build(dict(GOOD, log_source="ini", log_stale_nr=None)))["Log"]
        self.assertEqual(ok["state"], "ok")
        bad = by_name(checks.build(dict(GOOD, log_source="ini", log_stale_nr=9)))["Log"]
        self.assertEqual(bad["state"], "warn")
        self.assertIn("contest #9", bad["text"])

    def test_off_items_left_out(self) -> None:
        items = checks.build(dict(GOOD, scp_enabled=False, web="off", clock_offset=None))
        text = checks.line(items)
        self.assertNotIn("SCP", text)
        self.assertNotIn("Phone", text)
        self.assertNotIn("Clock", text)
        self.assertEqual(checks.worst(items), "ok")

    def test_team_slave_and_http_backup_count_as_rbn(self) -> None:
        for rbn, txt in (("team", "via team master"), ("http", "via HTTP backup")):
            it = by_name(checks.build(dict(GOOD, rbn=rbn)))["RBN"]
            self.assertEqual((it["state"], it["text"]), ("ok", txt))


class ClockTests(unittest.TestCase):
    def test_ntp_first(self) -> None:
        c = checks.ClockCheck(ntp=lambda host: -0.004, fetch=lambda u: self.fail("no web"))
        self.assertAlmostEqual(c.check_once(), -0.004)
        self.assertEqual(c.source, "NTP pool.ntp.org")

    def test_second_ntp_server(self) -> None:
        def ntp(host):
            if host == "pool.ntp.org":
                raise OSError("timed out")
            return 1.5
        c = checks.ClockCheck(ntp=ntp)
        self.assertEqual(c.check_once(), 1.5)
        self.assertEqual(c.source, "NTP time.windows.com")

    def test_web_fallback_counts_the_cache_age(self) -> None:
        import time
        # a cached answer: its Date is 24 s old, Age says so - the PC is right
        old = email.utils.formatdate(time.time() - 24, usegmt=True)
        def no_ntp(host):
            raise OSError("UDP 123 blocked")
        c = checks.ClockCheck(ntp=no_ntp, fetch=lambda url: (old, 24))
        self.assertAlmostEqual(c.check_once(), 0, delta=1.6)
        self.assertEqual(c.source, "web")

    def test_real_sntp_packet_against_a_local_server(self) -> None:
        import socket
        import struct
        import threading
        import time
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]

        def serve():
            data, addr = srv.recvfrom(512)
            now = time.time() + 30 + checks._NTP_DELTA     # server 30 s ahead = PC slow
            sec, frac = int(now), int((now % 1) * 2 ** 32)
            reply = bytearray(48)
            reply[0], reply[1] = 0x24, 2                   # mode 4 (server), stratum 2
            reply[24:32] = data[40:48]                     # originate = client's transmit
            struct.pack_into("!IIII", reply, 32, sec, frac, sec, frac)
            srv.sendto(bytes(reply), addr)
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        try:
            off = checks.ntp_offset("127.0.0.1", port=port, timeout=5)
        finally:
            srv.close()
        self.assertAlmostEqual(off, 30, delta=0.5)

    def test_wording(self) -> None:
        fast = by_name(checks.build(dict(GOOD, clock_offset=-24, clock_source="NTP x")))["Clock"]
        self.assertEqual((fast["state"], fast["text"]), ("warn", "PC 24 s fast"))
        slow = by_name(checks.build(dict(GOOD, clock_offset=3.2, clock_source="NTP x")))["Clock"]
        self.assertEqual((slow["state"], slow["text"]), ("ok", "PC 3 s slow"))
        web = by_name(checks.build(dict(GOOD, clock_offset=8, clock_source="web")))["Clock"]
        self.assertEqual(web["state"], "ok")          # the web is only good to ~1-2 s

    def test_unknown_until_checked(self) -> None:
        self.assertIsNone(checks.ClockCheck(fetch=lambda u: ("", 0)).offset)


class TriedTests(unittest.TestCase):
    def test_tried_window_and_count(self) -> None:
        now = [1000.0]
        with tempfile.TemporaryDirectory() as d:
            taps = TapLog(d, clock=lambda: now[0])
            taps.record("k1abc", 14025.0)
            now[0] += 120
            taps.record("K1ABC", 14026.0)
            now[0] += 60
            self.assertEqual(taps.tried("K1ABC", "20m", 1800), (2, 1120.0))
            self.assertIsNone(taps.tried("K1ABC", "40m", 1800))      # per band
            self.assertEqual(taps.tried("K1ABC", "20m", 90), (1, 1120.0))
            # kept across a restart
            again = TapLog(d, clock=lambda: now[0])
            self.assertEqual(again.tried("K1ABC", "20m", 1800)[0], 2)

    def test_app_text_and_option(self) -> None:
        from tests.test_so2r import _import_app
        app = _import_app()
        f = types.SimpleNamespace(
            cfg=types.SimpleNamespace(mark_tried=True, tried_minutes=30),
            taps=types.SimpleNamespace(tried=lambda c, b, w: (2, app.time.time() - 185)))
        for n in ("_tried", "_tried_fields"):
            setattr(f, n, getattr(app.PouncerApp, n).__get__(f))
        s = app.Spot(dxcall="K1ABC", freq_khz=14025.0, mode="CW", snr=10, wpm=28,
                     spot_type="CQ", spotter="X", time_utc="", received=0)
        self.assertEqual(f._tried(s), (2, 3))
        self.assertEqual(f._tried_fields(s), {"tried": 3, "tries": 2})
        f.cfg.mark_tried = False
        self.assertIsNone(f._tried(s))
        self.assertEqual(f._tried_fields(s), {})


if __name__ == "__main__":
    unittest.main()
