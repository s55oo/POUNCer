"""Build POUNCer's warm-up list: run % and hours on the air for every station
of chosen contests, so POUNCer knows them all before the contest starts.

    python server/warmlist.py [--set cw|ssb] [--years 5] [--data DIR] [--out FILE]

``--set cw`` (default): CQWW CW, ARRL DX CW, CQ WPX CW -> warm.sqlite.
``--set ssb``: their phone versions (CQWW SSB, ARRL DX SSB, CQ WPX SSB) ->
warm-ssb.sqlite - a separate list, since who runs and for how long is
different on SSB than on CW.

Downloads only the needed folders of S53ZO's public
Hamradio-Contest-logs-Archives (partial clone + sparse checkout): for each
contest the submitted logs and the reconstructed logs of the last N years.
Per call, contest and mode it does what POUNCer does per call
(pouncer/runhist.py): newest submitted logs (up to MAX_LOGS) - or, if the
station never sent one, its reconstructed logs - run % over all their QSOs
and hours on the air averaged per contest.

Output ``warm.sqlite``, table ``warm``:
    call, contest, mode, run, hours, rec, logs, newest
Keys match POUNCer's (contest = archive folder name: CQWW / CQWPX / ARRL;
mode CW or PH). Built by .github/workflows/build-warmup.yml and published as
the release assets ``warmup/warm.sqlite`` and ``warmup/warm-ssb.sqlite``;
POUNCer downloads both at start ([ui] warmup).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pouncer.runhist import (  # noqa: E402
    MAX_LOGS, on_air_hours, qso_count, run_share, run_share_by_band, summarize,
)

ARCHIVE = "https://github.com/s53zo/Hamradio-Contest-logs-Archives.git"
# (key contest, mode, archive folder) - key contest as pouncer.runhist.CONTESTS
SETS = {
    "cw": (
        ("CQWW", "CW", "CQWW/cw"),
        ("ARRL", "CW", "ARRL/arrl_international_dx_cw_contest"),
        ("CQWPX", "CW", "CQWPX/cw"),
    ),
    "ssb": (
        ("CQWW", "PH", "CQWW/ph"),
        ("ARRL", "PH", "ARRL/arrl_international_dx_phone_contest"),
        ("CQWPX", "PH", "CQWPX/ph"),
    ),
}
OUT = {"cw": "warm.sqlite", "ssb": "warm-ssb.sqlite"}
CONTESTS = SETS["cw"]


def git(*args: str, cwd: str | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def log_call(text: str) -> str:
    for line in text.splitlines()[:60]:
        if line.upper().startswith("CALLSIGN:"):
            return line.split(":", 1)[1].strip().upper()
    return ""


def fetch(data: str, years: list[int], contests=CONTESTS) -> str:
    repo = os.path.join(data, "archive")
    if not os.path.isdir(os.path.join(repo, ".git")):
        git("clone", "--filter=blob:none", "--no-checkout", "--depth", "1", ARCHIVE, repo)
    else:
        git("fetch", "--depth", "1", "origin", "main", cwd=repo)
        git("reset", "--soft", "FETCH_HEAD", cwd=repo)
    patterns = [f"/{pre}{folder}/{y}/"
                for _, _, folder in contests for y in years
                for pre in ("", "RECONSTRUCTED_LOGS/")]
    git("sparse-checkout", "init", "--no-cone", cwd=repo)
    git("sparse-checkout", "set", "--no-cone", *patterns, cwd=repo)
    git("checkout", "-f", "HEAD", cwd=repo)
    return repo


def measure(repo: str, years: list[int], contests=CONTESTS):
    """{(call, contest, mode): {"real": [(year, run, qsos, hours)], "rec": [...]}}"""
    found: dict[tuple, dict[str, list]] = {}
    for contest, mode, folder in contests:
        for pre, kind in (("", "real"), ("RECONSTRUCTED_LOGS/", "rec")):
            for y in years:
                ydir = os.path.join(repo, pre + folder, str(y))
                if not os.path.isdir(ydir):
                    continue
                n = 0
                for name in os.listdir(ydir):
                    if not name.lower().endswith(".log"):
                        continue
                    with open(os.path.join(ydir, name), encoding="latin-1") as fh:
                        text = fh.read()
                    qsos = qso_count(text)
                    if qsos == 0:
                        continue
                    run, total = run_share(text)
                    call = log_call(text) or name[:-4].upper().replace("-", "/")
                    found.setdefault((call, contest, mode), {"real": [], "rec": []})[kind].append(
                        (y, run, total, on_air_hours(text), qsos, run_share_by_band(text)))
                    n += 1
                print(f"{contest} {mode} {y} {kind}: {n} logs", flush=True)
    return found


def aggregate(found) -> list[tuple]:
    """Like RunHistory._lookup: newest MAX_LOGS submitted logs, else rebuilt.
    run % overall (None if the logs have no usable frequencies - hours
    only), per band (JSON), hours on the air."""
    rows = []
    for (call, contest, mode), logs in found.items():
        rec = not logs["real"]
        pick = sorted(logs["real"] or logs["rec"], key=lambda p: p[0], reverse=True)[:MAX_LOGS]
        out = summarize([p[1:] for p in pick])
        if out is None:
            continue
        rows.append((call, contest, mode, out["run"], out["hours"], int(rec),
                     out["logs"], pick[0][0], json.dumps(out["bands"], separators=(",", ":"))))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", choices=sorted(SETS), default="cw",
                    help="cw = CW contests, ssb = their phone versions")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--data", default="warm-data")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    contests = SETS[a.set]
    a.out = a.out or OUT[a.set]
    this_year = datetime.date.today().year
    years = list(range(this_year - a.years, this_year + 1))
    os.makedirs(a.data, exist_ok=True)
    repo = fetch(a.data, years, contests)
    rows = aggregate(measure(repo, years, contests))
    tmp = a.out + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE warm (call TEXT, contest TEXT, mode TEXT, run INTEGER,"
                " hours REAL, rec INTEGER, logs INTEGER, newest INTEGER, bands TEXT,"
                " PRIMARY KEY (call, contest, mode))")
    con.executemany("INSERT OR REPLACE INTO warm VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.execute("CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO info VALUES (?,?)", [
        ("built", datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")),
        ("years", f"{years[0]}-{years[-1]}"),
        ("contests", ",".join(f"{c} {'SSB' if m == 'PH' else m}" for c, m, _ in contests)),
    ])
    con.commit()
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, a.out)
    print(f"{a.out}: {len(rows)} stations", flush=True)


if __name__ == "__main__":
    main()
