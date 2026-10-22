"""Internet watch: down after 2 silent rounds, back at once, spots kept greyed."""

import time
import types
import unittest

from pouncer.netwatch import NetWatch, minutes_text


class NetWatchTests(unittest.TestCase):
    def test_down_after_two_rounds_back_at_once(self) -> None:
        ok = [True]
        changes = []
        n = NetWatch(connect=lambda h, p, t: ok[0], on_change=changes.append)
        self.assertTrue(n.probe_once())
        ok[0] = False
        n.probe_once()
        self.assertTrue(n.up)                     # one lost round: not an outage
        with self.assertLogs("pouncer.netwatch", "WARNING"):
            n.probe_once()
        self.assertFalse(n.up)
        self.assertIsNotNone(n.down_since)
        n.down_since -= 600
        with self.assertLogs("pouncer.netwatch", "INFO") as cm:
            n.alive()                             # a spot came in: up again
        self.assertTrue(n.up)
        self.assertIn("10 min", cm.output[0])
        self.assertEqual(changes, [False, True])

    def test_any_probe_is_enough(self) -> None:
        n = NetWatch(connect=lambda h, p, t: h == "9.9.9.9")
        self.assertTrue(n.probe_once())

    def test_minutes_text(self) -> None:
        self.assertEqual(minutes_text(5), "5 s")
        self.assertEqual(minutes_text(14 * 60 + 3), "14 min")
        self.assertEqual(minutes_text(72 * 60), "1 h 12 min")


class AppOfflineTests(unittest.TestCase):
    def test_prune_keeps_spots_while_down(self) -> None:
        from tests.test_so2r import _import_app
        app = _import_app()
        down_at = time.time() - 3600
        net = types.SimpleNamespace(up=False, down_since=down_at)
        old = types.SimpleNamespace(received=down_at - 300)     # 5 min before the outage
        older = types.SimpleNamespace(received=down_at - 3600)
        f = types.SimpleNamespace(net=net, spots={"a": old, "b": older}, _dirty=False,
                                  cfg=types.SimpleNamespace(max_age_minutes=15),
                                  _on_freq=types.SimpleNamespace(prune=lambda t: None))
        f._net_down_since = app.PouncerApp._net_down_since.__get__(f)
        app.PouncerApp._prune(f)
        self.assertEqual(list(f.spots), ["a"])    # kept (greyed), the older one aged out
        net.up, net.down_since = True, None
        app.PouncerApp._prune(f)
        self.assertEqual(f.spots, {})             # back: normal ageing

    def test_back_kicks_every_feed(self) -> None:
        from tests.test_so2r import _import_app
        app = _import_app()
        kicked = []
        c = lambda n: types.SimpleNamespace(kick=lambda: kicked.append(n))  # noqa: E731
        f = types.SimpleNamespace(_dirty=False, net=types.SimpleNamespace(back_after=840),
                                  _rbn_clients={7000: c("rbn")}, _ssb_client=c("ssb"),
                                  _self_feed=c("self"),
                                  status_label=types.SimpleNamespace(configure=lambda **k: None))
        app.PouncerApp._on_net_change(f, True)
        self.assertEqual(sorted(kicked), ["rbn", "self", "ssb"])


class ClientKickTests(unittest.TestCase):
    def test_kick_cuts_the_wait(self) -> None:
        from pouncer.rbn import RBNClient
        import queue
        import threading
        c = RBNClient("localhost", 1, "S55OO", "", queue.Queue())
        threading.Timer(0.1, c.kick).start()
        t0 = time.monotonic()
        c._pause(30)
        self.assertLess(time.monotonic() - t0, 2)


if __name__ == "__main__":
    unittest.main()
