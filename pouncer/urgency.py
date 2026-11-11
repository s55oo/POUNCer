"""Historical opportunity hints, not probabilities or departure predictions.

Verified duration sources and policy are documented in docs/URGENCY.md.
"""
from __future__ import annotations

import math

RUN_THRESHOLD = 30
HOURS_CAP = 6
DURATION_FRACTION = 0.25

# Keep CW/SSB variants explicit: a similarly named RTTY event can have
# different rules. Weekly durations are the entire band session, not a stage.
DURATIONS = {
    'CQWW': 48, 'CQWWCW': 48, 'CQWWSSB': 48,
    'CQWPX': 48, 'CQWPXCW': 48, 'CQWPXSSB': 48,
    'ARRL': 48, 'ARRLDXCW': 48, 'ARRLDXSSB': 48,
    'TTC-SPCWC': 1, 'MINI40': 1, 'MINI80': 1, 'MINITESTCW': 1,
    'OK1WC': 1,
}


# N1MM contest name prefix -> archive contest folder.
ARCHIVE_PREFIXES = {
    "CQWWRTTY": "CQWWRTTY", "CQWPXRTTY": "CQWPXRTTY", "CQWW": "CQWW",
    "CQWPX": "CQWPX", "CQ160": "CQ160", "ARRLDX": "ARRL", "WAE": "WAE",
    "EUHFC": "EUHFC", "OKOMDX": "OK_OM_DX_Contest", "SPDX": "SPDX_contest",
    "UKRAINDX": "URE", "YUDX": "YU_DX_Contest", "KVP": "ZRS_KVP",
}

def name_key(name: str) -> str:
    name = (name or '').strip().upper().replace(' ', '')
    return name[4:] if name.startswith('UDC_') else name


def history_key(name: str) -> str:
    name = name_key(name)
    return next((value.upper() for prefix, value in ARCHIVE_PREFIXES.items()
                 if name.startswith(prefix)), name)


def number(value, low=0, high=float('inf')) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and low <= value <= high else None
    except (ValueError, TypeError):
        return None


def format_hours(hours, *, unrounded=True) -> str:
    hours = number(hours)
    if hours is None:
        return ''
    if hours < 1:
        return f'~{round(hours * 60)}m' if unrounded else f'~{hours:g}h'
    return f'~{round(hours)}h'


def classify(info: dict, contest: str, duration_minutes=0) -> dict:
    """Classify only known evidence; provenance and unrounded hours are required.

    Existing cache entries retain their displayed numbers, but cannot acquire
    a matching-contest claim unless the source supplies it.
    """
    override = number(duration_minutes, high=10080)
    duration = override / 60 if override else DURATIONS.get(name_key(contest))
    run = number(info.get('run'), high=100)
    hours = number(info.get('hours'))
    unrounded = info.get('hours_unrounded') is True
    contexts = info.get('history_contests')
    matching = (isinstance(contexts, list) and len(contexts) == 1
                and isinstance(contexts[0], str)
                and history_key(contexts[0]) == history_key(contest))
    limit, fraction = None, None
    if duration is None:
        note = 'Contest duration unknown; hours hint unavailable.'
    elif not matching:
        note = 'History contest is different or unverified; hours hint unavailable.'
    elif hours is None:
        note = 'Operating hours unknown; hours hint unavailable.'
    elif not unrounded:
        note = 'Legacy operating hours are rounded; hours hint unavailable until history is refreshed.'
    elif hours > duration:
        note = 'Historical hours exceed contest duration; hours hint unavailable.'
    else:
        limit = min(HOURS_CAP, duration * DURATION_FRACTION)
        fraction = hours / duration
        note = (f'Hours hint: below {limit:g} h (25% of the {duration:g} h contest, '
                'capped at 6 h).')
    reasons = []
    if run is not None and run < RUN_THRESHOLD:
        reasons.append(f'Historically ran for {run:g}% of measurable QSOs (below 30%).')
    if limit is not None and hours < limit:
        reasons.append(f'Recorded about {hours:g} h in a {duration:g} h contest.')
    if info.get('rec'):
        note += ' Reconstructed log coverage may be incomplete.'
    medium_limit = min(12, duration * 0.5) if limit is not None else None
    medium = []
    if run is not None and run < 60:
        medium.append(f'Historical run share {run:g}% is below 60%.')
    if medium_limit is not None and hours < medium_limit:
        medium.append(f'Historical operating time is below {medium_limit:g} h (50%, capped at 12 h).')
    level = ('urgent' if reasons else 'medium' if medium else
             'normal' if run is not None or limit is not None else 'unknown')
    explanation = reasons or medium or ([
        'No elevated historical priority detected; this does not promise continued availability.'
    ] if level == 'normal' else ['Not enough usable history to assign a priority.'])
    confidence = 'limited' if not matching or not unrounded or info.get('rec') else 'matching-contest history'
    return {'urgency': level, 'urgency_reasons': explanation, 'urgency_confidence': confidence,
            'hours_text': format_hours(hours, unrounded=unrounded), 'rare': bool(reasons), 'rare_reasons': reasons, 'rare_hours_limit': limit,
            'hours_fraction': fraction, 'contest_hours': duration, 'history_matches': matching,
            'urgency_note': note, 'history_contests': contexts if isinstance(contexts, list) else []}
