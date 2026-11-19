"""Traffic console: the raw lines of the spots POUNCer shows are yellow."""

import time
import types
import unittest

from pouncer import traffic
from pouncer.rbn import parse_spot

LINE = "DX de EA8BFK-#:   14025.0  DL1ABC         CW    24 dB  28 WPM  CQ      1802Z"
OTHER_SKIMMER = "DX de W3LPL-#:    14025.0  DL1ABC         CW    18 dB  28 WPM  CQ      1802Z"


def _app(zones=(14, 15), spotted=None, clock=None):
    """A stand-in app with the real Spotters filter: skimmers in the zones."""
    from tests.test_so2r import _import_app
    from pouncer.rbn import SpotterFilter
    P = _import_app().PouncerApp
    zone = {"EA8BFK": 33, "DL1ABC": 14, "W3LPL": 5, "F5ABC": 14, "S50ABC": 15}
    kw = {"clock": clock} if clock is not None else {}
    sf = SpotterFilter("", zone_of=lambda c: zone.get(c), continent_of=lambda c: "EU",
                       home_cont=lambda: "EU", quiet_s=300, **kw)
    sf.configure("zones", zones=zones)
    f = types.SimpleNamespace(spotter_filter=sf, spotted_filter=spotted,
                              cfg=types.SimpleNamespace(hide_beacons=True, follow_modes=("CW",)))
    f.pskr = None
    f.CONSOLE_PSK_WAIT_S = P.CONSOLE_PSK_WAIT_S
    for name in ("_console_spot", "_console_shown", "_digi_wanted", "_console_psk"):
        setattr(f, name, getattr(P, name).__get__(f))
    return f


IN_ZONE = "DX de DL1ABC-#:   14025.0  K1ABC          CW    24 dB  28 WPM  CQ      1802Z"
OUT_ZONE = "DX de W3LPL-#:    14025.0  K1ABC          CW    18 dB  28 WPM  CQ      1802Z"


class ShownTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.f = _app()
        except Exception as exc:  # noqa: BLE001 - the app needs tkinter
            self.skipTest(f"app not importable here: {exc}")

    def test_spotter_zones_decide(self) -> None:
        f = self.f
        self.assertIsNone(f._console_shown("telnet", "Hello S55OO, this is a node"))
        self.assertTrue(f._console_shown("telnet", IN_ZONE))          # skimmer in zone 14
        self.assertFalse(f._console_shown("rbn", OUT_ZONE))           # zone 5: filtered out
        self.assertEqual(f._console_shown("rbn", OUT_ZONE), "EXCL SPOTTER")
        self.assertTrue(f._console_shown("telnet", "S55OO de NODE >" + IN_ZONE))
        # every source: HamQTH, the HTTP feed (JSON), N1MM's spot packets
        self.assertTrue(f._console_shown("HAMQTH", "DL1ABC-# 14025.0 K1ABC CW 24"))
        self.assertFalse(f._console_shown("HAMQTH", "W3LPL-# 14025.0 K1ABC CW 18"))
        self.assertTrue(f._console_shown(
            "N1MM 127.0.0.1", "<spot><dxcall>K1ABC</dxcall><frequency>1420500</frequency>"
            "<spottercall>F5ABC</spottercall><action>add</action></spot>"))

    def test_spotted_filter_and_beacons(self) -> None:
        f = _app(spotted=types.SimpleNamespace(accept=lambda call: call.startswith("K1")))
        self.assertTrue(f._console_shown("telnet", IN_ZONE))
        other = IN_ZONE.replace("K1ABC", "G3XYZ")
        self.assertFalse(f._console_shown("telnet", other))          # not a Spotted station
        self.assertEqual(f._console_shown("telnet", other), "EXCL SPOTTED")
        beacon = "DX de DL1ABC-#:   14100.0  4U1UN          CW    24 dB  22 WPM  NCDXF B 1802Z"
        self.assertFalse(_app()._console_shown("telnet", beacon))
        self.assertEqual(_app()._console_shown("telnet", beacon), "EXCL BCN")

    def test_backup_skimmers_are_orange_and_can_be_switched_off(self) -> None:
        now = [1000.0]
        f = _app(zones=(15,), clock=lambda: now[0])     # only zone 15 skimmers chosen
        self.assertFalse(f._console_shown("telnet", IN_ZONE))        # DL1ABC: zone 14
        now[0] += 120                                             # 2 min, none heard: backup
        self.assertEqual(f._console_shown("telnet", IN_ZONE), "backup")   # EU skimmer: orange
        s5 = IN_ZONE.replace("DL1ABC-#", "S50ABC-#")
        self.assertIs(f._console_shown("telnet", s5), True)          # a chosen one: yellow
        f.spotter_filter.backup_off = True                        # Disable backup spots
        self.assertFalse(f._console_shown("telnet", IN_ZONE))
        self.assertIs(f._console_shown("telnet", s5), True)

    def test_ft8_never_yellow_unless_wanted(self) -> None:
        f = _app(zones=())
        f.spotter_filter.configure("ww")
        ft8 = "DX de W7O:       14074.4  JH8ESQ"            # a person, no mode, FT8 window
        self.assertFalse(f._console_shown("telnet", ft8))
        self.assertTrue(str(f._console_shown("telnet", ft8)).startswith("EXCL FT"))
        self.assertFalse(f._console_shown("HAMQTH", "W7O 14074.4 JH8ESQ FT8"))
        self.assertTrue(f._console_shown("telnet", IN_ZONE))      # CW on 14025: yellow
        f.cfg.follow_modes = ("CW", "FT8")                        # FT8 wanted: yellow
        self.assertTrue(f._console_shown("telnet", ft8))

    def test_cw_on_an_ft8_frequency_waits_for_psk_reporter(self) -> None:
        f = _app(zones=())
        f.spotter_filter.configure("ww")
        reports, asked = {}, []
        f.pskr = types.SimpleNamespace(digital=lambda c, k: reports.get(c, ""),
                                       ask=lambda c, k: asked.append(c))
        cw = "DX de DL1ABC-#:   7075.0  RA1ABC       CW    12 dB  22 WPM  CQ      0640Z"
        self.assertEqual(f._console_shown("telnet", cw), "wait")       # asking PSK Reporter
        self.assertEqual(asked, ["RA1ABC"])
        reports["RA1ABC"] = "FT8"
        self.assertFalse(f._console_shown("telnet", cw))               # he is on FT8: white
        self.assertEqual(f._console_shown("telnet", cw), "EXCL FT8 (PSK Reporter)")
        del reports["RA1ABC"]
        f._console_psk_seen = {("RA1ABC", 7075): time.time() - 200}
        self.assertTrue(f._console_shown("telnet", cw))                # no FT8 report: real CW
        self.assertTrue(f._console_shown("telnet", cw.replace("7075.0", "7025.0")))
        f.pskr = None                                                  # PSK Reporter off
        self.assertTrue(f._console_shown("telnet", cw))

    def test_people_lines_white_when_cluster_is_off(self) -> None:
        f = _app(zones=())
        f.spotter_filter.configure("ww")
        person = "DX de UT4VL:      7024.0  YO2CK                                       0724Z"
        f.cfg.rbn_source = "rbn"                       # Spots: RBN only - skimmers only
        self.assertFalse(f._console_shown("telnet", person))
        self.assertTrue(f._console_shown("telnet", IN_ZONE))      # skimmers still yellow
        f.cfg.rbn_source = "telnet"                    # v0.7.212: Telnet brings people's too
        self.assertTrue(f._console_shown("telnet", person))
        f.cfg.rbn_source = "rbn,telnet3"               # Telnet #3 on
        self.assertTrue(f._console_shown("telnet", person))

    def test_all_pass_without_filters(self) -> None:
        f = _app(zones=())
        f.spotter_filter.configure("ww")
        self.assertTrue(f._console_shown("rbn", OUT_ZONE))


