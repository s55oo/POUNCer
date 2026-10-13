import unittest

from pouncer.rbn import TelnetFilter, is_beacon, parse_spot, ports_for_modes


class PortsForModesTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(ports_for_modes(("CW",), 7000, 7001), {7000})
        self.assertEqual(ports_for_modes(("RTTY",), 7000, 7001), {7000})
        self.assertEqual(ports_for_modes(("FT8", "FT4"), 7000, 7001), {7001})
        self.assertEqual(ports_for_modes(("CW", "FT8"), 7000, 7001), {7000, 7001})
        self.assertEqual(ports_for_modes((), 7000, 7001), set())


class ParseSpotTests(unittest.TestCase):
    def test_cw_spot(self):
        line = "DX de S53WW-#:     14014.0  K1ABC          CW    13 dB  25 WPM  CQ      1135Z"
        spot = parse_spot(line)
        self.assertIsNotNone(spot)
        self.assertEqual(spot.spotter, "S53WW-#")
        self.assertEqual(spot.dxcall, "K1ABC")
        self.assertEqual(spot.freq_khz, 14014.0)
        self.assertEqual(spot.mode, "CW")
        self.assertEqual(spot.snr, 13)
        self.assertEqual(spot.wpm, 25)
        self.assertEqual(spot.spot_type, "CQ")
        self.assertEqual(spot.time_utc, "1135Z")

    def test_ft8_spot_without_wpm(self):
        line = "DX de S53WW-#:     14074.0  DL1XYZ         FT8   -5 dB            CQ      1200Z"
        spot = parse_spot(line)
        self.assertIsNotNone(spot)
        self.assertEqual(spot.snr, -5)
        self.assertIsNone(spot.wpm)
        self.assertEqual(spot.mode, "FT8")

    def test_non_spot_lines_ignored(self):
        self.assertIsNone(parse_spot("Please enter your call:"))
        self.assertIsNone(parse_spot("DX de S53WW-#: 14014.0"))


class TelnetFilterTests(unittest.TestCase):
    def test_strips_iac_and_replies(self):
        f = TelnetFilter()
        data, reply = f.feed(bytes([255, 251, 1, 65, 66]))
        self.assertEqual(data, b"AB")
        self.assertEqual(reply, bytes([255, 254, 1]))

    def test_escaped_iac_passes_through(self):
        f = TelnetFilter()
        data, reply = f.feed(bytes([255, 255]))
        self.assertEqual(data, bytes([255]))
        self.assertEqual(reply, b"")


class BeaconTests(unittest.TestCase):
    def test_beacon_spots_detected(self) -> None:
        for line in (
            "DX de S53WW-#:  14100.0  4U1UN  CW  20 dB  22 WPM  NCDXF B  1135Z",
            "DX de S53WW-#:  28205.0  DK0TEN  CW  15 dB  12 WPM  BEACON  1135Z",
            "DX de S53WW-#:  50050.0  OH6/B  CW  10 dB  14 WPM  CQ  1135Z",
        ):
            spot = parse_spot(line)
            self.assertIsNotNone(spot, line)
            self.assertTrue(is_beacon(spot), line)

    def test_normal_cq_spot_is_not_a_beacon(self) -> None:
        spot = parse_spot("DX de S53WW-#:  14014.0  K1ABC  CW  13 dB  25 WPM  CQ  1135Z")
        self.assertFalse(is_beacon(spot))


class OnFreqTrackerTests(unittest.TestCase):
    def _spot(self, freq, t, call="M0KTZ", kind="CQ"):
        from pouncer.rbn import Spot
        return Spot(dxcall=call, freq_khz=freq, mode="CW", snr=10, wpm=25,
                    spot_type=kind, spotter="S53WW-#", time_utc="1200Z", received=t)

    def test_time_on_frequency(self) -> None:
        from pouncer.rbn import OnFreqTracker
        tr = OnFreqTracker(stay_khz=0.5, gap_s=600)
        tr.add(self._spot(7012.0, 1000), "40m")
        tr.add(self._spot(7012.1, 1240), "40m")   # drift 0.1 kHz: still there
        self.assertEqual(tr.minutes("M0KTZ", "40m", 7012.1, 1300), 5)
        self.assertEqual(tr.first_heard("M0KTZ", "40m", 7012.1), 1000)
        tr.add(self._spot(7018.0, 1400), "40m")   # moved: starts again
        self.assertIsNone(tr.minutes("M0KTZ", "40m", 7012.1, 1500))
        self.assertEqual(tr.minutes("m0ktz", "40m", 7018.0, 1520), 2)
        tr.add(self._spot(7018.0, 2500), "40m")   # silent > 10 min: new start
        self.assertEqual(tr.minutes("M0KTZ", "40m", 7018.0, 2560), 1)
        tr.prune(3200)
        self.assertIsNone(tr.minutes("M0KTZ", "40m", 7018.0, 3300))

    def test_beacons_ignored(self) -> None:
        from pouncer.rbn import OnFreqTracker
        tr = OnFreqTracker()
        tr.add(self._spot(14100.0, 0, call="4U1UN", kind="NCDXF"), "20m")
        self.assertIsNone(tr.minutes("4U1UN", "20m", 14100.0, 60))


