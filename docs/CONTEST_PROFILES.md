# Weekly contest profiles

Verified 2026-11-10. All schedules below are **UTC**, with start inclusive
and end exclusive. These profiles guide worked status and multiplier
recommendations, not adjudicated scores or N1MM configuration.

| Profile override | Session | Repeat eligibility | Multiplier scope |
| --- | --- | --- | --- |
| `ttc` | Tuesday 19:30–20:30, CW, 80m + 40m | Once per call/band in each of four fixed 15-minute stages | Unique full callsign across both bands, whole round |
| `mini40` | Wednesday 17:00–18:00, CW, 40m | Once per call in each of six fixed 10-minute periods | Unique callsign, whole band session |
| `mini80` | Wednesday 18:00–19:00, CW, 80m | Once per call in each of six fixed 10-minute periods | Unique callsign, whole band session |
| `mwc` | Monday 16:30–17:30, CW, 80m + 40m | Once per call/band in the whole session; no period reset | Final suffix character A–Z or 0–9, separately per band |

TTC boundaries are 19:30, 19:45, 20:00, 20:15 and 20:30. Mini-Test boundaries
are at minutes 00, 10, 20, 30, 40, 50 and the following hour. A QSO just before
a boundary does not make that station worked in the next period. A station
can still be a repeat-QSO opportunity without being a new multiplier.

## Sources and N1MM identification

- [TTC organizer rules](https://www.spcwc.pl/ttc/rules/en), version
  20261024_01: four stages, unique-call multiplier across the round, and
  continuous serials. The [organizer's N1MM download](https://www.spcwc.pl/ttc/download)
  serves `ttc-spcwc.udc` (`Name=TTC-SPCWC`, `MultipleSessions=0000/15`).
- [Mini-Test organizer](https://minitest.narod.ru/): twelve ten-minute periods
  across two successive band hours, separate serials/results per band.
- [MWC organizer rules](https://memorial-ok1wc.cz/index.php?page=rules2l):
  one QSO per band per session, suffix-character multipliers per band.
- [N1MM supported contests](https://n1mmwp.hamdocs.com/manual-supported/contests-list/)
  lists `MINITESTCW` and `UDC_OK1WC`.
  [N1MM MiniTest setup](https://n1mmwp.hamdocs.com/manual-supported/contests-setup/setup-hf-contests/)
  documents the 18:00, 80m, six-period variant. The
  [UDC instructions](https://n1mmwp.hamdocs.com/manual-supported/contests-setup/setup-udc-contests/)
  identify `OK1WC.udc`. The [downloaded UDC](https://n1mmwp.hamdocs.com/mmfiles/ok1wc-udc/)
  (revision 1.0.2) confirms `Name=OK1WC` and `MultSqlString=LASTLETTER`.
  Its legacy `Mode=BOTH` predates the organizer's CW-only rule; this profile
  follows the current organizer rules and does not rewrite the UDC.
- [N1MM Contacts protocol](https://n1mmwp.hamdocs.com/appendices/external-udp-broadcasts/)
  documents `ID`, `timestamp`, `oldcall`, `oldtimestamp`, and delete/replace
  pairs. [Database/ADIF notes](https://n1mmwp.hamdocs.com/appendices/technical-information/)
  document the unique contact identifier.

`[worked] profile = auto` matches exact names only, ignoring case:

| N1MM identifier | Profile |
| --- | --- |
| `TTC-SPCWC`, `UDC_TTC-SPCWC` | `ttc` (UDC Name plus N1MM's documented UDC prefix convention) |
| `OK1WC`, `UDC_OK1WC` | `mwc` (UDC name / supported-list identifier) |
| `MINITESTCW` | `mini80` (documented N1MM 80m variant) |

Separate N1MM identifiers for MINI40 and MINI80 were not verified. Do not
infer a schedule from a substring or a selected band. Choose `mini40` or
`mini80` explicitly in **Settings → Program → Contest session profile** for
those sessions, including when reusing MINITESTCW for 40m. Restart POUNCer
after changing this setting. Explicit profiles also support differently
named user-defined contests. `off` disables profile handling. Unknown names
in `auto` retain ordinary whole-contest behavior. Settings do not modify
N1MM's UDC, duplicate checking, exchange, serials, or database.

## Session and display behavior

Session identity includes its UTC calendar date and scheduled start,
independently of N1MM's ContestNR. Older weekly rounds cannot mark calls
worked in the new round. During each period POUNCer builds worked keys from
only that period; QSO counts and multiplier sets use the whole session.
Counts include logged duplicate rows, consistent with the existing log
reader; they are not a score certified by the organizer.

The UI checks the injected/system clock on every tick. Period and session
changes, including sleep/resume or clock corrections, refresh desktop lists,
worked colours, Hide worked, and all cached per-radio/combined-band browser
views. The rate line and summary use the same session snapshot and multiplier
scope. Run-history percentages/hours and the live CQ-duration tracker remain
independent of this eligibility calculation.

A wrapping browser status line and a dedicated desktop session line show
the session and countdown. The desktop line stays visible in both clean
and detailed layouts, independently of the optional log-details panel. Outside a session no period is active: worked flags and new-mult
recommendations are inactive. On the scheduled weekday the displayed session
is today's, including before its start; on other days it is the most recent
scheduled session. The next start is displayed outside the active hour.

## Database and live Contacts reconciliation

Enable N1MM **App Info + Contacts**. App Info identifies the open contest;
a late contact from a different contest does not change that selection.
Contact event sequence numbers remain monotonic across App Info packets.

Database snapshots are read-only. QSO timestamps (`TS` / `timestamp`) are
interpreted as UTC; packet arrival time is never substituted. Missing or
invalid timestamps are excluded from profiled counts/eligibility with a
visible warning. Unsupported bands/modes are excluded from profile totals.

A contact's GUID (`ID`) identifies additions, replacements and deletions.
Database rows without GUIDs use SQLite row identity; ID-less UDP changes use
an exact original call/time/band match only when unambiguous. Old timestamps
and calls identify edits across periods. An ambiguous change waits for the
next database read rather than deleting every repeat of that station.
Identical ID-less creation packets are deduplicated against a known contact.

Pending UDP patches are replayed over stale database snapshots until the
database confirms them. A snapshot begun before a packet cannot undo it.
Because UDP has no edit-version number, an authoritative database snapshot
begun at least five seconds after an unmatched edit supersedes the pending
patch; the existing forced reread (30 seconds) provides eventual recovery
from dropped/reordered edits and missed packets. Duplicate/delayed creation
packets and deletes identifying a pre-edit timestamp/call/band are ignored.
Original fallback identities are retained after an edit is acknowledged,
so a delayed ID-less creation cannot resurrect its original callsign/time.
Arbitrarily reordered replacements with identical GUID/call/time cannot be
version-ordered from the protocol alone; the database resolves them.

Physical logger/radio operation still depends on the existing N1MM setup.
These profiles do not initiate tuning or transmission on a period change.