try:
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
except Exception:  # noqa: BLE001
    _root = None


@unittest.skipIf(_root is None, "no display")
class ConsolePendingTests(unittest.TestCase):
    """The filter decision (shown / EXCL) still runs as before - it just no
    longer drives the line's colour (v0.7.225; see ConsolePublishColorTests)."""

    def setUp(self) -> None:
        from pouncer import console
        self.yes = set()
        self.c = console.TrafficConsole(
            _root, shown=lambda src, txt: (None if "DX de" not in txt else txt in self.yes))
        self.c.open()

    def tearDown(self) -> None:
        self.c.close()

    def excl_text(self) -> str:
        r = self.c.text.tag_ranges("excl")
        return "".join(self.c.text.get(r[i], r[i + 1]) for i in range(0, len(r), 2))

    def test_wait_keeps_the_line_pending_longer(self) -> None:
        from pouncer import console
        self.c.shown = lambda src, txt: "wait" if "DX de" in txt else None
        self.c.add([(time.time(), "rbn", OTHER_SKIMMER)])
        self.assertEqual(len(self.c._pending), 1)
        self.c.recheck(now=time.time() + console.SHOWN_WAIT_S + 10)   # past the 5 s, still asking
        self.assertEqual(len(self.c._pending), 1)
        self.c.shown = lambda src, txt: True
        self.c.recheck()
        self.assertEqual(self.c._pending, [])

    def test_given_up_after_the_wait(self) -> None:
        self.c.add([(time.time(), "rbn", OTHER_SKIMMER)])
        self.c.recheck(now=time.time() + 60)
        self.assertEqual(self.c._pending, [])