class SpotterFilterTests(unittest.TestCase):
    def test_backup_skimmers_while_primary_is_quiet(self) -> None:
        from pouncer.rbn import SpotterFilter
        now = [1000.0]
        cont = {"S50ARX": "EU", "OE9GHV": "EU", "W3LPL": "NA"}
        f = SpotterFilter("S53WW", continent_of=cont.get, home_cont=lambda: "EU",
                          quiet_s=300, clock=lambda: now[0])
        self.assertTrue(f.accept("S53WW-#"))
        self.assertFalse(f.accept("S50ARX-#"))       # primary alive: only S53WW
        now[0] += 301                                 # S53WW quiet 5 min
        self.assertTrue(f.backup_active())
        self.assertTrue(f.accept("S50ARX-#"))         # same continent
        self.assertTrue(f.accept("OE9GHV-#"))
        self.assertFalse(f.accept("W3LPL-#"))         # other continent
        self.assertTrue(f.accept("S53WW-#"))          # back again
        self.assertFalse(f.backup_active())
        self.assertFalse(f.accept("S50ARX-#"))

    def test_listed_backups_only(self) -> None:
        from pouncer.rbn import SpotterFilter
        now = [0.0]
        f = SpotterFilter("S53WW", backups=["OE9GHV"], continent_of=lambda c: "EU",
                          quiet_s=60, clock=lambda: now[0])
        now[0] = 61
        self.assertTrue(f.accept("OE9GHV-#"))
        self.assertFalse(f.accept("S50ARX-#"))       # not in the list

    def test_no_primary_accepts_all(self) -> None:
        from pouncer.rbn import SpotterFilter
        self.assertTrue(SpotterFilter("").accept("ANY-#"))


class HistoryTests(unittest.TestCase):
    def test_parse_sh_dx_line(self) -> None:
        import calendar
        from pouncer.rbn import parse_history
        now = calendar.timegm((2026, 10, 13, 12, 5, 0))
        s = parse_history("  14025.0  K1ABC        13-Oct-2026 1159Z  CW 23 dB 28 WPM CQ          <S53WW-#>",
                          now)
        self.assertEqual((s.dxcall, s.freq_khz, s.mode, s.snr, s.wpm, s.spot_type, s.spotter),
                         ("K1ABC", 14025.0, "CW", 23, 28, "CQ", "S53WW-#"))
        self.assertEqual(now - s.received, 6 * 60)          # its own time: ages as usual
        self.assertIsNone(parse_history("DX de S53WW-#: 14025.0 K1ABC CW 23 dB 28 WPM CQ 1159Z"))
        self.assertIsNone(parse_history("S55OO de RBN 13-Oct-2026 1205Z >"))

    def test_client_asks_for_history_after_login(self) -> None:
        import queue
        import socket
        import threading
        from pouncer.rbn import RBNClient, SpotterFilter
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        got_cmds, q = [], queue.Queue()

        def server():
            c, _ = srv.accept()
            c.sendall(b"Please enter your call: ")
            buf = b""
            while b"sh/dx" not in buf:
                data = c.recv(1024)
                if not data:
                    return
                buf += data
            got_cmds.append(buf.decode())
            c.sendall(b"  14025.0  K1ABC        13-Oct-2026 1159Z  CW 23 dB 28 WPM CQ   <S53WW-#>\r\n"
                      b"  14030.0  W1AW         13-Oct-2026 1158Z  CW 11 dB 25 WPM CQ   <DL1XX-#>\r\n")
            import time as _t
            _t.sleep(1)
            c.close()

        th = threading.Thread(target=server, daemon=True)
        th.start()
        client = RBNClient("127.0.0.1", srv.getsockname()[1], "S55OO",
                           SpotterFilter("S53WW"), q, history=30)
        client.start()
        th.join(10)
        client.stop()
        srv.close()
        self.assertTrue(got_cmds and "S55OO" in got_cmds[0])
        self.assertIn("sh/dx/100 spotter=S53WW-#", got_cmds[0])
        spots = [q.get(timeout=2)]
        self.assertEqual(spots[0].dxcall, "K1ABC")           # DL1XX's filtered out
        self.assertTrue(q.empty())


