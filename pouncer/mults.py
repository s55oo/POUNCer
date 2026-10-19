"""Multipliers of the open N1MM contest - what they are and how they count.

N1MM's built-in contest rules are compiled into N1MM; no file describes them.
What every log database does carry:

* the ``Contest`` table: for all built-in (and opened user-defined) contests
  ``Multiplier1Name`` / ``2`` / ``3`` - the DXLOG column each multiplier is
  counted from (``ZN``, ``CountryPrefix``, ``WPXPrefix``, ``Sect``,
  ``Exchange1`` ...). Only a hint: N1MM adapts the rules to where you are
  (SPDX from outside SP also counts SP provinces, but names only
  CountryPrefix);
* on every logged QSO ``IsMultiplier1`` / ``2`` / ``3`` = 1 when N1MM counted
  it as a new multiplier - N1MM's own decision.

So the slots start from the table names; as QSOs come in, the flags are
replayed to learn (a) the column of a slot the table doesn't name or names
with something that isn't a column, and (b) whether the multiplier counts per
band, per band and mode, or once per contest. Exchange values a skimmer spot
doesn't carry (section, DOK, province ...) come from the log (the station
worked on another band) or N1MM's call history.
"""

from __future__ import annotations

from .bands import band_from_khz

# DXLOG columns a multiplier can be counted from (what the slots may learn)
CANDIDATES = ("CountryPrefix", "ZN", "WPXPrefix", "Sect", "Exchange1", "MiscText",
              "GridSquare", "Continent", "CK", "Prec")
# values a spot's call gives us without the exchange (cty.dat / the call)
FROM_CALL = ("CountryPrefix", "ZN", "WPXPrefix", "Continent")
# N1MM call history (N1MM Admin.s3db, table CallHist) column -> DXLOG column
CALLHIST = {"Sect": "Sect", "Exchange1": "Exchange1", "MiscText": "MiscText",
            "Locator1": "GridSquare", "CK": "CK"}

LABELS = {"ZN": "zone", "CountryPrefix": "country", "WPXPrefix": "WPX prefix",
          "Sect": "section", "Exchange1": "exchange", "MiscText": "misc text",
          "GridSquare": "grid", "Continent": "continent", "CK": "check", "Prec": "precedence"}
SCOPES = ("band", "contest", "bandmode")
SCOPE_TEXT = {"band": "per band", "contest": "once per contest", "bandmode": "per band and mode"}

MIN_ROWS = 10      # QSOs with a value before the flags are trusted
MIN_FLAGS = 3      # ... and at least this many flagged as mult
MIN_AGREE = 0.9    # a learnt column must match N1MM's flags this well
DISTRUST = 0.6     # a named column matching N1MM's flags worse than this is not used


def normalize(field: str, value: object):
    """Comparable value: zone as int, text upper-case without N1MM's "*"
    (WAE-only entity) marker. None when empty / unknown."""
    if value is None:
        return None
    if field == "ZN":
        try:
            z = int(str(value).strip())
        except ValueError:
            return None
        return z if z > 0 else None
    text = str(value).strip().upper().lstrip("*")
    return text or None


def default_scope(field: str) -> str:
    return "contest" if field == "WPXPrefix" else "band"


def scope_key(scope: str, band: str | None, mode: str | None) -> str:
    if scope == "contest":
        return "*"
    if scope == "bandmode":
        return f"{band}|{mode}"
    return band or "?"


def agreement(rows: list, field: str, flag: int, scope: str) -> tuple[float, int, int]:
    """Replay the log in time order: does "value not seen yet in this scope"
    match N1MM's IsMultiplier flag? -> (share agreeing, rows with a value,
    rows flagged). ``rows``: dicts with band, mode, values {field: v}, flags."""
    seen: set = set()
    n = ok = flagged = 0
    for r in rows:
        v = r["values"].get(field)
        if v is None:
            continue
        f = bool(r["flags"][flag])
        k = (scope_key(scope, r["band"], r["mode"]), v)
        new = k not in seen
        seen.add(k)
        n += 1
        flagged += f
        ok += (new == f)
    return (ok / n if n else 0.0), n, flagged


