"""Spots buttons: each source on / off; Telnet + Cluster on the same node =
one connection; the same spot from two sources once; the relay passes
people's spots only with Cluster on; the hover text names the connection."""

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
    f = types.SimpleNamespace(
        cfg=cfg, team=None, _rbn_clients={}, _http_feed=None, _dxc=None, rbn_status="",
        ssb_status="", status_queue=queue.Queue(), spot_queue=queue.Queue(),
        _ssb_status_q=queue.Queue(), spotter_filter=types.SimpleNamespace(label=lambda: "S53WW"),
        _continent=lambda c: "EU", home_cont="EU", _on_raw_spot=lambda s: None,
        _n1mm_spot_first=None, _n1mm_spot_last=0.0, _rbn_paused=lambda: False,
        _ensure_self_feed=lambda: None, own_call=lambda: "S55OO",
        SOURCE_NAMES=P.SOURCE_NAMES, N1MM_SPOT_BREAK_S=P.N1MM_SPOT_BREAK_S,
        N1MM_SPOT_QUIET_S=P.N1MM_SPOT_QUIET_S, N1MM_SPOT_QUIET_X=P.N1MM_SPOT_QUIET_X,
        MERGE_S=P.MERGE_S)
    for n in ("_ensure_rbn_clients", "_ensure_telnet_client", "_ensure_ssb_client",
              "_ensure_http_feed", "_stop_http_feed", "_start_http_feed", "_ssb_wanted",
              "_cluster_merged", "_people_filter", "_desired_rbn_ports", "_n1mm_spots_ok",
              "_n1mm_quiet_limit", "_ssb_status_put", "_source_tip", "_merged_dup"):
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
        self.assertEqual(f.ssb_status, "off")
        f, made = self.run_with("http")
        self.assertEqual(made, [])
        self.assertIsNotNone(f._http_feed)
        self.assertTrue(f.rbn_status.startswith("standby"))
        f, made = self.run_with("")
        self.assertEqual(made, [])
        self.assertTrue(f.rbn_status.startswith("off"))

    def test_telnet_and_cluster_one_login(self) -> None:
        f, made = self.run_with("telnet,cluster")
        self.assertEqual(len(made), 1)                       # ONE connection to azure
        self.assertEqual((made[0].host, made[0].port, made[0].call),
                         ("azure.s53m.com", 8000, "S55OO"))
        self.assertIsNotNone(made[0].people)                 # it carries people's spots too
        self.assertIsNone(getattr(f, "_ssb_client", None))

    def test_cluster_alone_people_only(self) -> None:
        f, made = self.run_with("rbn,cluster")
        cl = [c for c in made if c.host == "azure.s53m.com"]
        self.assertEqual(len(cl), 1)
        self.assertFalse(cl[0].skimmers)                     # skimmers: RBN's
        self.assertEqual(cl[0].kw.get("modes"), ())          # every mode

    def test_hover_names_the_connection(self) -> None:
        f, made = self.run_with("telnet,cluster")
        t = f._source_tip("telnet")
        self.assertIn("azure.s53m.com:8000", t)
        self.assertIn("ON", t)
        self.assertIn("same connection as Telnet", f._source_tip("cluster"))
        self.assertIn("off", f._source_tip("http"))
        self.assertIn("reversebeacon", f._source_tip("rbn"))

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
