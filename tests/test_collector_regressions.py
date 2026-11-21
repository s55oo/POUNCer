"""Regressions reproduced by the independent collector audits."""
import queue
import types
import unittest
import threading
from unittest.mock import patch
from collections import deque
from dataclasses import replace

from pouncer.collector import Collector
from pouncer.clusterserver import ClusterServer
from pouncer.rbn import parse_spot, parse_cluster_spot, set_spot_mode
from pouncer.spot_processing import parse_spot as parse_header
from tests import test_merge_served as bridge
from tests.test_collector_spot_processing import START, line


class AuditedBridgeTests(unittest.TestCase):
    setUp = bridge.CollectorBridgeTests.setUp
    spot = bridge.CollectorBridgeTests.spot

    def test_synthetic_mirror_does_not_invent_an_independent_receiver(self):
        self.collector.process(self.app, self.spot())
        report = self.spot('W3LPL-10-#', 14020.7)
        self.assertEqual(len(self.collector.process(self.app, report)), 1)   # a move (v0.7.248)
        mirror = replace(report, raw_line='', feed_id='http')
        self.assertEqual(self.collector.process(self.app, mirror), [])
        self.assertEqual(self.collector.engine.last_evaluation['reason'], 'repeat')
        self.assertEqual(self.collector.engine.counts['confirmed_moves'], 1)

    def test_synthetic_header_retains_receiver_call_and_frequency(self):
        report = replace(self.spot('W3LPL-10-#'),
                         freq_khz=14020.123456, dxcall='EA8/VERYLONG1ABC', raw_line='')
        parsed = parse_header(self.collector._wire(report), START)
        self.assertEqual(parsed.origin, 'W3LPL-10')
        self.assertEqual(parsed.call, 'EA8/VERYLONG1ABC')
        self.assertAlmostEqual(parsed.frequency_hz, 14020123.456)

    def test_receiverless_spot_is_served_with_one_decimal_not_six(self):
        # A spot with no native raw_line (e.g. the RBN HTTP feed) is wired
        # internally with full Hz precision for evidence matching; what
        # actually goes out must still read like a normal "DX de" line.
        report = replace(self.spot('GM0UDL-#'), freq_khz=7008.0, dxcall='EI0CZ', raw_line='')
        selected = self.collector.process(self.app, report)
        self.assertTrue(selected)
        self.assertIn(' 7008.0 ', selected[0].raw_line)
        self.assertNotIn('7008.000000', selected[0].raw_line)

    def _hold(self):
        self.collector.process(self.app, self.spot('B1BBB-#', call='S53M'))
        local = self.spot('LOCAL1-#', 14020.1, 'S53MM', source='telnet2', role='local')
        self.assertEqual(self.collector.process(self.app, local), [])
        self.assertEqual(len(self.collector.templates), 1)
        return local

    def test_stale_repeat_keeps_the_original_held_payload(self):
        local = self._hold()
        stale = replace(local, raw_line=local.raw_line.replace('1200Z', '1150Z'))
        self.collector.process(self.app, stale)
        self.assertEqual(len(self.collector.templates), 1)
        self.now += 3
        released = self.collector.drain(self.app)
        self.assertEqual([s.dxcall for s in released], ['S53MM'])
        self.assertEqual(released[0].raw_line, local.raw_line.replace('14020.10', '14020.1'))

    def test_out_of_order_repeat_keeps_the_original_held_payload(self):
        local = self._hold()
        older = replace(local, raw_line=local.raw_line.replace('1200Z', '1159Z'))
        self.collector.process(self.app, older)
        self.now += 3
        self.assertEqual([s.dxcall for s in self.collector.drain(self.app)], ['S53MM'])

    def test_epoch_reset_precedes_a_qualified_hold_release(self):
        self.app.spotter_filter = types.SimpleNamespace(accept=lambda call: call.startswith('LOCAL'))
        local = self._hold()
        self.now += .1
        support = self.spot('C1CCC-#', call='S53M', source='telnet3')
        self.collector.engine.process('telnet3', 'cluster', self.collector._wire(support), admit=False)
        self.now += .1
        new_connection = replace(self.spot('B1BBB-#', call='JA9ZZZ'), feed_epoch='new-epoch')
        outputs = self.collector.process(self.app, new_connection)
        self.assertEqual(outputs, [])
        self.assertEqual(self.collector.engine.counts['callsign_corrections'], 0)
        self.now += 3
        self.assertEqual([s.dxcall for s in self.collector.drain(self.app)], [local.dxcall])

    def _external_support(self):
        self.app.spotter_filter = types.SimpleNamespace(accept=lambda call: call.startswith('LOCAL'))
        for receiver in ('B1BBB-#', 'C1CCC-#'):
            self.collector.process(self.app, self.spot(receiver, call='S53M'))

    def test_spotted_filter_uses_corrected_callsign(self):
        self.app.spotted_filter = types.SimpleNamespace(accept=lambda call: call == 'S53M')
        self._external_support()
        local = self.spot('LOCAL1-#', 14020.1, 'S53N', source='telnet2', role='local')
        self.assertEqual([s.dxcall for s in self.collector.process(self.app, local)], ['S53M'])

    def test_filtered_corrected_call_does_not_consume_an_episode(self):
        self.app.spotted_filter = types.SimpleNamespace(accept=lambda call: call == 'S53N')
        self._external_support()
        local = self.spot('LOCAL1-#', 14020.1, 'S53N', source='telnet2', role='local')
        self.assertEqual(self.collector.process(self.app, local), [])
        self.assertNotIn(('20M', 'CW', 'S53M'), self.collector.engine.repeats)
        self.app.spotted_filter = None
        self.assertEqual([s.dxcall for s in self.collector.process(self.app, local)], ['S53M'])

    def test_filtered_confirmation_releases_wanted_receiver_across_minute(self):
        self.app.spotter_filter = types.SimpleNamespace(accept=lambda rx: rx.startswith('ON6'))
        for rx in ('G4ZFE-#', 'DM5GG-#'):
            self.collector.process(self.app, self.spot(rx, call='S53ZO'))
        pending = self.spot('ON6URE-1-#', call='S53M')
        self.assertEqual(self.collector.process(self.app, pending), [])
        self.now += 61
        def report(rx):
            spot = self.spot(rx, call='S53M', source='telnet3')
            return replace(spot, raw_line=spot.raw_line.replace('1200Z', '1201Z'))
        mirror = report('ON6URE-2-#')
        self.assertEqual(self.collector.process(self.app, mirror), [])
        selected = self.collector.process(self.app, report('DK0KK-#'))
        self.assertEqual([s.raw_line for s in selected], [pending.raw_line.replace('14020.00', '14020.0')])
        self.assertFalse(self.collector.cw_waiting)
        self.assertFalse(self.collector.process(self.app, pending))

    def test_filtered_confirmation_cannot_resurrect_disconnected_receiver(self):
        self.app.spotter_filter = types.SimpleNamespace(accept=lambda rx: rx.startswith('ON6'))
        for rx in ('G4ZFE-#', 'DM5GG-#'):
            self.collector.process(self.app, self.spot(rx, call='S53ZO', source='telnet3'))
        pending = self.spot('ON6URE-1-#', call='S53M')
        self.assertFalse(self.collector.process(self.app, pending))
        self.collector.engine.reset_source('telnet')
        self.assertFalse(self.collector.process(self.app, self.spot('DK0KK-#', call='S53M', source='telnet3')))

    def test_queued_observations_keep_their_receipt_window(self):
        self.collector.process(self.app, self.spot())
        early = self.spot('B1BBB-#', 14020.7)
        early.arrived = START + 1
        self.now += 21
        late = self.spot('C1CCC-#', 14020.7)
        late.arrived = START + 21
        # v0.7.248: one spot over 500 Hz away moves at once; its copy is a repeat
        self.assertEqual(len(self.collector.process(self.app, early)), 1)
        self.assertEqual(self.collector.process(self.app, late), [])
        self.assertEqual(self.collector.engine.counts['confirmed_moves'], 1)

    def test_backlogged_local_cannot_use_evidence_arriving_after_hold_deadline(self):
        self._external_support()  # drop one receiver so the original call is pending
        self.collector.engine.reset_source('telnet')
        self.collector.process(self.app, self.spot('B1BBB-#', call='S53M'))
        local = self.spot('LOCAL1-#', 14020.1, 'S53MM', source='telnet2', role='local')
        self.now += 4
        self.collector.process(self.app, self.spot('C1CCC-#', call='S53M', source='telnet3'))
        outputs = self.collector.process(self.app, local)
        self.assertEqual([s.dxcall for s in outputs], ['S53MM'])
        self.assertFalse(self.collector.engine.local_call_holds)

    def test_selected_records_bypass_server_legacy_window(self):
        self.collector = Collector({'repeat_seconds': 10}, clock=lambda: self.now)
        server = ClusterServer(port=0)
        self.assertTrue(server.start())
        try:
            first = self.collector.process(self.app, self.spot())[0]
            self.assertTrue(server.publish(first))
            self.now += 11
            second = self.collector.process(self.app, self.spot())[0]
            self.assertTrue(server.publish(second))
        finally:
            server.stop()

    def test_selected_mode_episodes_reach_server(self):
        server = ClusterServer(port=0)
        self.assertTrue(server.start())
        try:
            first = self.spot()
            self.assertTrue(server.publish(self.collector.process(self.app, first)[0]))
            rtty = replace(first, mode='RTTY', raw_line=first.raw_line.replace('CW', 'RTTY'))
            self.app.cfg.follow_modes = ('CW', 'RTTY')
            self.assertTrue(server.publish(self.collector.process(self.app, rtty)[0]))
        finally:
            server.stop()

    def test_suppressed_primary_receiver_still_updates_own_marker(self):
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        self.app.spotter_filter = types.SimpleNamespace(
            primary='S53WW', accept=lambda call: True, multi=True)
        self.app._own_heard = {}
        self.app.OWN_HEARD_S = P.OWN_HEARD_S
        self.app._note_own = P._note_own.__get__(self.app)
        first = self.spot()
        self.collector.process(self.app, first)
        self.assertEqual(self.collector.process(self.app, self.spot('S53WW-#')), [])
        self.assertEqual(P._own_mark(self.app, first), 'S53WW')

    def test_fourth_telnet_has_independent_processing_role(self):
        self.app.cfg.telnet4_role = 'gocluster'
        spot = replace(self.spot(), feed_id='telnet4')
        self.assertEqual(Collector.role(self.app.cfg, spot), 'gocluster')

    def test_timely_queued_evidence_can_correct_after_timer_is_delayed(self):
        self.app.spotter_filter = types.SimpleNamespace(accept=lambda call: call.startswith('LOCAL'))
        self._hold()
        support = self.spot('C1CCC-#', call='S53M', source='telnet3')
        support.arrived = START + 2
        self.now += 4
        outputs = self.collector.process(self.app, support) + self.collector.drain(self.app)
        self.assertEqual([s.dxcall for s in outputs], ['S53M'])
        self.assertEqual(self.collector.engine.counts['local_call_corrected'], 1)

    def test_unrelated_backlog_packet_does_not_expire_timely_support(self):
        self.app.spotter_filter = types.SimpleNamespace(accept=lambda call: call.startswith('LOCAL'))
        self._hold()
        unrelated = self.spot('D1DDD-#', call='JA9ZZZ', source='telnet3')
        unrelated.arrived = START + 1
        support = self.spot('C1CCC-#', call='S53M', source='telnet3')
        support.arrived = START + 2
        self.now += 4
        self.assertEqual(self.collector.process(self.app, unrelated), [])
        self.assertTrue(self.collector.engine.local_call_holds)
        outputs = self.collector.process(self.app, support) + self.collector.drain(self.app)
        self.assertEqual([s.dxcall for s in outputs], ['S53M'])
        self.assertEqual(self.collector.engine.counts['local_call_expired'], 0)

    def test_held_correction_updates_primary_marker_even_when_duplicate(self):
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        self.app.spotter_filter = types.SimpleNamespace(
            primary='LOCAL1', accept=lambda call: True, multi=True)
        self.app._own_heard = {}
        self.app.OWN_HEARD_S = P.OWN_HEARD_S
        self.app._note_own = P._note_own.__get__(self.app)
        self._hold()
        self.assertEqual(self.collector.process(
            self.app, self.spot('C1CCC-#', call='S53M', source='telnet3')), [])
        self.assertEqual(self.app._own_heard[('S53M', '20m')][1], 'LOCAL1')
        self.assertTrue(self.app._dirty)



