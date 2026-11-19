"""Spots menu: each source on / off; Telnet #1 / #2 / #3 equal, each its own
connection with skimmer AND people's spots, one on the same server as an
earlier one opens nothing; the same spot from two sources once; the hover
text names the connection."""

import queue
import time
import types
import unittest
from unittest import mock

from tests.test_so2r import _import_app

_import_app()

from pouncer.config import load_config  # noqa: E402
from pouncer.rbn import RBNClient, Spot  # noqa: E402


def fake_app(sources):
    app = _import_app()
    P = app.PouncerApp
    cfg = load_config("")
    cfg.rbn_source = sources
    cfg.rbn_callsign = "S55OO"
    made = []

    class FakeClient:
        def __init__(self, host, port, call, *a, **k):
            self.host, self.port, self.call, self.kw = host, port, call, k
            self.skimmers, self.people, self.status_text = True, None, ""
            made.append(self)
            self.stopped = False

        def start(self):
            pass

        def stop(self):
            self.stopped = True

        def share(self, *a):
            pass

        def connected(self):
            return getattr(self, "up", False)
    f = types.SimpleNamespace(
        cfg=cfg, team=None, _rbn_clients={}, _http_feed=None, _dxc=None, rbn_status="",
        status_queue=queue.Queue(), spot_queue=queue.Queue(), _telnet_clients={}, _telnet_same={},
        spotter_filter=types.SimpleNamespace(label=lambda: "S53WW"),
        _continent=lambda c: "EU", home_cont="EU", _on_raw_spot=lambda s: None,
        _n1mm_spot_first=None, _n1mm_spot_last=0.0, _rbn_paused=lambda: False,
        own_call=lambda: "S55OO",
        SOURCE_NAMES=P.SOURCE_NAMES, CONN_KEYS=P.CONN_KEYS, N1MM_SPOT_BREAK_S=P.N1MM_SPOT_BREAK_S,
        MERGE_S=P.MERGE_S, _relay_back=lambda line: None,
        HQ_AFTER_S=P.HQ_AFTER_S, HQ_BACK_S=P.HQ_BACK_S)
    for n in ("_ensure_rbn_clients", "_ensure_telnet_clients", "_telnet_wanted",
              "_ensure_http_feed", "_stop_http_feed", "_start_http_feed",
              "_people_filter", "_desired_rbn_ports", "_source_tip", "_merged_dup", "_conn_tip",
              "_toggle_conn", "_set_source", "_upstream_client",
              "_rbn_healthy", "_ensure_hamqth", "_hq_state",
              "_skimmer_telnet_ok", "_cluster_telnet_ok", "_telnet_states"):
        setattr(f, n, getattr(P, n).__get__(f))
    return app, f, made, FakeClient


