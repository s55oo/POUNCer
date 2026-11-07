"""'Spots: RBN | Telnet': the preferred CW server, fallback both ways, team."""

import queue
import threading
import time
import types
import unittest

from pouncer.rbn import RBNClient


class ClientPreferTests(unittest.TestCase):
    def _client(self):
        return RBNClient("rbn.example", 7000, "S55OO", "", queue.Queue(),
                         backup=("telnet.example", 8000))

    def _run_with(self, c, behaviour, rounds):
        """Run the loop with a fake connect: behaviour(host) raises or returns."""
        seen = []

        def fake(host=None, port=None, backup=False):
            seen.append(host or c.host)
            if len(seen) >= rounds:
                c._stop.set()
            behaviour(host or c.host)
        c._connect_and_read = fake
        c._pause = lambda s: None
        c.run()
        return seen

    def test_prefer_telnet_from_the_start(self) -> None:
        c = self._client()
        c.prefer(True)
        self.assertTrue(c.on_backup and c.prefer_backup)
        seen = self._run_with(c, lambda h: None, 2)
        self.assertEqual(seen[0], "telnet.example")

    def test_telnet_down_falls_back_to_rbn_and_returns(self) -> None:
        c = self._client()
        c.prefer(True)

        def down(host):
            if host == "telnet.example":
                raise ConnectionError("refused")
        seen = self._run_with(c, down, 5)
        # telnet twice, then RBN meanwhile, then telnet again
        self.assertEqual(seen[:4], ["telnet.example", "telnet.example", "rbn.example",
                                    "telnet.example"])

    def test_rbn_preferred_as_before(self) -> None:
        c = self._client()

        def down(host):
            if host == "rbn.example":
                raise ConnectionError("refused")
        seen = self._run_with(c, down, 4)
        self.assertEqual(seen[:3], ["rbn.example", "rbn.example", "telnet.example"])

    def test_no_backup_no_telnet(self) -> None:
        c = RBNClient("rbn.example", 7001, "S55OO", "", queue.Queue())
        c.prefer(True)
        self.assertFalse(c.prefer_backup)

    def test_switch_while_running_closes_the_connection(self) -> None:
        c = self._client()
        closed = []
        c._sock = types.SimpleNamespace(close=lambda: closed.append(1))
        c.is_alive = lambda: True
        c.prefer(True)
        self.assertEqual(closed, [1])
        self.assertTrue(c._switch_now and c._wake.is_set())


class TeamSrcTests(unittest.TestCase):
    def _team(self, started):
        from pouncer.team import Team
        t = Team.__new__(Team)
        t._lock = threading.Lock()
        t.peers = {}
        t.id, t.started = "me", started
        t._on_update = t._on_spot = t._on_runhist = t._on_self = None
        return t

    def test_master_takes_a_slaves_request(self) -> None:
        got = []
        t = self._team(1.0)                        # oldest: the master
        t._on_src = got.append
        t.handle({"t": "hi", "id": "s", "started": 9}, "10.0.0.3")
        t.handle({"t": "src", "id": "s", "v": "telnet"}, "10.0.0.3")
        t.handle({"t": "src", "id": "s", "v": "bogus"}, "10.0.0.3")
        self.assertEqual(got, ["telnet"])

    def test_slave_ignores_and_knows_the_masters_choice(self) -> None:
        got = []
        t = self._team(9.0)
        t._on_src = got.append
        t.handle({"t": "hi", "id": "m", "started": 1, "src": "telnet"}, "10.0.0.2")
        t.handle({"t": "src", "id": "m", "v": "rbn"}, "10.0.0.2")
        self.assertEqual(got, [])
        self.assertEqual(t.master_src(), "telnet")


class AppButtonTests(unittest.TestCase):
    def _fake(self, slave):
        from tests.test_so2r import _import_app
        app = _import_app()
        sent, saved = [], []
        team = types.SimpleNamespace(alive_peers=lambda: [1], is_master=lambda: not slave,
                                     send_src=sent.append)
        f = types.SimpleNamespace(team=team, cfg=types.SimpleNamespace(
            rbn_backup_host="telnet.example", rbn_backup_port=8000, rbn_source="rbn"),
            status_label=types.SimpleNamespace(configure=lambda **k: None),
            _save_setting=lambda k, v: saved.append((k, v)), _src_last=1)
        return app, f, sent, saved

    def test_slave_asks_the_master(self) -> None:
        app, f, sent, saved = self._fake(slave=True)
        app.PouncerApp._set_source(f, "telnet")
        self.assertEqual((sent, saved), (["telnet"], []))

    def test_master_switches_and_saves(self) -> None:
        app, f, sent, saved = self._fake(slave=False)
        app.PouncerApp._set_source(f, "telnet")
        self.assertEqual((sent, saved), ([], [("rbn.source", "telnet")]))

    def test_config(self) -> None:
        import os
        import tempfile
        from pouncer.config import load_config
        d = tempfile.mkdtemp()
        p = os.path.join(d, "c.ini")
        for val, want in (("telnet", "telnet"), ("RBN", "rbn"), ("xyz", "rbn")):
            with open(p, "w") as fh:
                fh.write(f"[rbn]\nsource = {val}\n")
            self.assertEqual(load_config(p).rbn_source, want)


if __name__ == "__main__":
    unittest.main()