def resolve_slots(names: list, cols: set, rows: list) -> list[dict]:
    """The contest's multiplier slots: [{n, field, scope, how, agree, flagged}].
    ``names`` = Multiplier1Name..3 (None / "N/A" = not named), ``cols`` the
    DXLOG columns, ``rows`` the contest's QSOs (time order) for learning."""
    by_lower = {c.lower(): c for c in cols}
    slots = []
    used: set = set()
    for i, name in enumerate(list(names)[:3]):
        n = i + 1
        flagged_any = any(r["flags"][i] for r in rows)
        raw = (str(name).strip() if name else "")
        field = by_lower.get(raw.lower()) if raw and raw.upper() != "N/A" else None
        how = "contest table"
        if field is not None and field not in CANDIDATES:
            field = None                      # a column, but not one we can count
        if field is None and not flagged_any:
            continue                          # not named, never flagged: no slot
        if field is not None and flagged_any:
            # the table names it - but does it match what N1MM flags? (CQMM:
            # "WPXPrefix", yet N1MM flags 1 QSO in 28) - if clearly not, learn
            best_named = max(agreement(rows, field, i, sc) for sc in SCOPES)
            if best_named[1] >= MIN_ROWS and best_named[0] < DISTRUST:
                raw = f"{raw} (doesn't match N1MM's flags)"
                field = None
        if field is None:                     # flagged but unnamed / unknown: learn it
            best = None
            for cand in CANDIDATES:
                if cand not in cols or cand in used:
                    continue
                for sc in SCOPES:
                    a, cnt, fl = agreement(rows, cand, i, sc)
                    if cnt >= MIN_ROWS and fl >= MIN_FLAGS and a >= MIN_AGREE and \
                            (best is None or a > best[0] + 1e-9):
                        best = (a, cand, sc, fl)
            if best is None:
                slots.append({"n": n, "field": None, "name": raw or None, "scope": "band",
                              "how": "unknown", "agree": None,
                              "flagged": sum(1 for r in rows if r["flags"][i])})
                continue
            a, field, scope, fl = best
            used.add(field)
            slots.append({"n": n, "field": field, "name": raw or None, "scope": scope,
                          "how": "learnt from the log", "agree": round(a, 3), "flagged": fl})
            continue
        used.add(field)
        scope = default_scope(field)
        agree = None
        fl = sum(1 for r in rows if r["flags"][i])
        if flagged_any:                       # the log tells per band / contest / mode
            base, cnt, fl = agreement(rows, field, i, scope)
            agree = base
            if cnt >= MIN_ROWS and fl >= MIN_FLAGS:
                for sc in SCOPES:
                    a, _, _ = agreement(rows, field, i, sc)
                    if a > agree + 0.02:
                        scope, agree, how = sc, a, "contest table, scope learnt from the log"
        slots.append({"n": n, "field": field, "name": raw, "scope": scope, "how": how,
                      "agree": round(agree, 3) if agree is not None else None, "flagged": fl})
    return slots


def describe(slots: list[dict], zone_kind: str = "CQ") -> str:
    """'CQ zone per band + country per band' for the window / phone."""
    parts = []
    for s in slots:
        if s["field"] is None:
            parts.append(f"mult {s['n']} ({s.get('name') or 'unnamed'}: not known)")
            continue
        label = LABELS.get(s["field"], s["field"])
        if s["field"] == "ZN":
            label = f"{zone_kind} zone"
        parts.append(f"{label} {SCOPE_TEXT.get(s['scope'], s['scope'])}"
                     + (" (learnt)" if s["how"] != "contest table" else ""))
    return " + ".join(parts)


def band_of(freq_khz: float) -> str | None:
    return band_from_khz(freq_khz)