class BackupServerTests(unittest.TestCase):
    def test_switches_to_backup_when_rbn_is_down_and_back_later(self) -> None:
        import queue
        import socket
        import threading
        from pouncer.rbn import RBNClient, SpotterFilter
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()                                  # RBN "down": refused
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(2)
        got = []

        def backup_server():
            c, _ = srv.accept()
            c.sendall(b"Please enter your call: ")
            buf = b""
            while b"S55OO" not in buf:
                buf += c.recv(1024)
            got.append(buf)
            c.sendall(b"DX de S53WW-#:   14025.0  K1ABC  CW 23 dB 28 WPM CQ  1200Z\r\n")
            import time as _t
            _t.sleep(0.5)
            got.append(c.recv(1024))                  # set/skimmer
            _t.sleep(3)
            c.close()

        th = threading.Thread(target=backup_server, daemon=True)
        th.start()
        statuses, q = [], queue.Queue()
        client = RBNClient("127.0.0.1", dead_port, "S55OO", SpotterFilter("S53WW"), q,
                           on_status=statuses.append, history=0,
                           backup=("127.0.0.1", srv.getsockname()[1]),
                           backup_commands=("set/skimmer",), retry_primary_s=6)
        client.start()
        try:
            spot = q.get(timeout=20)
            self.assertEqual(spot.dxcall, "K1ABC")
            self.assertTrue(any("switching to backup" in x for x in statuses))
            self.assertTrue(any(x.startswith("logged in") and "backup" in x for x in statuses))
            th.join(10)
            self.assertIn(b"set/skimmer", b"".join(got))
            deadline = __import__("time").time() + 10
            while not any("trying RBN again" in x for x in statuses) and __import__("time").time() < deadline:
                __import__("time").sleep(0.1)
            self.assertTrue(any("trying RBN again" in x for x in statuses))
        finally:
            client.stop()
            srv.close()

    def test_light_says_backup(self) -> None:
        from pouncer.lights import OK, rbn_light
        self.assertEqual(rbn_light("logged in as S55OO (backup azure.s53m.com)", None, "solo"),
                         (OK, "RBN via backup"))
        self.assertEqual(rbn_light("logged in as S55OO", None, "solo"), (OK, "RBN"))


class SsbClusterTests(unittest.TestCase):
    def test_parse_human_spot_and_guess_mode(self) -> None:
        from pouncer.rbn import guess_mode, parse_cluster_spot
        s = parse_cluster_spot("DX de S50ABC:     14205.0  K1ABC        59 tnx QSO                 1200Z JN76")
        self.assertEqual((s.spotter, s.freq_khz, s.dxcall, s.mode, s.time_utc),
                         ("S50ABC", 14205.0, "K1ABC", "SSB", "1200Z"))
        s = parse_cluster_spot("DX de DL1XX-#:   14025.0  W1AW         CW 22 dB 25 WPM CQ          1201Z")
        self.assertEqual(s.mode, "CW")
        self.assertEqual(guess_mode(7012.0), "CW")
        self.assertEqual(guess_mode(7150.0), "SSB")
        self.assertEqual(guess_mode(14074.0, "FT8 -12dB"), "FT8")
        self.assertEqual(guess_mode(14020.0, "lsb up 5"), "SSB")       # the comment wins
        self.assertIsNone(parse_cluster_spot("S55OO de S53M-8 >"))

    def test_continent_filter(self) -> None:
        from pouncer.rbn import ContinentFilter
        cont = {"S50ABC": "EU", "K1TTT": "NA"}.get
        f = ContinentFilter(cont, lambda: "EU", "EU")
        self.assertTrue(f.accept("S50ABC-2"))
        self.assertFalse(f.accept("K1TTT"))
        self.assertTrue(ContinentFilter(cont, lambda: "EU", "ALL").accept("K1TTT"))

    def test_cluster_client_keeps_eu_ssb_spots_only(self) -> None:
        import queue
        import socket
        import threading
        import time as _t
        from pouncer.rbn import ContinentFilter, RBNClient
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        cmds = []

        def server():
            c, _ = srv.accept()
            c.sendall(b"login: \r\nPlease enter your call: ")
            buf = b""
            while b"sh/dx" not in buf:
                data = c.recv(1024)
                if not data:
                    return
                buf += data
            cmds.append(buf.decode())
            c.sendall(b"DX de S50ABC:     14205.0  K1ABC        59 tnx QSO      1200Z\r\n"
                      b"DX de K1TTT:      14210.0  W1AW         loud in NA      1200Z\r\n"
                      b"DX de S51XX:      14020.0  OK1RR        cw              1200Z\r\n")
            _t.sleep(1)
            c.close()

        th = threading.Thread(target=server, daemon=True)
        th.start()
        q = queue.Queue()
        cont = {"S50ABC": "EU", "K1TTT": "NA", "S51XX": "EU"}.get
        client = RBNClient("127.0.0.1", srv.getsockname()[1], "S55OO",
                           ContinentFilter(cont, lambda: "EU", "EU"), q, history=30,
                           kind="cluster", modes=("SSB",))
        client.backup_commands = ("set/nobeep",)
        client.start()
        th.join(15)
        client.stop()
        srv.close()
        self.assertIn("set/nobeep", cmds[0])
        self.assertIn("sh/dx/30", cmds[0])
        got = []
        while not q.empty():
            got.append(q.get())
        self.assertEqual([(s.dxcall, s.mode) for s in got], [("K1ABC", "SSB")])


if __name__ == "__main__":
    unittest.main()
