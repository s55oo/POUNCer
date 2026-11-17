"""UI-thread bridge to the receiver-aware spot-collector engine.

Readers enqueue original observations; the UI owns evidence and selection state.
Unwanted receivers can supply evidence without consuming an output episode.
"""
from collections import OrderedDict
from dataclasses import replace

from .spot_processing import SpotProcessor, parse_spot as parse_header
from .clusterserver import spot_comment
from .config import TELNETS
from .bands import band_from_khz
from . import traffic


class Collector:
    def __init__(self, config=None, clock=None):
        self.engine = SpotProcessor(config, **({"clock": clock} if clock else {}))
        self.enabled = None
        self.epochs = {}
        self.templates = OrderedDict()
        self.selected = OrderedDict()
        self.deferred = OrderedDict()
        self._active = None

    @staticmethod
    def role(cfg, spot):
        source = spot.feed_id or spot.source or "rbn"
        if source in TELNETS:
            n = 1 if source == "telnet" else int(source[-1])
            return getattr(cfg, f"telnet{n}_role", "cluster")
        return "rbn" if source in ("rbn", "http", "hamqth") or source.startswith("rbn:") else "cluster"

    @staticmethod
    def wanted(app, spot, *, check_dx=True):
        cfg = app.cfg
        mode = (spot.mode or "").upper()
        role = Collector.role(cfg, spot)
        skimmer = spot.spotter.endswith("-#") or role == "local"
        if not skimmer and (spot.feed_id or "").startswith("rbn"):
            return False
        if skimmer and mode not in getattr(cfg, "follow_modes", ("CW",)):
            return False
        if mode in traffic.DIGITAL and mode not in getattr(cfg, "follow_modes", ("CW",)):
            return False
        filt = getattr(app, "spotter_filter", None)
        if filt is not None:
            accept = filt.accept if skimmer else getattr(filt, "people_accept", filt.accept)
            if not accept(spot.spotter):
                return False
        sf = getattr(app, "spotted_filter", None)
        if check_dx and sf is not None and not sf.accept(spot.dxcall):
            return False
        from .rbn import is_beacon
        if getattr(cfg, "hide_beacons", True) and is_beacon(spot):
            return False
        from .bands import is_warc
        return not (getattr(cfg, "hide_warc", False) and is_warc(spot.freq_khz))

    def _convert(self, spot, output):
        parsed = parse_header(output, self.engine.clock())
        if parsed is None:
            return replace(spot, collector_routed=False, collector_selected=True)
        return replace(spot, dxcall=parsed.call, freq_khz=parsed.frequency_hz / 1000,
                       raw_line=output.decode("latin-1"), collector_routed=False,
                       collector_selected=True)

    def _sync(self, app):
        enabled = bool(getattr(app.cfg, "cluster_merge", True))
        if self.enabled is not None and enabled != self.enabled:
            self.engine = SpotProcessor(self.engine.config, clock=self.engine.clock)
            self.templates.clear()
            self.epochs.clear()
            self.selected.clear()
            self.deferred.clear()
        self.engine.admission = lambda obs, call, frequency: self._admission(app, obs, call, frequency)
        self.enabled = enabled

    @staticmethod
    def _wire(spot):
        if spot.raw_line:
            return spot.raw_line.encode("latin-1", "replace")
        # Evidence serialization must never use the width-limited display
        # formatter: clipping a receiver ID can invent an independent vote.
        comment = spot_comment(spot)
        if spot.mode_guessed:
            comment = ""  # a guessed API mode is not independent mode evidence
        stamp = f" {spot.time_utc}" if spot.time_utc else ""
        return (f"DX de {spot.spotter}: {spot.freq_khz:.6f} "
                f"{spot.dxcall} {comment}{stamp}").encode("latin-1", "replace")

    @staticmethod
    def _template_key(source, header):
        return (source, header.receiver_base, header.call, header.band, header.mode)

    @staticmethod
    def _deferred_key(spot):
        return spot.feed_id, spot.spotter, spot.key, spot.mode

    def _put(self, store, key, spot):
        store[key] = spot
        store.move_to_end(key)
        while len(store) > self.engine.config['max_entries']:
            store.popitem(last=False)

    def _remember(self, spots):
        for spot in spots:
            key = (spot.dxcall.upper(), band_from_khz(spot.freq_khz), spot.mode)
            self._put(self.selected, key, spot)
        return spots

    def _template(self, obs):
        if self._active is not None:
            source, wire, spot = self._active
            if source == obs.source and wire == obs.spot.raw:
                return spot
        return self.templates.get(self._template_key(obs.source, obs.spot))

    def _admission(self, app, obs, call, frequency):
        template = self._template(obs)
        return template is not None and self.wanted(
            app, replace(template, dxcall=call, freq_khz=frequency / 1000))

    def process(self, app, spot, *, replay=False):
        self._sync(app)
        source = spot.feed_id or spot.source or "rbn"
        epoch = (spot.feed_epoch, self.role(app.cfg, spot))
        if not replay:
            if source in self.epochs and self.epochs[source] != epoch:
                self.engine.reset_source(source)
            self.epochs[source] = epoch
        # Connection invalidation must precede any held correction evaluation.
        released = []
        if spot.history or not self.enabled:
            outputs = [replace(spot, collector_routed=False, collector_selected=True)] if self.wanted(app, spot) else []
            return self._remember(outputs) + self.drain(app, through=spot.arrived or spot.received)
        wire = self._wire(spot)
        header = parse_header(wire, self.engine.clock())
        key = self._template_key(source, header) if header else None
        hold = self.engine.local_call_holds.get(key)
        received_at = spot.arrived or spot.received
        if hold is not None and (hold.force_original or
                self.engine.clock() >= hold.deadline and received_at >= hold.deadline):
            # Retire an old same-identity hold before a genuinely later packet
            # can replace it. Other timely queued evidence is learned first.
            released = self.drain(app, keys={key})
        receiver_wanted = self.wanted(app, spot, check_dx=False)
        self._active = (source, wire, spot)
        try:
            outputs = self.engine.process(
                source, epoch[1], wire, admit=receiver_wanted, replay=replay,
                received_at=spot.arrived or spot.received)
        finally:
            self._active = None
        hold = self.engine.local_call_holds.get(key)
        if hold is not None:
            # A stale/rejected repeat did not replace the held observation.
            if hold.observation is self.engine.last_observation:
                self._put(self.templates, key, spot)
        else:
            self.templates.pop(key, None)
        evaluation = self.engine.last_evaluation or {}
        deferred_key = self._deferred_key(spot)
        if not receiver_wanted:
            self._put(self.deferred, deferred_key, spot)
        elif evaluation.get('reason') == 'filtered' and header is not None:
            # Keep an already evaluated identity, never resurrect its old typo.
            candidate = self._convert(spot, header.render(
                evaluation['frequency_hz'], evaluation['call']))
            self._put(self.deferred, deferred_key, candidate)
        else:
            self.deferred.pop(deferred_key, None)
        selected = [self._convert(spot, output) for output in outputs]
        if not replay and self.engine.last_observation is not None and receiver_wanted:
            self._note_observation(app, spot, evaluation)
        if evaluation and (not outputs or evaluation.get('frequency_basis') != 'input'):
            traffic.emit("collector", f"{spot.dxcall} {spot.freq_khz:.2f}: "
                         f"{evaluation.get('reason', '')}; {evaluation.get('frequency_basis', '')}")
        return released + self._remember(selected) + self.drain(app, through=received_at)

    def _note_observation(self, app, spot, evaluation):
        observed = replace(spot, dxcall=evaluation.get('call', spot.dxcall),
                           freq_khz=evaluation.get('frequency_hz', spot.freq_khz * 1000) / 1000)
        note = getattr(app, "_note_own", None)
        if note is not None and not evaluation.get('held') and self.wanted(app, observed):
            note(observed)

    def drain(self, app, keys=None, through=None):
        self._sync(app)
        selected = []
        for event in self.engine.drain_local_holds(keys=keys, through=through):
            header = parse_header(event['input'], self.engine.clock())
            template = self.templates.pop(self._template_key(event['source'], header), None)
            if template is None:
                continue
            if not event['delta'].get('stale_spots'):
                self._note_observation(app, template, event['evaluation'])
            for output in event['outputs']:
                selected.append(self._convert(template, output))
            if event['evaluation'].get('reason') == 'filtered':
                evaluation = event['evaluation']
                candidate = self._convert(template, header.render(
                    evaluation['frequency_hz'], evaluation['call']))
                self._put(self.deferred, self._deferred_key(template), candidate)
        return self._remember(selected)

    def readmit(self, app):
        """Replay selected records or previously filtered candidates, not rejects.

        Deferred candidates may consult retained evidence but cannot add votes.
        Previously suppressed outliers and corrected-away aliases are absent.
        """
        self._sync(app)
        now = self.engine.clock()
        results = []
        keys = set(app.spots)
        for spot in list(self.selected.values()):
            if now - spot.received <= 900 and spot.key not in keys and self.wanted(app, spot):
                results.append(replace(spot, collector_routed=False, collector_selected=True))
                keys.add(spot.key)
        for spot in list(self.deferred.values()):
            if now - spot.received > self.engine.config['max_age_seconds']:
                continue
            for selected in self.process(app, spot, replay=True):
                if selected.key not in keys:
                    results.append(selected)
                    keys.add(selected.key)
        return results

    def status(self):
        counts = self.engine.snapshot()
        text = (f"Collector: {'on' if self.enabled else 'off'} | "
                f"selected {counts.get('delivered_spots', 0)} | "
                f"repeats suppressed {counts.get('suppressed_repeats', 0)} | "
                f"moves confirmed {counts.get('confirmed_moves', 0)} | "
                f"calls corrected {counts.get('callsign_corrections', 0)}")
        rows = self.engine.alignment_snapshot()
        rows.sort(key=lambda row: (row['state'] != 'Ready', row['last_match_age_seconds'] or 0))
        for row in rows[:40]:
            offset = row['offset_hz']
            adjustment = 'unavailable' if offset is None else f'{offset:+.1f} Hz'
            text += (f"\n{row['source']} / {row['receiver']} / {row['band']}: "
                     f"{row['state']}, adjustment {adjustment}, "
                     f"{row['samples']}/{row['required']} fresh distinct calls")
            if row['rejection_reason']:
                text += f" ({row['rejection_reason']})"
        if len(rows) > 40:
            text += f"\n{len(rows) - 40} additional receiver/band rows omitted"
        return text