class SourceTests(unittest.TestCase):
    def run_with(self, sources):
        app, f, made, Fake = fake_app(sources)
        with mock.patch.object(app, "RBNClient", Fake), \
                mock.patch.object(app, "RBNHttpFeed", lambda *a, **k: types.SimpleNamespace(
                    start=lambda: None, stop=lambda: None, spots_in=0, last_ok=0.0)):
            f._ensure_rbn_clients()
        return f, made

    def test_only_what_is_on(self) -> None:
        f, made = self.run_with("rbn")
        self.assertEqual([(c.host, c.port) for c in made], [("telnet.reversebeacon.net", 7000)])
        self.assertIsNone(f._http_feed)
        self.assertEqual(f._telnet_clients, {})
        f, made = self.run_with("http")
        self.assertEqual(made, [])
        self.assertIsNotNone(f._http_feed)
        self.assertTrue(f.rbn_status.startswith("standby"))
        f, made = self.run_with("")
        self.assertEqual(made, [])
        self.assertTrue(f.rbn_status.startswith("off"))

    def test_three_telnets_each_its_own_connection(self) -> None:
        app, f, made, Fake = fake_app("telnet,telnet2,telnet3,telnet4")
        f.cfg.telnet2_host, f.cfg.telnet2_port, f.cfg.telnet2_login = "dx.example", 7300, "S55OO-2"
        f.cfg.telnet3_host, f.cfg.telnet3_commands = "skim.example", ("set/skimmer",)
        f.cfg.telnet4_host, f.cfg.telnet4_port = "four.example", 23
        with mock.patch.object(app, "RBNClient", Fake):
            f._ensure_rbn_clients()
        self.assertEqual([(c.host, c.port, c.call) for c in made],
                         [("azure.s53m.com", 8000, "S55OO"), ("dx.example", 7300, "S55OO-2"),
                          ("skim.example", 8000, "S55OO"), ("four.example", 23, "S55OO")])
        self.assertTrue(all(c.people is not None and c.skimmers for c in made))   # both kinds
        self.assertEqual([c.kw.get("name") for c in made], ["telnet", "telnet2", "telnet3", "telnet4"])
        self.assertEqual(made[2].kw.get("backup_commands"), ("set/skimmer",))
        self.assertEqual(sorted(f._telnet_clients), [1, 2, 3, 4])
        # #2 and #4 unticked: only their connections close
        f.cfg.rbn_source = "telnet,telnet3"
        with mock.patch.object(app, "RBNClient", Fake):
            f._ensure_rbn_clients()
        self.assertEqual([c.stopped for c in made], [False, True, False, True])
        self.assertEqual(sorted(f._telnet_clients), [1, 3])

    def test_five_telnets_each_its_own_connection(self) -> None:
        # v0.7.235: Telnet #5
        app, f, made, Fake = fake_app("telnet,telnet2,telnet3,telnet4,telnet5")
        f.cfg.telnet2_host, f.cfg.telnet2_port, f.cfg.telnet2_login = "dx.example", 7300, "S55OO-2"
        f.cfg.telnet3_host, f.cfg.telnet3_commands = "skim.example", ("set/skimmer",)
        f.cfg.telnet4_host, f.cfg.telnet4_port = "four.example", 23
        f.cfg.telnet5_host, f.cfg.telnet5_port = "five.example", 41
        with mock.patch.object(app, "RBNClient", Fake):
            f._ensure_rbn_clients()
        self.assertEqual([(c.host, c.port, c.call) for c in made],
                         [("azure.s53m.com", 8000, "S55OO"), ("dx.example", 7300, "S55OO-2"),
                          ("skim.example", 8000, "S55OO"), ("four.example", 23, "S55OO"),
                          ("five.example", 41, "S55OO")])
        self.assertEqual([c.kw.get("name") for c in made],
                         ["telnet", "telnet2", "telnet3", "telnet4", "telnet5"])
        self.assertEqual(sorted(f._telnet_clients), [1, 2, 3, 4, 5])
        # only #5 unticked: only its connection closes
        f.cfg.rbn_source = "telnet,telnet2,telnet3,telnet4"
        with mock.patch.object(app, "RBNClient", Fake):
            f._ensure_rbn_clients()
        self.assertEqual([c.stopped for c in made], [False, False, False, False, True])
        self.assertEqual(sorted(f._telnet_clients), [1, 2, 3, 4])

    def test_same_server_opens_nothing_extra(self) -> None:
        app, f, made, Fake = fake_app("rbn,telnet,telnet2")
        f.cfg.telnet2_host = "AZURE.s53m.com"                # = Telnet #1
        with mock.patch.object(app, "RBNClient", Fake):
            f._ensure_rbn_clients()
        self.assertEqual(sorted(c.host for c in made), ["azure.s53m.com", "telnet.reversebeacon.net"])
        self.assertEqual(f._telnet_same, {2: "Telnet #1"})
        self.assertIn("same server as Telnet #1", f._source_tip("telnet2"))
        self.assertIn((2, "same"), f._telnet_states())

    def test_no_server_cannot_be_ticked(self) -> None:
        app, f, made, Fake = fake_app("rbn")
        saved = []
        f._save_setting = lambda k, v: saved.append((k, v))
        msgs = []
        f.status_label = types.SimpleNamespace(configure=lambda **k: msgs.append(k.get("text")))
        f._src_last = None
        f._set_source("telnet3")                             # [telnet3] host empty
        self.assertEqual(saved, [])
        self.assertIn("Telnet #3 has no server", msgs[-1])
        f._set_source("cluster")                             # an older team member's name
        self.assertEqual(saved, [])                          # = Telnet #2: no server either

    def test_settings_tick_is_the_same_switch_as_the_spots_button(self) -> None:
        # v0.7.227: Settings > Telnet's "enable this connection" tick goes
        # through the very same _set_source as the Spots line's button
        import os
        import shutil
        import tempfile

        app, f, made, Fake = fake_app("rbn,telnet")
        d = tempfile.mkdtemp()
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(d, "config.ini")
        shutil.copy(os.path.join(here, "config.ini"), path)
        f.cfg.config_path = path
        msgs = []
        f.status_label = types.SimpleNamespace(
            configure=lambda **k: msgs.append(k.get("text")),
            cget=lambda *_a: (msgs[-1] if msgs else ""))
        for n in ("_save_setting", "_save_source_tick", "_apply_source"):
            setattr(f, n, getattr(app.PouncerApp, n).__get__(f))
        try:
            with mock.patch.object(app, "RBNClient", Fake), \
                    mock.patch.object(app, "RBNHttpFeed", lambda *a, **k: types.SimpleNamespace(
                        start=lambda: None, stop=lambda: None, spots_in=0, last_ok=0.0)):
                # Telnet #1 ("telnet") is on: tick it off
                res = f._save_source_tick("telnet1.enabled", "telnet", "false")
                self.assertEqual(res, {"ok": True, "key": "telnet1.enabled",
                                       "value": False, "live": True})
                self.assertNotIn("telnet", f.cfg.rbn_source.split(","))
                # tick it back on: it has a default server, so it succeeds
                res = f._save_source_tick("telnet1.enabled", "telnet", "true")
                self.assertEqual(res, {"ok": True, "key": "telnet1.enabled",
                                       "value": True, "live": True})
                self.assertIn("telnet", f.cfg.rbn_source.split(","))
                # already on: a no-op, nothing touched
                before = f.cfg.rbn_source
                res = f._save_source_tick("telnet1.enabled", "telnet", "true")
                self.assertEqual(res, {"ok": True, "key": "telnet1.enabled",
                                       "value": True, "live": True})
                self.assertEqual(f.cfg.rbn_source, before)
                # Telnet #3 has no server: refused, same reason as the Spots button
                res = f._save_source_tick("telnet3.enabled", "telnet3", "true")
                self.assertFalse(res["ok"])
                self.assertIn("Telnet #3 has no server", res["error"])
                self.assertNotIn("telnet3", f.cfg.rbn_source.split(","))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_hover_names_the_connection(self) -> None:
        f, made = self.run_with("telnet")
        t = f._source_tip("telnet")
        self.assertIn("azure.s53m.com:8000", t)
        self.assertIn("ON", t)
        self.assertIn("skimmers ('-#') and people", t)
        self.assertIn("no server", f._source_tip("telnet2"))
        self.assertIn("off", f._source_tip("http"))
        self.assertIn("reversebeacon", f._source_tip("rbn"))

    def test_relay_button_and_no_mycall(self) -> None:
        f, made = self.run_with("rbn")
        saved = []
        f._save_setting = lambda k, v: (saved.append((k, v)),
                                        setattr(f.cfg, {"cluster.relay": "cluster_relay"}[k], v))
        f.status_label = types.SimpleNamespace(configure=lambda **k: None)
        f._src_last = 1
        self.assertNotIn("mycall", f.CONN_KEYS)              # v0.7.214: no MyCall button
        self.assertIn("cluster server is off", f._source_tip("relay"))
        self.assertIn("Opens no connection", f._source_tip("relay"))
        f._set_source("relay")
        self.assertEqual(saved[-1][0], "cluster.relay")
        self.assertEqual([c.host for c in made], ["telnet.reversebeacon.net"])   # nothing else

    def test_hamqth_only_while_telnet_is_down(self) -> None:
        app, f, made, Fake = fake_app("rbn,telnet")
        f.cfg.rbn_hamqth_backup = True
        started = []

        class FakeHQ:
            def __init__(self, kind, *a, **k):
                self.kind, self.stopped, self.status_text = kind, False, "ok"
                started.append(self)

            def start(self):
                pass

            def stop(self):
                self.stopped = True
        now = [1000.0]
        with mock.patch.object(app, "RBNClient", Fake), mock.patch.object(app, "HamQTHFeed", FakeHQ), \
                mock.patch.object(app.time, "time", side_effect=lambda: now[0]):
            f._ensure_rbn_clients()                       # telnets not up yet
            self.assertEqual(started, [])
            now[0] += 61                                  # down a minute: HamQTH for both
            f._ensure_rbn_clients()
            self.assertEqual(sorted(x.kind for x in started), ["rbn", "spots"])
            self.assertIn("ACTIVE", f._source_tip("hamqth"))
            self.assertIn("HamQTH.com", f._source_tip("hamqth"))
            for c in made:                                # telnets back
                c.up, c._last_dx = True, app.time.monotonic()
            f._ensure_rbn_clients()
            self.assertFalse(any(x.stopped for x in started))     # not at once
            now[0] += 61
            for c in made:
                c._last_dx = app.time.monotonic()
            f._ensure_rbn_clients()
            self.assertTrue(all(x.stopped for x in started))      # a minute later
        f2 = fake_app("rbn")[1]
        self.assertIn("off", f2._source_tip("hamqth"))
        # everything else off: HamQTH is the source itself, at once
        app3, f3, made3, Fake3 = fake_app("")
        f3.cfg.rbn_hamqth_backup = True
        started.clear()
        with mock.patch.object(app3, "RBNClient", Fake3), mock.patch.object(app3, "HamQTHFeed", FakeHQ):
            f3._ensure_rbn_clients()
        self.assertEqual(sorted(x.kind for x in started), ["rbn", "spots"])
        self.assertEqual(made3, [])

    def test_same_spot_from_two_sources_once(self) -> None:
        f, made = self.run_with("rbn")
        a = Spot(dxcall="DL1ABC", freq_khz=14025.0, mode="CW", snr=20, wpm=28, spot_type="CQ",
                 spotter="S53WW-#", time_utc="1200Z", received=time.time())
        self.assertFalse(f._merged_dup(a))
        self.assertTrue(f._merged_dup(a))                    # HTTP's copy
        b = Spot(dxcall="DL1ABC", freq_khz=14025.0, mode="CW", snr=20, wpm=28, spot_type="CQ",
                 spotter="DL8LAS-#", time_utc="1200Z", received=time.time())
        self.assertFalse(f._merged_dup(b))                   # another skimmer: its own


