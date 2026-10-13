"""Pre-contest self-test checks (pure functions, no N1MM / Windows needed)."""

import socket
import threading
import unittest
from unittest import mock

from pouncer import selftest as st


class FakeServer:
    """A telnet-like server that sends ``greeting`` and closes."""

    def __init__(self, greeting: bytes) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.got = b""
        self.greeting = greeting
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        conn, _ = self.sock.accept()
        conn.sendall(self.greeting)
        conn.settimeout(1)
        try:
            self.got = conn.recv(100)
        except OSError:
            pass
        conn.close()

    def close(self) -> None:
        self.sock.close()


class ProbeTests(unittest.TestCase):
    def test_asks_for_call_and_we_do_not_log_in(self) -> None:
        srv = FakeServer(b"Welcome\r\nPlease enter your call: ")
        try:
            status, detail = st.probe("127.0.0.1", srv.port, timeout=3)
        finally:
            srv.close()
        self.assertEqual(status, st.PASS)
        self.assertIn("asks for the call", detail)
        self.assertEqual(srv.got, b"")          # never sent a login

    def test_unreachable(self) -> None:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                                # nobody listens there
        status, detail = st.probe("127.0.0.1", port, timeout=1)
        self.assertEqual(status, st.FAIL)
        self.assertIn("not reachable", detail)

    def test_not_set(self) -> None:
        self.assertEqual(st.probe("", 8000)[0], st.SKIP)


class CheckTests(unittest.TestCase):
    def test_radios(self) -> None:
        self.assertEqual(st.check_radios({})[0], st.FAIL)
        ok = st.check_radios({1: {"freq_khz": 14025.0, "mode": "CW", "entry_window_hwnd": 1},
                              2: {"freq_khz": 7012.0, "mode": "CW", "entry_window_hwnd": 2}})
        self.assertEqual(ok, (st.PASS, "A 14025.0 CW, B 7012.0 CW"))
        warn = st.check_radios({1: {"freq_khz": 14025.0, "mode": "CW"}})
        self.assertEqual(warn[0], st.WARN)
        self.assertIn("SO1R", warn[1])

    def test_feed_and_history(self) -> None:
        self.assertEqual(st.check_feed("logged in as S55OO", 30, "off")[0], st.PASS)
        self.assertEqual(st.check_feed("logged in as S55OO", 30, "off", on_backup=True)[0], st.WARN)
        self.assertEqual(st.check_feed("logged in as S55OO", None, "off")[0], st.WARN)
        self.assertEqual(st.check_feed("disconnected, reconnecting", None, "off")[0], st.FAIL)
        self.assertEqual(st.check_feed("", None, "slave")[0], st.PASS)
        self.assertEqual(st.check_history(30, 30, "off")[0], st.PASS)
        self.assertEqual(st.check_history(30, 0, "off")[0], st.WARN)
        self.assertEqual(st.check_history(0, 0, "off")[0], st.SKIP)

    def test_login_clash(self) -> None:
        clash = st.check_login_clash("S55OO", "azure.s53m.com", 8000,
                                     "azure.s53m.com", 8000, "", "auto")
        self.assertEqual(clash[0], st.WARN)
        self.assertIn("S55OO-2", clash[1])
        self.assertEqual(st.check_login_clash("S55OO", "azure.s53m.com", 8000,
                                              "azure.s53m.com", 8000, "S55OO-2", "auto")[0], st.PASS)
        self.assertEqual(st.check_login_clash("S55OO", "a", 8000, "a", 8000, "", "off")[0], st.SKIP)

    def test_decimal(self) -> None:
        self.assertEqual(st.check_decimal(",", "14025,3")[0], st.PASS)
        self.assertEqual(st.check_decimal("?", "14025.3")[0], st.WARN)


class BoxTests(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch("pouncer.selftest.time.sleep")
        p.start()
        self.addCleanup(p.stop)

    def test_puts_back_what_was_there(self) -> None:
        box = {"t": "S53M"}
        status, detail = st.box_roundtrip(lambda: box["t"],
                                          lambda t: box.update(t=t) or True)
        self.assertEqual(status, st.PASS)
        self.assertEqual(box["t"], "S53M")
        self.assertIn('held "S53M"', detail)

    def test_wrong_text(self) -> None:
        box = {"t": ""}

        def set_text(t):
            box["t"] = "P" + t if t else t      # N1MM mangled it
            return True
        status, _ = st.box_roundtrip(lambda: box["t"], set_text)
        self.assertEqual(status, st.FAIL)
        self.assertEqual(box["t"], "")

    def test_no_box(self) -> None:
        self.assertEqual(st.box_roundtrip(lambda: None, lambda t: True)[0], st.FAIL)


class QsyTests(unittest.TestCase):
    def _clock(self):
        t = [0.0]

        def clock():
            return t[0]

        def sleep(s):
            t[0] += s
        return clock, sleep

    def test_up_and_back(self) -> None:
        clock, sleep = self._clock()
        radio = {"f": 14025.0}
        status, detail = st.qsy_roundtrip(lambda: radio["f"],
                                          lambda k: radio.update(f=k) or True,
                                          14025.0, sleep=sleep, clock=clock)
        self.assertEqual(status, st.PASS)
        self.assertEqual(radio["f"], 14025.0)
        self.assertIn("14026.0", detail)

    def test_radio_does_not_move(self) -> None:
        clock, sleep = self._clock()
        status, _ = st.qsy_roundtrip(lambda: 14025.0, lambda k: True, 14025.0,
                                     wait_s=1, sleep=sleep, clock=clock)
        self.assertEqual(status, st.FAIL)

    def test_stuck_up(self) -> None:
        clock, sleep = self._clock()
        radio = {"f": 14025.0}

        def move(k):
            if k > 14025.5:
                radio["f"] = k
            return True
        status, detail = st.qsy_roundtrip(lambda: radio["f"], move, 14025.0,
                                          wait_s=1, sleep=sleep, clock=clock)
        self.assertEqual(status, st.FAIL)
        self.assertIn("check the radio", detail)


class RunTests(unittest.TestCase):
    def test_run_summary_and_text(self) -> None:
        seen = []

        def boom():
            raise RuntimeError("x")
        res = st.run([("a", lambda: (st.PASS, "fine")), ("b", boom),
                      ("c", lambda: (st.WARN, "hm"))], lambda *r: seen.append(r))
        self.assertEqual([r[1] for r in res], [st.PASS, st.FAIL, st.WARN])
        self.assertEqual(len(seen), 3)
        self.assertTrue(st.summary(res).startswith("1 FAILED"))
        text = st.as_text(res, "0.7.29")
        self.assertIn("[   OK] a: fine", text)
        self.assertIn("[ FAIL] b: error", text)
        self.assertEqual(st.summary([("a", st.PASS, "")]), "all OK (1 OK, 0 check, 0 failed, 0 skipped)")


if __name__ == "__main__":
    unittest.main()
