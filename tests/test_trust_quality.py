"""DXSpider's Q:n (n different skimmers heard it) confirms a call from
[cluster] trust_q on (v0.7.239) - from S55OO's nonPublishedSpots.log,
where Q:9+ spots were held back as cw-conflict-unconfirmed."""
import time
import unittest

from pouncer import settings
from pouncer.config import load_config
from pouncer.spot_processing import SpotProcessor, parse_spot


class TrustQualityTests(unittest.TestCase):
    def setUp(self):
        self.now = 1795216800  # 2026-11-20 23:20Z

    def engine(self, **policy):
        return SpotProcessor(policy, clock=lambda: self.now)

    def send(self, engine, rx, call, khz, tail, source='feed'):
        stamp = time.strftime('%H%MZ', time.gmtime(self.now))
        raw = ('DX de %s: %.1f  %s  CW %s  %s' % (rx, khz, call, tail, stamp)).encode()
        return engine.process(source, 'cluster', raw)

    def occupy(self, engine, call='CT1GFQ', khz=14064.0):
        # two distinct receivers: an established occupant
        self.send(engine, 'DL1HWS-#', call, khz, '20dB Q:1')
        self.now += 1
        self.send(engine, 'M9PSY-#', call, khz, '18dB Q:1')
        self.now += 1

    def test_quality_parsed(self):
        def q(tail):
            return parse_spot(('DX de K5TR-#: 14044.0  KG5SSB  CW %s  2329Z' % tail).encode(),
                              self.now).quality
        self.assertEqual(q('41dB Q:9+ Z:4'), 9)
        self.assertEqual(q('6dB Q:9*+ Z:14'), 9)
        self.assertEqual(q('12dB Q:2* Z:4'), 2)
        self.assertEqual(q('23 dB 24 WPM CQ'), 0)      # raw skimmer line: no Q

    def test_q9_newcomer_is_not_held(self):
        # 2026-11-19 03:07:12Z N7DX 7034.0 K7EK-# Q:9*+ (occupied by N3AD)
        e = self.engine()
        self.occupy(e, 'N3AD', 7034.0)
        out = self.send(e, 'K7EK-#', 'N7DX', 7034.0, '18dB Q:9*+ Z:3,4')
        self.assertEqual(len(out), 1)
        self.assertEqual(e.counts['cw_conflicts_held'], 0)

    def test_below_threshold_is_still_held(self):
        e = self.engine()
        self.occupy(e, 'RW0LD', 7015.7)
        self.assertEqual(self.send(e, 'BH4XDZ-#', 'RW0LN', 7015.7, '9dB Q:2'), [])
        self.assertEqual(e.counts['cw_conflicts_held'], 1)

    def test_threshold_is_configurable_and_zero_is_off(self):
        e = self.engine(trust_quality=2)
        self.occupy(e, 'RW0LD', 7015.7)
        self.assertEqual(len(self.send(e, 'BH4XDZ-#', 'RW0LN', 7015.7, '9dB Q:2')), 1)
        e = self.engine(trust_quality=0)
        self.occupy(e, 'N3AD', 7034.0)
        self.assertEqual(self.send(e, 'K7EK-#', 'N7DX', 7034.0, '18dB Q:9*+'), [])

    def test_mirrors_do_not_add_up(self):
        # one skimmer's Q:1 line from four Telnets is still Q:1, never Q:4
        e = self.engine()
        self.occupy(e, 'N1MX', 7016.0)
        got = []
        for source in ('telnet', 'telnet2', 'telnet3', 'telnet4'):
            got += self.send(e, 'AC0C-#', 'WA2BLE', 7016.0, '12dB Q:1', source=source)
        self.assertEqual(got, [])

    def test_repeat_is_still_suppressed(self):
        e = self.engine()
        self.assertEqual(len(self.send(e, 'DF7GB-#', '9A2G', 7008.5, '6dB Q:9+')), 1)
        self.now += 30
        self.assertEqual(self.send(e, 'DO4DXA-#', '9A2G', 7008.5, '6dB Q:9+'), [])

    def test_quality_confirms_a_move(self):
        e = self.engine()
        self.send(e, 'DF7GB-#', 'K1KT', 14015.0, '7dB Q:5')
        self.now += 20
        out = self.send(e, 'KM7W-#', 'K1KT', 14020.0, '7dB Q:5')
        self.assertEqual(len(out), 1)
        self.assertIn(b'14020', out[0])
        e = self.engine(trust_quality=0)
        self.send(e, 'DF7GB-#', 'K1KT', 14015.0, '7dB Q:5')
        self.now += 20
        self.assertEqual(self.send(e, 'KM7W-#', 'K1KT', 14020.0, '7dB Q:5'), [])

    def gc(self, engine, rx, call, khz, conf, wpm=True):
        stamp = time.strftime('%H%MZ', time.gmtime(self.now))
        head = ('DX de %s:' % rx)
        head = head + ('%.2f' % khz).rjust(25 - len(head)) + '  ' + call.ljust(12)
        body = ('CW 6 dB 30 WPM         - JN95 ' if wpm else 'CW 48 dB - JN18 ') + conf + ' ' + stamp
        return engine.process('telnet4', 'gocluster', (head + body).encode())

    def test_gocluster_v_is_trusted_others_are_not(self):
        # Telnet #4 (N2WQ GoCluster): V = strongly corroborated
        for conf, held in (('V', 0), ('P', 1), ('S', 1), ('?', 1)):
            e = self.engine()
            self.occupy(e, 'EA3IZQ', 7008.0)
            out = self.gc(e, 'BG4GOV-#', 'JA6JQZ/6', 7008.0, conf)
            self.assertEqual(e.counts['cw_conflicts_held'], held, conf)
            self.assertEqual(len(out), 1 - held, conf)

    def test_gocluster_v_on_a_short_line(self):
        e = self.engine()
        self.occupy(e, 'G4ALD', 21031.2)
        self.assertEqual(len(self.gc(e, 'SE5E-#', 'F6FLU', 21031.2, 'V', wpm=False)), 1)
        e = self.engine(trust_quality=0)
        self.occupy(e, 'G4ALD', 21031.2)
        self.assertEqual(self.gc(e, 'SE5E-#', 'F6FLU', 21031.2, 'V', wpm=False), [])

    def test_star_or_gocluster_v_confirms_a_move_over_500_hz(self):
        # v0.7.247 (S55OO): over 500 Hz away with the call confirmed = he moved
        e = self.engine()
        self.send(e, 'DF7GB-#', 'K1KT', 14015.0, '7dB Q:5*')
        self.now += 20
        self.assertTrue(self.send(e, 'KM7W-#', 'K1KT', 14020.0, '7dB Q:5*'))
        self.assertEqual(e.last_evaluation['reason'], 'frequency-move-quality')
        e = self.engine()
        self.gc(e, 'DF7GB-#', 'K1KT', 14015.0, 'V')
        self.now += 20
        self.assertTrue(self.gc(e, 'KM7W-#', 'K1KT', 14020.0, 'V'))

    def test_low_q_or_within_500_hz_still_waits(self):
        e = self.engine()
        self.send(e, 'DF7GB-#', 'K1KT', 14015.0, '7dB Q:1*')
        self.now += 20
        self.assertEqual(self.send(e, 'KM7W-#', 'K1KT', 14020.0, '7dB Q:1*'), [])
        self.assertEqual(e.last_evaluation['reason'], 'pending-corroboration')
        e = self.engine()
        self.send(e, 'DF7GB-#', 'K1KT', 14015.0, '7dB Q:5*')
        self.now += 20
        self.assertEqual(self.send(e, 'KM7W-#', 'K1KT', 14015.3, '7dB Q:5*'), [])
        self.assertEqual(e.last_evaluation['reason'], 'repeat')

    def test_gocluster_q_text_is_not_quality(self):
        line = b'DX de DF7GB-#:    7008.50  9A2G        CW 6 dB Q:9 WPM        - JN95 P 2257Z'
        self.assertEqual(parse_spot(line, self.now, 'gocluster').quality, 0)


