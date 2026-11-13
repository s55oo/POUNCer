# Historical opportunity hints

The ⚡ mark describes historical run/participation patterns. It does **not**
estimate the probability of departure, predict remaining CQ time, or tell the
operator that another station can safely wait. Low run share and limited
participation can suggest an opportunity worth considering, but neither
establishes when the station will leave its frequency or stop operating.

## Policy

A station gets a historical opportunity hint when either:

1. Known historical run share is **below 30%**; or
2. Known matching-contest operating hours are **below the smaller of six
   hours and 25% of the full contest duration**.

The 25% fraction is an explicit heuristic policy choice, not a fitted or
validated probability model. Boundaries are strict: exactly 25% / six hours
does not trigger the hours criterion. Six hours remains the cap for long
contests. Repeat-QSO stages are not the event duration.

| Event duration | Hours threshold | Example |
| --- | --- | --- |
| 1 hour | 15 minutes | 48 minutes alone does not flag; 12 minutes can |
| 12 hours (explicit override) | 3 hours | 4 hours alone does not flag |
| 24 hours (explicit override) | 6 hours | Existing six-hour cap applies |
| 48 hours | 6 hours | Existing six-hour cap applies |

Unknown duration, unknown/mixed/different source contest, missing/invalid
hours, legacy rounded hours, or hours exceeding event duration disable the
hours criterion. Low run share can still generate a hint, explicitly as
historical evidence.
A missing flag is not evidence that a station will remain available.

## Verified automatic durations

Verified 2026-11-11, using full event windows rather than category-specific
operating-time allowances:

- **CQ WW CW/SSB: 48 hours**, [organizer rules](https://www.cqww.com/rules/).
- **CQ WPX CW/SSB: 48 hours**, [organizer rules](https://cqwpx.com/rules/).
- **ARRL International DX CW/SSB: 48 hours**, [organizer information](https://www.arrl.org/arrl-dx).
- **TTC-SPCWC: 1 hour**, [organizer rules](https://www.spcwc.pl/ttc/rules/en).
- **MINI40 / MINI80: 1 hour each**, [organizer rules](https://minitest.narod.ru/).
- **OK1WC Memorial: 1 hour**, [organizer rules](https://memorial-ok1wc.cz/index.php?page=rules2l).

The duration table accepts exact names in `pouncer/urgency.py`, including
N1MM's `MINITESTCW`, `OK1WC` and `TTC-SPCWC` names, and their `UDC_` prefix.
CW/SSB variants are explicit; an unlisted RTTY or similarly named contest does
not inherit a duration. Archive names CQWW, CQWPX and ARRL also work.
This recognition is for historical hints, not a new logger contest definition.

Other events can set **Settings → Alerts & history → History hint: contest duration**
(`[ui] urgency_contest_minutes`). Zero means the verified automatic table;
a positive integer supplies the full event duration in minutes (maximum one
week). The setting is live and does not change logging/scoring. Reset it to
zero when changing contests. It does not override source-history matching.

## Source context and compatibility

Run percentages/hours are still calculated from the same Cabrillo history.
Hours represent summed QSO gaps of at most 30 minutes, averaged across
selected logs. They are an estimate of recorded activity, not exact on-time.
Measurement and averaging retain the unrounded activity time; only the
display rounds it. Exactly 15 recorded minutes in a one-hour contest therefore
remain at the strict threshold and display as `~15m`.
Reconstructed logs may have incomplete coverage and keep the `*` marker.

Archive lookups now retain `history_contests` for the **actual selected logs**,
including when the requested contest falls back to other same-mode contests.
Only one matching source contest enables the hours criterion. Existing
archive-name mappings normalize N1MM names for matching; they do not supply
unverified durations. This avoids comparing another event's participation
hours with the current event's duration.

Precomputed warm-up rows have an explicit contest column, so loading them
supplies verified context. A verified warm-up row replaces a legacy cached
value lacking source context rather than attaching a guessed source to old
numbers. JSON cache and team snapshots retain the new metadata. Old caches,
older peers and run-only lists remain readable: their R%/hours can be shown,
but unverified or rounded hours cannot create an hours-based hint at either
priority threshold. Newly measured summaries carry `hours_unrounded: true`;
new warm-up builds declare the equivalent `hours_unrounded=1` in their
SQLite `info` table. Loading one replaces legacy cached numbers, including
entries that already have contest context. Older warm-up files cannot replace
unrounded results or supply this precision marker. Old sub-hour values keep
their rounded hour unit (for example `~0.2h`), avoiding a misleading conversion
to `~12m`. The tooltip explains when rounded hours disable the hint.
An old cache entry outside the warm-up lists needs to be looked up afresh
(remove its cached result or the cache file) before the hours criterion can
use its provenance and precision; it is not silently attributed to the
requested contest.

Warm-up assets must be rebuilt with `Build warm-up list` after this change
to supply unrounded hours. Existing downloaded files keep working, with
hours-based hints disabled until a new build is downloaded (normally after
the 30-day cache interval; deleting `warm.sqlite` / `warm-ssb.sqlite` forces a
download at next startup).

The archive lookup contest mappings are unchanged. In particular, selecting
a TTC/Mini-Test/MWC contest does not claim the archive has matching history;
generic fallback remains visibly unverified/different for the hours rule.

## Presentation

Python produces one per-spot classification plus reasons, effective duration,
hours threshold, matching status and optional duration fraction. Desktop
badges/alerts and browser badges/alerts/⚡ sorting use that same decision.
The browser does not independently reapply the old six-hour rule. Unknown
unclassifiable history goes last in the historical-hint sort; DX and multiplier
sort options retain their existing precedence.

Desktop tooltips and browser tooltips/Debug expose the evidence and limits.
Alerts read **history hint + new mult**. Phrases promising that a station
will leave soon or can wait are removed. Sub-hour historical activity is shown
in minutes (`~12m`) instead of rounding to `~0h` or `~1h`.

This change is independent of PR #2's weekly session/duplicate rules, live CQ
tracking, clicked-station pinning, selected bands, N1MM control and transmission.

## ZO mode

ZO mode is a live, instance-wide setting: View → ZO mode (Ctrl+Z) or
Settings → Display → ZO mode, persisted
as `[ui] zo_mode = false` by default. It forces desktop tiles without overwriting
the previous table/tile preference and adds three permanent columns in desktop
and browser views. Narrow phones retain three columns; callsigns wrap.

The shared Python classifier in `pouncer/urgency.py` supplies both interfaces.
Run means the percentage of measurable historical QSOs made while running,
not run length in minutes. It is usable only when finite and between 0 and 100.

| Priority | Historical evidence (either condition suffices) |
| --- | --- |
| Urgent | Run share <30%, or comparable hours <min(6 h, 25% of event duration) |
| Medium | Not Urgent, and run share <60%, or comparable hours <min(12 h, 50%) |
| Normal | Usable evidence exists without an elevated-priority flag |
| Unknown | Neither usable run share nor comparable hours exists |

Unknown appears below a labelled divider in Normal. Normal's heading count
includes Unknown; the divider also shows its own count. Empty column headings
remain visible. Unknown does not imply low risk, and Normal does not guarantee
continued availability. No calibrated departure probabilities are calculated.

Hours must be finite, nonnegative, no greater than the event duration, and from
verified matching-contest history. Missing provenance in old caches, mixed or
different contests, unknown duration, and invalid values disable the hours rule.
Run-share evidence can still be used with limited confidence. Reconstructed
logs may omit activity; their evidence is explicitly marked limited. Provenance,
confidence, and limitations remain separate from priority in tooltips and browser
long-press details. History cannot establish whether a station is currently
calling CQ or when it will leave; current spot information remains separate.

The duration table uses full event duration, not individual operating limits or
15-minute stages. Supported aliases include CW/SSB variants and `UDC_` names.
A positive `[ui] urgency_contest_minutes` override supplies a custom event's
full duration; zero uses the table.

Duration sources:

- [CQ WW rules](https://cqww.com/rules/): Saturday 0000 through Sunday 2359 UTC,
  48 hours.
- [CQ WPX rules](https://cqwpx.com/rules/): 48-hour event; individual operating
  limits do not change its duration here.
- [ARRL DX rules](https://contests.arrl.org/ContestRules/DX-Rules.pdf): Saturday
  0000 through Sunday 2359 UTC, 48 hours.
- [SP TTC rules](https://www.spcwc.pl/ttc/rules/en): one-hour event; its stages
  do not become the historical-hours comparison duration.
- [Mini-Test rules](https://minitest.narod.ru/): MINI40 and MINI80 are separate
  one-hour sessions.
- [OK1WC Memorial rules](https://memorial-ok1wc.cz/index.php?page=rules2l):
  one-hour session.

Existing sorting runs independently inside each priority group, including
browser DX, MLT and lightning options. NEXT follows Urgent → Medium → Normal →
Unknown. Calm list retains its sorting delay and interaction holds; a change in
classification moves a station to its new group. Refreshing, sorting and grouping
never issue radio commands. Existing control permissions still gate clicks and
NEXT. Desktop canvas items and browser tile elements are reused across refreshes.
