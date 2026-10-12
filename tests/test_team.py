"""Tests for the POUNCer team: discovery, master election, sharing."""

import time
import unittest

from pouncer import team as teammod
from pouncer.team import Team, elect
from pouncer.web import WebServer


def wait_for(cond, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class ElectTests(unittest.TestCase):
    def test_earliest_alive_healthy_wins(self) -> None:
        now = 1000.0
        nodes = {
            "b": {"started": 10, "seen": now, "healthy": True},
            "a": {"started": 20, "seen": now, "healthy": True},
        }
        self.assertEqual(elect(nodes, now), "b")                # ran first
        nodes["b"]["seen"] = now - 10                           # b gone
        self.assertEqual(elect(nodes, now), "a")
        nodes["b"]["seen"] = now
        nodes["b"]["healthy"] = False                           # b's RBN down
        self.assertEqual(elect(nodes, now), "a")
        nodes["a"]["healthy"] = False                           # nobody healthy
        self.assertEqual(elect(nodes, now), "b")
        self.assertIsNone(elect({}, now))


class TeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = teammod.ANNOUNCE_S
        teammod.ANNOUNCE_S = 0.2
        self.spots_b: list[dict] = []
        self.learned_a: list[dict] = []
        self.learned_b: list[dict] = []
        self.a = Team(8081, on_runhist=self.learned_a.append, port=18101,
                      send_to=[("127.0.0.1", 18102), ("127.0.0.1", 18103)])
        self.a.started -= 100            # A has been running longest
        self.b = Team(0, on_spot=self.spots_b.append, on_runhist=self.learned_b.append,
                      port=18102, send_to=[("127.0.0.1", 18101), ("127.0.0.1", 18103)])
        self.b._synced = True            # (no web server for A in this test)
        self.a.start()
        self.b.start()

    def tearDown(self) -> None:
        self.a.stop()
        self.b.stop()
        teammod.ANNOUNCE_S = self._old

    def test_discovery_roles_and_sharing(self) -> None:
        self.assertTrue(wait_for(lambda: self.a.alive_peers() and self.b.alive_peers()))
        self.assertEqual(self.a.role(), "master")
        self.assertEqual(self.b.role(), "slave")
        self.assertEqual(self.b.master_ip(), "127.0.0.1")
        # master passes spots on; a slave's spots are ignored
        self.a.send_spot({"dxcall": "K1ABC", "freq_khz": 7012.0})
        self.b.send_spot({"dxcall": "X1X", "freq_khz": 7001.0})
        self.assertTrue(wait_for(lambda: self.spots_b))
        time.sleep(0.3)
        self.assertEqual([s["dxcall"] for s in self.spots_b], ["K1ABC"])
        # anything one learns, the others learn
        self.b.send_runhist("M0KTZ|CQWW|CW", {"run": 6, "hours": 3.5, "rec": True})
        self.assertTrue(wait_for(lambda: self.learned_a))
        self.assertEqual(self.learned_a[0]["M0KTZ|CQWW|CW"]["run"], 6)

    def test_slave_takes_over_when_master_stops(self) -> None:
        self.assertTrue(wait_for(lambda: self.b.role() == "slave"))
        old = teammod.PEER_TIMEOUT_S
        teammod.PEER_TIMEOUT_S = 0.8
        try:
            self.a.stop()
            self.assertTrue(wait_for(lambda: self.b.role() == "solo", 4))
            self.assertTrue(self.b.is_master())
        finally:
            teammod.PEER_TIMEOUT_S = old


class JoinSyncTests(unittest.TestCase):
    def test_joiner_fetches_known_runhist_from_master(self) -> None:
        old = teammod.ANNOUNCE_S
        teammod.ANNOUNCE_S = 0.2
        web = WebServer("127.0.0.1", 0, lambda: {}, lambda *a: None, lambda k: None)
        web.set_runhist_provider(lambda: {"S50R|CQWW|CW": {"run": 82, "hours": 23.8}})
        web.start()
        got: list[dict] = []
        a = Team(web.port, port=18111, send_to=[("127.0.0.1", 18112)])
        a.started -= 100
        b = Team(0, on_runhist=got.append, port=18112, send_to=[("127.0.0.1", 18111)])
        try:
            a.start()
            b.start()
            self.assertTrue(wait_for(lambda: got))
            self.assertEqual(got[0]["S50R|CQWW|CW"]["run"], 82)
        finally:
            a.stop()
            b.stop()
            web.stop()
            teammod.ANNOUNCE_S = old


if __name__ == "__main__":
    unittest.main()
