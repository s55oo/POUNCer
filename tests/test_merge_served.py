"""The former Q-based output merge is replaced by shared receiver evidence."""
import queue
import time
import tempfile
from pathlib import Path
import types
import unittest
from dataclasses import replace
from unittest.mock import patch

from pouncer.collector import Collector
from pouncer.config import Config, load_config
from pouncer.rbn import RBNClient, parse_spot, parse_cluster_spot
from pouncer.clusterserver import ClusterServer
from tests.test_collector_spot_processing import START, line, gc


class CollectorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.now = START
        self.app = types.SimpleNamespace(cfg=Config(), spotter_filter=None, spotted_filter=None)
        self.collector = Collector(clock=lambda: self.now)

    def spot(self, receiver='A1AAA-#', frequency=14020, call='JA1ABC', source='telnet', role='cluster'):
        raw = line(call, frequency, origin=receiver).decode('latin-1')
        spot = parse_spot(raw)
        spot.feed_id, spot.feed_epoch = source, 'epoch-one'
        spot.received = self.now
        if source in ('telnet', 'telnet2', 'telnet3'):
            n = 1 if source == 'telnet' else int(source[-1])
            setattr(self.app.cfg, f'telnet{n}_role', role)
        return spot

    def test_shared_window_no_quality_bypass_and_distinct_move(self):
        self.assertTrue(self.collector.process(self.app, self.spot()))
        same = self.spot('B1BBB-#')
        same.quality = 99
        self.assertEqual(self.collector.process(self.app, same), [])
        result = self.collector.process(self.app, self.spot('B1BBB-#', 14020.7))
        self.assertEqual(result[0].freq_khz, 14020.7)      # over 500 Hz: a move, at once
        self.assertTrue(result[0].collector_selected)
        mirrored = self.spot('B1BBB-#', 14020.7, source='telnet2')
        self.assertEqual(self.collector.process(self.app, mirrored), [])
        self.assertEqual(self.collector.process(self.app, self.spot('C1CCC-#', 14020.7)), [])
        self.now += 181
        self.assertTrue(self.collector.process(self.app, self.spot()))

    def test_filtered_receivers_learn_without_consuming_episode(self):
        self.app.spotter_filter = types.SimpleNamespace(accept=lambda call: call.startswith('A1'))
        self.assertEqual(self.collector.process(self.app, self.spot('B1BBB-#')), [])
        self.assertTrue(self.collector.process(self.app, self.spot()))
        for origin in ('B1BBB-#', 'C1CCC-#'):
            self.assertEqual(self.collector.process(self.app, self.spot(origin, 14020.7)), [])
        selected = self.collector.process(self.app, self.spot(frequency=14020.7))
        self.assertEqual(selected[0].freq_khz, 14020.7)

    def test_reset_retains_episode_but_retires_receiver_evidence(self):
        self.collector.process(self.app, self.spot())
        self.assertTrue(self.collector.engine.measurements)
        changed = replace(self.spot(), feed_epoch='epoch-two')
        self.assertEqual(self.collector.process(self.app, changed), [])
        self.assertEqual(self.collector.engine.counts['source_resets'], 1)
        self.assertEqual(len(self.collector.engine.measurements), 1)

    def test_disabled_then_enabled_starts_fresh(self):
        self.collector.process(self.app, self.spot())
        self.app.cfg.cluster_merge = False
        self.assertTrue(self.collector.process(self.app, self.spot()))
        self.app.cfg.cluster_merge = True
        self.assertTrue(self.collector.process(self.app, self.spot()))

    def test_history_does_not_train_or_consume_episode(self):
        history = replace(self.spot(), history=True)
        self.assertTrue(self.collector.process(self.app, history))
        self.assertFalse(self.collector.engine.measurements)
        self.assertTrue(self.collector.process(self.app, self.spot()))

    def test_undated_does_not_gain_timestamp_evidence(self):
        spot = self.spot()
        spot.raw_line = spot.raw_line.replace('1200Z', '')
        spot.time_utc = ''
        self.assertTrue(self.collector.process(self.app, spot))
        self.assertIsNone(next(iter(self.collector.engine.measurements.values())).spot.observed_at)

    def test_wire_time_before_grid_is_preserved_in_spot_fields(self):
        spot = self.spot()
        spot = parse_spot(spot.raw_line + ' JO31')
        self.assertEqual(spot.time_utc, '1200Z')
        self.assertTrue(spot.raw_line.endswith('1200Z JO31'))

    def test_raw_human_comment_preserved(self):
        raw = 'DX de DL1ABC:   14205.0  K1ABC   59  tnx > Q:9  original  1200Z JO31'
        spot = parse_cluster_spot(raw)
        spot.feed_id = 'telnet'
        selected = self.collector.process(self.app, spot)
        self.assertEqual(selected[0].raw_line, raw)

    def test_local_hold_releases_original_without_more_input(self):
        self.collector.process(self.app, self.spot('B1BBB-#', 14020, 'S53M'))
        suspicious = self.spot('LOCAL1-#', 14020.1, 'S53MM', source='telnet2', role='local')
        self.assertEqual(self.collector.process(self.app, suspicious), [])
        self.assertEqual(len(self.collector.templates), 1)
        self.now += 3
        selected = self.collector.drain(self.app)
        self.assertEqual(selected[0].dxcall, 'S53MM')
        self.assertEqual(selected[0].raw_line, suspicious.raw_line.replace('14020.10', '14020.1'))
        self.assertFalse(self.collector.templates)

    def test_local_correction_uses_unselected_external_receivers(self):
        self.app.spotter_filter = types.SimpleNamespace(accept=lambda call: call.startswith('LOCAL'))
        for receiver in ('B1BBB-#', 'C1CCC-#'):
            self.assertEqual(self.collector.process(self.app, self.spot(receiver, call='S53M')), [])
        suspicious = self.spot('LOCAL1-#', 14020.1, 'S53MM', source='telnet2', role='local')
        selected = self.collector.process(self.app, suspicious)
        self.assertEqual(selected[0].dxcall, 'S53M')
        self.assertEqual(selected[0].freq_khz, 14020)
        suffix = suspicious.raw_line.split('S53MM', 1)[1].lstrip()
        self.assertTrue(selected[0].raw_line.endswith(suffix))

    def test_source_roles_and_independent_rbn_ports(self):
        for source, role in [('http', 'rbn'), ('rbn:7000', 'rbn'), ('hamqth', 'rbn')]:
            self.assertEqual(Collector.role(self.app.cfg, replace(self.spot(), feed_id=source)), role)
        a = RBNClient('example.invalid', 7000, 'S55OO', '', queue.Queue())
        b = RBNClient('example.invalid', 7001, 'S55OO', '', queue.Queue())
        self.assertNotEqual(a._tag_spot(self.spot()).feed_id, b._tag_spot(self.spot()).feed_id)

    def test_alignment_corrects_header_and_preserves_suffix(self):
        self.app.spotter_filter = types.SimpleNamespace(accept=lambda call: call.startswith('A1'))
        for index in range(5):
            self.now = START + index * 12
            call, freq = f'JA{index}ABC', 14010 + index * 10
            for receiver, frequency in [('A1AAA-#', freq + .7), ('B1BBB-#', freq), ('C1CCC-#', freq)]:
                spot = self.spot(receiver, frequency, call, role="rbn")
                stamp = time.strftime('%H%MZ', time.gmtime(self.now))
                spot.raw_line = spot.raw_line.replace('1200Z', stamp)
                self.collector.process(self.app, spot)
        fresh = self.spot(frequency=14060.7, call='JA9XYZ', role='rbn')
        selected = self.collector.process(self.app, fresh)
        self.assertAlmostEqual(selected[0].freq_khz, 14060)
        self.assertTrue(selected[0].raw_line.endswith(fresh.raw_line.split('JA9XYZ', 1)[1]))

    def test_policy_and_roles_validate_at_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.ini'
            path.write_text('[telnet2]\nrole = gocluster\n[spot_processing]\nrepeat_seconds = 12.5\n')
            cfg = load_config(str(path))
            self.assertEqual(cfg.telnet2_role, 'gocluster')
            self.assertEqual(cfg.spot_processing['repeat_seconds'], 12.5)
            for text in ('[telnet1]\nrole = typo', '[spot_processing]\nmax_changes = -1',
                         '[spot_processing]\ncorroboration_seconds = nan',
                         '[spot_processing]\nenabled = false'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    load_config(str(path))

    def test_gocluster_login_only_and_commands_stay_on_cluster(self):
        from tests.test_sources import fake_app
        app_module, app, made, fake = fake_app('telnet,telnet2')
        app.cfg.telnet1_role = 'gocluster'
        app.cfg.telnet1_commands = ('set/noise quiet',)
        app.cfg.telnet2_host = 'primary.example.invalid'
        with patch.object(app_module, 'RBNClient', fake):
            app._ensure_telnet_clients()
        gc_client, primary = made
        self.assertEqual(gc_client.kw['history'], 0)
        self.assertEqual(gc_client.backup_commands, ())
        self.assertIsNone(gc_client.on_line)
        gc_client.up = primary.up = True
        self.assertIs(app._upstream_client(), primary)
        self.assertIsNotNone(primary.on_line)



class CollectorAppTests(unittest.TestCase):
    def test_readers_to_list_team_and_server_share_selection(self):
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        served, team = [], []
        app = types.SimpleNamespace(cfg=Config(), spotter_filter=None, spotted_filter=None,
            _collector_incoming=queue.Queue(), spot_queue=queue.Queue(), _team_queue=queue.Queue(),
            _collector=Collector(clock=lambda: START), spots={}, _perf={'raw': 0, 'spots_in': 0},
            bandact=types.SimpleNamespace(add=lambda s: None),
            team=types.SimpleNamespace(is_master=lambda: True, send_spot=team.append),
            _is_my_call=lambda s: False, _beacon_out=lambda s: False,
            _note_own=lambda s: None, _check_overlap=lambda s: None,
            _track_on_freq=lambda s: None, _serve=served.append, _relabel_overlap=lambda: None,
            _spot_dict=P._spot_dict,
            _merged_dup=lambda s: self.fail('selected spots reached old dedupe'))
        reader = RBNClient('example.invalid', 8000, 'S55OO', '', app.spot_queue,
                           name='telnet', on_raw=lambda s: P._on_raw_spot(app, s))
        raw = line().decode()
        reader._consume(raw + '\n' + raw + '\n', '')
        self.assertTrue(app.spot_queue.empty())  # original reader path bypassed
        P._drain_spots(app)
        self.assertEqual(len(app.spots), 1)
        self.assertEqual(len(team), 1)
        self.assertEqual(len(served), 1)
        self.assertEqual(team[0]['raw_line'], raw.replace('14020.00', '14020.0'))
        self.assertEqual(served[0].raw_line, raw.replace('14020.00', '14020.0'))
