"""Weekly contest sessions, repeat-QSO periods and multiplier scopes.

Sources and verified N1MM identifiers are documented in docs/CONTEST_PROFILES.md.
No logger state is written here. All times are UTC; the clock is supplied by callers.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import re
import time

UTC = timezone.utc


@dataclass(frozen=True)
class Profile:
    name: str
    weekday: int
    minute: int
    period_seconds: int
    bands: tuple[str, ...]
    multiplier: str = 'call'

    def session(self, now: float) -> Session:
        dt = datetime.fromtimestamp(now, UTC)
        day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        day -= timedelta(days=(dt.weekday() - self.weekday) % 7)
        start = (day + timedelta(minutes=self.minute)).timestamp()
        return Session(self, start, now)


@dataclass(frozen=True)
class Session:
    profile: Profile
    start: float
    now: float

    @property
    def end(self) -> float:
        return self.start + 3600

    @property
    def active(self) -> bool:
        return self.start <= self.now < self.end

    @property
    def period(self) -> int | None:
        return int((self.now - self.start) // self.profile.period_seconds) if self.active else None

    def contains(self, timestamp: float | None) -> bool:
        return timestamp is not None and self.start <= timestamp < self.end

    def worked_in_period(self, timestamp: float | None) -> bool:
        return self.active and self.contains(timestamp) and int(
            (timestamp - self.start) // self.profile.period_seconds) == self.period

    def status(self) -> str:
        date = datetime.fromtimestamp(self.start, UTC).strftime('%Y-%m-%d %H:%M')
        prefix = f'{self.profile.name} {date} UTC'
        if not self.active:
            next_start = self.start if self.now < self.start else self.start + 7 * 86400
            return prefix + ' · outside session · next ' + datetime.fromtimestamp(
                next_start, UTC).strftime('%Y-%m-%d %H:%M UTC')
        boundary = self.start + (self.period + 1) * self.profile.period_seconds
        seconds = max(0, int(boundary - self.now))
        period = (f' · period {self.period + 1}/{3600 // self.profile.period_seconds}'
                  if self.profile.period_seconds < 3600 else '')
        return prefix + period + f' · {seconds // 60:02d}:{seconds % 60:02d} to ' + (
            'end' if boundary == self.end else 'next period')


PROFILES = {
    'ttc': Profile('TTC-SPCWC', 1, 19 * 60 + 30, 900, ('80m', '40m')),
    'mini40': Profile('MINI40', 2, 17 * 60, 600, ('40m',)),
    'mini80': Profile('MINI80', 2, 18 * 60, 600, ('80m',)),
    'mwc': Profile('OK1WC Memorial', 0, 16 * 60 + 30, 3600, ('80m', '40m'), 'suffix'),
}
# Exact identifiers only. MINI40/MINI80 are explicit override names, not
# invented N1MM aliases. MINITESTCW is N1MM's documented 80m Mini-Test.
IDENTIFIERS = {'TTC-SPCWC': 'ttc', 'UDC_TTC-SPCWC': 'ttc',
               'OK1WC': 'mwc', 'UDC_OK1WC': 'mwc', 'MINITESTCW': 'mini80'}


def profile_for(contest: str, override: str = 'auto') -> Profile | None:
    if override == 'off':
        return None
    key = IDENTIFIERS.get((contest or '').strip().upper()) if override == 'auto' else override
    return PROFILES.get(key)


def utc_timestamp(value) -> float | None:
    """N1MM TS/timestamp is UTC, including when no timezone suffix is supplied."""
    if value is None or value == '':
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        return dt.replace(tzinfo=UTC).timestamp() if dt.tzinfo is None else dt.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def contact_id(value) -> str:
    return str(value or '').strip().lower().replace('-', '').strip('{}')


def suffix_character(call: str) -> str | None:
    # Remove portable designators and geographic prefixes, preserving suffix
    # digits (A-Z and 0-9 are both multipliers in MWC).
    parts = call.upper().split('/')
    candidates = [p for p in parts if re.search(r'\d[A-Z]', p)]
    if not candidates:  # special calls whose suffix ends in a digit
        candidates = [p for p in parts if re.search(r'[A-Z].*\d|\d.*[A-Z]', p)]
    base = max(candidates, key=len, default='')
    return base[-1] if base and base[-1].isalnum() else None


@dataclass(frozen=True)
class Contact:
    call: str
    band: str
    mode: str
    timestamp: float | None
    guid: str = ''

    @property
    def key(self) -> tuple:
        return ('id', self.guid) if self.guid else ('qso', self.timestamp, self.call, self.band, self.mode)


class SessionLog:
    """Database snapshot with unacknowledged UDP edits replayed over it.

    Patches stay until confirmed or superseded by an authoritative reread
    after the write grace. A pre-packet background read cannot undo a packet.
    Missing identities/timestamps defer to the next database read.
    """
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.pending_since = {}
        self.records: dict[tuple, Contact] = {}
        self.pending: dict[tuple, Contact | None] = {}
        self.retired: set[tuple] = set()
        self.warning = ''

    def reconcile(self, records: list[Contact], read_started: float | None = None) -> None:
        self.warning = ""
        baseline = {r.key: r for r in records}
        for key, value in list(self.pending.items()):
            confirmed = (value is None and key not in baseline) or baseline.get(key) == value
            if value is not None and not value.guid:
                # An ID-less packet may precede a row with a database GUID.
                confirmed = confirmed or sum((r.call, r.band, r.mode, r.timestamp) ==
                    (value.call, value.band, value.mode, value.timestamp)
                    for r in baseline.values()) == 1
            # UDP has no edit-version number. After a five-second write grace,
            # a database snapshot begun after the edit is authoritative. Reads
            # begun before a packet can never roll it back.
            authoritative = (read_started is not None and read_started >=
                             self.pending_since.get(key, float('inf')) + 5)
            post_packet = (read_started is None or read_started >=
                           self.pending_since.get(key, float('inf')))
            if (confirmed and post_packet) or authoritative:
                del self.pending[key]
                self.pending_since.pop(key, None)
        baseline.update({k: v for k, v in self.pending.items() if v is not None})
        for key, value in self.pending.items():
            if value is None:
                baseline.pop(key, None)
        self.records = baseline

    def event(self, kind: str, record: Contact, oldcall='', oldtimestamp=None) -> bool:
        current = self.records.get(record.key)
        if kind == 'contactinfo' and (record.key in self.retired or current is not None):
            return False  # duplicate or delayed pre-edit creation
        if kind == 'contactdelete' and current is not None and (
                record.timestamp is not None and current.timestamp != record.timestamp
                or record.call and current.call != record.call):
            return False  # a delayed delete of the pre-edit record
        if record.timestamp is None and kind != 'contactdelete':
            self.warning = 'Contact UTC timestamp missing; waiting for database'
            return False
        if not record.guid or current is None:
            # GUIDs are preferred. Otherwise match a single original call/time,
            # never every occurrence of that call on a band.
            target_call = oldcall or record.call
            target_ts = oldtimestamp if oldtimestamp is not None else record.timestamp
            hits = [(k, r) for k, r in self.records.items() if r.call == target_call
                    and r.timestamp == target_ts and r.band == record.band]
            if kind == 'contactinfo' and len(hits) == 1 and (
                    not record.guid or not hits[0][1].guid or hits[0][1].guid.startswith('row:')):
                return False
            if not record.guid and kind != 'contactinfo':
                if len(hits) != 1:
                    self.warning = 'Contact identity ambiguous; waiting for database'
                    return False
                oldkey, old = hits[0]
                if old.guid:
                    record = replace(record, guid=old.guid)
                else:
                    self.pending[oldkey] = None
                    self.pending_since[oldkey] = self.clock()
                    self.records.pop(oldkey, None)
        self.pending_since[record.key] = self.clock()
        if kind == 'contactdelete':
            self.pending[record.key] = None
            self.records.pop(record.key, None)
            self.retired.add(record.key)
        else:
            self.pending[record.key] = record
            self.records[record.key] = record
            if kind == 'contactreplace':
                self.retired.add(record.key)
        return True

    def snapshot(self, session: Session) -> tuple[set, list[Contact], set]:
        rows = [r for r in self.records.values() if session.contains(r.timestamp)
                and r.band in session.profile.bands and r.mode == 'CW']
        keys = {(r.call, r.band, r.mode) for r in rows if session.worked_in_period(r.timestamp)}
        mults = {(r.band, suffix_character(r.call)) if session.profile.multiplier == 'suffix'
                 else ('*', r.call) for r in rows}
        return keys, rows, {m for m in mults if m[1] is not None}