class ClientPeopleTests(unittest.TestCase):
    def test_one_connection_both_kinds(self) -> None:
        q = queue.Queue()
        c = RBNClient("h", 1, "S55OO", "", q)
        c.people = types.SimpleNamespace(accept=lambda sp: True)
        c._consume("DX de S53WW-#:   14025.0  DL1ABC       CW    23 dB  28 WPM  CQ      1200Z\n"
                   "DX de S50ARX:    14205.0  EA6RJU       59 tnx                  1201Z\n", "")
        got = [q.get_nowait() for _ in range(q.qsize())]
        self.assertEqual([(s.dxcall, s.source) for s in got], [("DL1ABC", ""), ("EA6RJU", "cluster")])
        self.assertEqual(got[1].mode, "SSB")
        c.skimmers = False                                   # Telnet off, Cluster on
        c._consume("DX de S53WW-#:   14030.0  F5BY         CW    23 dB  28 WPM  CQ      1200Z\n", "")
        self.assertTrue(q.empty())

    def test_people_only_client_skips_skimmers(self) -> None:
        q = queue.Queue()
        c = RBNClient("h", 1, "S55OO", "", q, kind="cluster")
        c.skimmers = False
        c._consume("DX de S53WW-#:   14025.0  DL1ABC       CW    23 dB  28 WPM  CQ      1200Z\n"
                   "DX de S50ARX:    14012.0  EA6RJU       cw tnx                  1201Z\n", "")
        got = [q.get_nowait() for _ in range(q.qsize())]
        self.assertEqual([s.dxcall for s in got], ["EA6RJU"])


if __name__ == "__main__":
    unittest.main()