class TrustQualitySettingTests(unittest.TestCase):
    def test_default_and_saved(self):
        import os
        import tempfile
        self.assertEqual(load_config("").cluster_trust_q, 3)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            open(p, "w").close()
            cfg = load_config(p)
            self.assertTrue(settings.save(cfg, p, "cluster.trust_q", "4")["ok"])
            self.assertEqual(cfg.cluster_trust_q, 4)
            self.assertEqual(load_config(p).cluster_trust_q, 4)
        self.assertEqual(settings.place("cluster.trust_q")[0], "Spots")

    def test_menu_choice_saves_and_applies_live(self):
        import types
        from pouncer.app import PouncerApp
        from pouncer.collector import Collector
        saved = []
        fake = types.SimpleNamespace(status_label=types.SimpleNamespace(configure=lambda **k: None),
                                     _save_setting=lambda k, v: saved.append((k, v)))
        PouncerApp._set_trust_q(fake, 2)
        PouncerApp._set_trust_q(fake, 0)
        self.assertEqual(saved, [("cluster.trust_q", 2), ("cluster.trust_q", 0)])

    def test_app_policy(self):
        import types
        from pouncer.app import PouncerApp
        fake = types.SimpleNamespace(cfg=types.SimpleNamespace(
            spot_processing={"repeat_seconds": 240}, cluster_trust_q=5))
        self.assertEqual(PouncerApp._collector_policy(fake),
                         {"repeat_seconds": 240, "trust_quality": 5})


if __name__ == "__main__":
    unittest.main()