@unittest.skipIf(_root is None, "no display")
class ConsolePublishColorTests(unittest.TestCase):
    """v0.7.225: colour is the publish outcome, nothing else - red by
    default (never sent), green (this line's station went out), yellow
    (merged into another line's send, with its own PT note)."""

    def setUp(self) -> None:
        from pouncer import console
        self.yes = set()
        self.c = console.TrafficConsole(
            _root, shown=lambda src, txt: (None if "DX de" not in txt else txt in self.yes))
        self.c.open()

    def tearDown(self) -> None:
        self.c.close()

    def tagged(self, name: str) -> str:
        r = self.c.text.tag_ranges(name)
        return "".join(self.c.text.get(r[i], r[i + 1]) for i in range(0, len(r), 2))

    def test_red_by_default_green_once_published(self) -> None:
        self.yes.add(LINE)
        t = time.time()
        self.c.add([(t, "telnet", LINE)])
        self.assertIn("DL1ABC", self.tagged("notsent"))
        self.assertNotIn("DL1ABC", self.tagged("sent"))
        self.c.add([(t + 1, "server >", LINE)])
        self.assertIn("DL1ABC", self.tagged("sent"))

    def test_never_shown_stays_red(self) -> None:
        # excluded by the filters, never published: stays red, no green/yellow
        self.c.add([(time.time(), "telnet", LINE)])
        self.c.recheck(now=time.time() + 60)
        self.assertIn("DL1ABC", self.tagged("notsent"))
        self.assertEqual(self.tagged("sent"), "")
        self.assertEqual(self.tagged("merged"), "")

    def test_backup_sourced_turns_green_too_once_published(self) -> None:
        # there is no separate "backup" colour any more - whether a spot
        # actually reaches port 10000 is what matters (v0.7.225)
        self.c.shown = lambda src, txt: "backup" if "DX de" in txt else None
        t = time.time()
        self.c.add([(t, "telnet", LINE)])
        self.assertIn("DL1ABC", self.tagged("notsent"))
        self.c.add([(t + 1, "server >", LINE)])
        self.assertIn("DL1ABC", self.tagged("sent"))

    def test_green_even_while_the_filter_decision_is_still_pending(self) -> None:
        # a spot still "wait"-ing on a filter decision (e.g. PSK Reporter)
        # can be published in the meantime - it turns green right away,
        # the filter decision does not gate colour at all
        self.c.shown = lambda src, txt: "wait" if "DX de" in txt else None
        t = time.time()
        self.c.add([(t, "telnet", LINE)])
        self.assertEqual(len(self.c._pending), 1)
        self.c.add([(t + 1, "server >", LINE)])
        self.assertIn("DL1ABC", self.tagged("sent"))

    def test_merged_line_is_yellow_with_its_own_pt_note(self) -> None:
        from tests.test_console_pb import A, B
        t = 35334.0
        self.c.add([(t, "telnet", A)])
        self.c.add([(t + 1, "server >", A)])        # A's station goes out: A is "sent"
        self.c.add([(t + 4, "telnet2", B)])          # same station, after the publish: merged
        self.assertIn("SQ6OEP", self.tagged("sent"))
        self.assertIn("SQ6OEP", self.tagged("merged"))
        self.assertIn("merged into PT", self.tagged("pb"))

    def test_pt_note_keeps_its_own_colour_on_a_green_line(self) -> None:
        # v0.7.224 regression: green used to swallow the whole line, PT
        # suffix included, so the publish time stopped standing out
        self.yes.add(LINE)
        t = time.time()
        self.c.add([(t, "telnet", LINE)])
        self.c.add([(t + 1, "server >", LINE)])
        pb_text = self.tagged("pb")
        self.assertTrue(pb_text.strip().startswith("PT "), pb_text)
        green = self.tagged("sent")
        self.assertNotIn("PT ", green)              # the note itself stays "pb"-coloured
        self.assertIn("DL1ABC", green)               # the rest of the line is green

    def test_not_sent_note_after_giving_up(self) -> None:
        # v0.7.230: a shown line that never gets a PT at all - most likely
        # a recent repeat the collector / server didn't resend - says so,
        # instead of staying a silently unexplained red line forever
        from pouncer import console
        self.yes.add(LINE)
        t = time.time()
        self.c.add([(t, "telnet", LINE)])
        self.c._sweep_unsent(now=t + console.UNSENT_NOTE_S - 1)   # not yet
        self.assertEqual(self.tagged("excl"), "")
        self.c._sweep_unsent(now=t + console.UNSENT_NOTE_S + 1)   # now give up
        self.assertIn("not sent", self.tagged("excl"))
        self.assertEqual(self.c._waiting_pb, [])

    def test_not_sent_note_skipped_when_already_excluded(self) -> None:
        # an EXCL'd line already explains the red - no second, redundant note
        from pouncer import console
        from pouncer import traffic
        self.c.shown = lambda src, txt: traffic.Excl("EXCL WARC") if "DX de" in txt else None
        t = time.time()
        self.c.add([(t, "telnet", LINE)])
        self.c.recheck(now=t + console.SHOWN_WAIT_S + 1)   # resolves to EXCL WARC
        self.c._sweep_unsent(now=t + console.UNSENT_NOTE_S + 1)
        excl = self.tagged("excl")
        self.assertIn("EXCL WARC", excl)
        self.assertNotIn("not sent", excl)

    def test_sweep_never_fires_before_the_psk_wait_can_resolve(self) -> None:
        # UNSENT_NOTE_S must stay comfortably past PSK_WAIT_S, or a CW spot
        # still waiting on PSK Reporter could get "not sent" before its
        # real EXCL reason ever lands
        from pouncer import console
        self.assertGreater(console.UNSENT_NOTE_S, console.PSK_WAIT_S)


if __name__ == "__main__":
    unittest.main()