class AuditedAppTests(unittest.TestCase):
    def app(self):
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        served = []
        app = types.SimpleNamespace(
            cfg=types.SimpleNamespace(cluster_merge=True, hide_beacons=False),
            spotter_filter=None, spotted_filter=None,
            _collector_incoming=queue.Queue(), spot_queue=queue.Queue(), _team_queue=queue.Queue(),
            _collector=Collector(clock=lambda: START), spots={}, _spot_memory=deque(),
            _perf={'spots_in': 0}, team=None, _is_my_call=lambda s: False,
            _beacon_out=lambda s: False, _note_own=lambda s: None,
            _check_overlap=lambda s: None, _track_on_freq=lambda s: None,
            _serve=served.append, _relabel_overlap=lambda: None,
            _merged_dup=lambda s: False)
        return P, app, served

    def test_filter_readmission_does_not_restore_an_uncorroborated_outlier(self):
        from pouncer.app import readmit_spots
        P, app, served = self.app()
        first = parse_spot(line(call='K1ABC').decode())
        first.received = START
        first.feed_id = 'telnet'
        selected = app._collector.process(app, first)[0]
        app.spots[selected.key] = selected
        # 14020.3: within 500 Hz of the selected spot = a repeat, never restored
        # (over 500 Hz it would be a move, sent at once since v0.7.248)
        bad = replace(first, freq_khz=14020.3, raw_line=first.raw_line.replace('14020.00', '14020.30'))
        self.assertEqual(app._collector.process(app, bad), [])
        app._spot_memory.extend((first, bad))
        self.assertEqual(readmit_spots(app), 0)
        P._drain_spots(app)
        self.assertEqual(served, [])

    def test_filter_readmission_replays_only_the_corrected_selected_identity(self):
        from pouncer.app import readmit_spots
        P, app, served = self.app()
        app.cfg.telnet2_role = 'local'
        app.spotter_filter = types.SimpleNamespace(accept=lambda call: call.startswith('LOCAL'))
        for origin in ('B1BBB-#', 'C1CCC-#'):
            s = parse_spot(line('S53M', origin=origin).decode())
            s.feed_id, s.received = 'telnet', START
            app._collector.process(app, s)
        typo = parse_spot(line('S53MM', 14020.1, origin='LOCAL1-#').decode())
        typo.feed_id, typo.received = 'telnet2', START
        corrected = app._collector.process(app, typo)[0]
        self.assertEqual(corrected.dxcall, 'S53M')
        app._spot_memory.append(typo)
        self.assertEqual(readmit_spots(app), 0)
        P._drain_spots(app)
        P._drain_spots(app)  # scheduled replay is admitted on the next tick
        self.assertEqual([s.dxcall for s in served], ['S53M'])

    def test_filter_widening_can_admit_new_station_without_relearning_votes(self):
        from pouncer.app import readmit_spots
        P, app, served = self.app()
        app.spotter_filter = types.SimpleNamespace(accept=lambda call: False)
        s = parse_spot(line().decode())
        s.feed_id, s.received = 'telnet', START
        self.assertEqual(app._collector.process(app, s), [])
        before = len(app._collector.engine.measurements)
        app.spotter_filter = types.SimpleNamespace(accept=lambda call: True)
        self.assertEqual(readmit_spots(app), 0)
        P._drain_spots(app)
        P._drain_spots(app)  # scheduled replay is admitted on the next tick
        self.assertEqual([s.dxcall for s in served], ['JA1ABC'])
        self.assertEqual(len(app._collector.engine.measurements), before)

    def test_psk_mode_relabel_preserves_comments_in_server_output(self):
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        s = parse_spot('DX de A1AAA-#:   14074.5  JA1ABC    CW  12dB Q:9* > original 1200Z JO31')
        app = types.SimpleNamespace(pskr=types.SimpleNamespace(digital=lambda *args: 'FT8'))
        P._check_overlap(app, s)
        self.assertEqual(s.mode, 'FT8')
        self.assertIn('FT8  12dB Q:9* > original 1200Z JO31', s.raw_line)
        server = ClusterServer(port=0)
        self.assertTrue(server.start())
        try:
            self.assertTrue(server.publish(s))
            self.assertEqual(server._history[-1], s.raw_line)
        finally:
            server.stop()

    def test_mode_relabel_inserts_token_before_human_comment(self):
        raw = 'DX de DL1ABC:   7074.5  JA1ABC  59  original > comment 1200Z'
        s = parse_cluster_spot(raw)
        set_spot_mode(s, 'FT8')
        self.assertEqual(s.raw_line, raw.replace('59  original', 'FT8 59  original'))

    def test_commentless_mode_relabel_inserts_mode(self):
        raw = 'DX de DL1ABC:   7074.5  JA1ABC'
        s = parse_cluster_spot(raw)
        set_spot_mode(s, 'FT8')
        self.assertEqual(s.raw_line, raw + ' FT8')

    def test_web_readmission_is_scheduled_on_ui_owner(self):
        from pouncer.app import readmit_spots
        P, app, served = self.app()
        threads = []
        original = app._collector.readmit
        def readmit(current_app):
            threads.append(threading.get_ident())
            return original(current_app)
        with patch.object(app._collector, 'readmit', side_effect=readmit):
            worker = threading.Thread(target=readmit_spots, args=(app,))
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(threads, [])
            P._drain_spots(app)
        self.assertEqual(threads, [threading.get_ident()])

    def test_hold_survives_ui_batch_boundary_until_queued_support_is_learned(self):
        P, app, served = self.app()
        now = [START]
        app._collector = Collector(clock=lambda: now[0])
        app.cfg.telnet2_role = 'local'
        app.spotter_filter = types.SimpleNamespace(accept=lambda call: call.startswith('LOCAL'))
        first = parse_spot(line('S53M', origin='B1BBB-#').decode())
        first.feed_id, first.received = 'telnet', START
        app._collector.process(app, first)
        local = parse_spot(line('S53MM', 14020.1, origin='LOCAL1-#').decode())
        local.feed_id, local.received = 'telnet2', START
        self.assertEqual(app._collector.process(app, local), [])
        now[0] += 4
        for index in range(500):
            unrelated = parse_spot(line(f'JA{index}ZZZ', origin='D1DDD-#').decode())
            unrelated.feed_id, unrelated.arrived = 'telnet', START + 1
            app._collector_incoming.put(unrelated)
        support = parse_spot(line('S53M', origin='C1CCC-#').decode())
        support.feed_id, support.arrived = 'telnet', START + 2
        app._collector_incoming.put(support)
        P._drain_spots(app)
        self.assertEqual(served, [])
        self.assertTrue(app._collector.engine.local_call_holds)
        P._drain_spots(app)
        self.assertEqual([s.dxcall for s in served], ['S53M'])
        self.assertFalse(app._collector.engine.local_call_holds)
