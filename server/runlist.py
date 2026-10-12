"""POUNCer run list server: how often each station RUNS, for a whole contest.

    python runlist.py build [--contest CQWW] [--years 3] [--data /data]
    python runlist.py serve [--port 8077] [--data /data]

``build`` downloads only the wanted contest's logs (last N years, CW + SSB)
from S53ZO's public Hamradio-Contest-logs-Archives (git partial clone +
sparse checkout, so the other ~2 million files are never fetched) and
writes ``runlist.sqlite``: per call, contest, mode and year the share of
QSOs made running (pouncer.runhist.run_share).

``serve`` answers POUNCer over HTTP (for ZeroTier / LAN):
    GET /runs?contest=CQWW&mode=CW  -> {"CALL": run %, ...} newest year per call
    GET /run?call=S53CC&contest=CQWW&mode=CW -> {"call", "run", "year", "qsos"}
    GET /status -> what is in the database
Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pouncer.runhist import MIN_QSOS, run_share  # noqa: E402

ARCHIVE = "https://github.com/s53zo/Hamradio-Contest-logs-Archives.git"
FOLDER_MODES = {"cw": "CW", "ph": "PH", "ssb": "PH", "rtty": "RTTY"}


def git(*args: str, cwd: str | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def fetch_logs(data: str, contest: str, years: int) -> str:
    """Partial clone with only <contest>/<mode>/<year> folders checked out."""
    repo = os.path.join(data, "archive")
    if not os.path.isdir(os.path.join(repo, ".git")):
        git("clone", "--filter=blob:none", "--no-checkout", "--depth", "1", ARCHIVE, repo)
    else:
        git("fetch", "--depth", "1", "origin", "main", cwd=repo)
        git("reset", "--soft", "FETCH_HEAD", cwd=repo)
    this_year = datetime.date.today().year
    patterns = [
        f"/{contest}/{folder}/{y}/"
        for folder in FOLDER_MODES
        for y in range(this_year - years, this_year + 1)
    ]
    git("sparse-checkout", "init", "--no-cone", cwd=repo)
    git("sparse-checkout", "set", "--no-cone", *patterns, cwd=repo)
    git("checkout", "-f", "HEAD", cwd=repo)
    return repo


def log_call(text: str) -> str:
    """The station's call from the Cabrillo header (CALLSIGN: EA6/DL6TK)."""
    for line in text.splitlines()[:60]:
        if line.upper().startswith("CALLSIGN:"):
            return line.split(":", 1)[1].strip().upper()
    return ""


def build(data: str, contest: str, years: int) -> None:
    repo = fetch_logs(data, contest, years)
    db = os.path.join(data, "runlist.sqlite")
    con = sqlite3.connect(db + ".new")
    con.execute("DROP TABLE IF EXISTS runs")
    con.execute("CREATE TABLE runs (call TEXT, contest TEXT, mode TEXT, year INTEGER,"
                " run INTEGER, qsos INTEGER, PRIMARY KEY (call, contest, mode, year))")
    n = 0
    base = os.path.join(repo, contest)
    for folder in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        mode = FOLDER_MODES.get(folder.lower())
        if not mode:
            continue
        for year in sorted(os.listdir(os.path.join(base, folder))):
            ydir = os.path.join(base, folder, year)
            if not (year.isdigit() and os.path.isdir(ydir)):
                continue
            for name in os.listdir(ydir):
                if not name.lower().endswith(".log"):
                    continue
                with open(os.path.join(ydir, name), encoding="latin-1") as fh:
                    text = fh.read()
                run, total = run_share(text)
                if total < MIN_QSOS:
                    continue
                call = log_call(text) or name[:-4].upper().replace("-", "/")
                con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
                            (call, contest, mode, int(year), round(100 * run / total), total))
                n += 1
            con.commit()
            print(f"{contest} {mode} {year}: done ({n} logs so far)", flush=True)
    con.close()
    os.replace(db + ".new", db)
    print(f"runlist.sqlite: {n} logs", flush=True)


def serve(data: str, port: int) -> None:
    db = os.path.join(data, "runlist.sqlite")

    class Handler(BaseHTTPRequestHandler):
        def _json(self, obj, code: int = 200) -> None:
            body = json.dumps(obj, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if not os.path.exists(db):
                return self._json({"error": "no database yet - run build"}, 503)
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                contest = q.get("contest", "CQWW").upper()
                mode = q.get("mode", "CW").upper()
                newest = ("SELECT call, run, year, qsos FROM runs r WHERE contest=? AND mode=?"
                          " AND year=(SELECT MAX(year) FROM runs WHERE call=r.call"
                          " AND contest=r.contest AND mode=r.mode)")
                if url.path == "/runs":
                    rows = con.execute(newest, (contest, mode)).fetchall()
                    return self._json({c: run for c, run, _, _ in rows})
                if url.path == "/run":
                    row = con.execute(newest + " AND call=?",
                                      (contest, mode, q.get("call", "").upper())).fetchone()
                    if not row:
                        return self._json({"error": "not found"}, 404)
                    return self._json(dict(zip(("call", "run", "year", "qsos"), row)))
                if url.path == "/status":
                    rows = con.execute("SELECT contest, mode, year, COUNT(*) FROM runs"
                                       " GROUP BY 1,2,3 ORDER BY 1,2,3").fetchall()
                    return self._json([dict(zip(("contest", "mode", "year", "logs"), r))
                                       for r in rows])
                return self._json({"error": "not found"}, 404)
            finally:
                con.close()

        def log_message(self, *a) -> None:
            pass

    print(f"serving {db} on port {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("build", "serve"))
    ap.add_argument("--data", default=os.environ.get("DATA", "/data"))
    ap.add_argument("--contest", default=os.environ.get("CONTEST", "CQWW"))
    ap.add_argument("--years", type=int, default=int(os.environ.get("YEARS", "3")))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8077")))
    a = ap.parse_args()
    os.makedirs(a.data, exist_ok=True)
    if a.command == "build":
        build(a.data, a.contest.upper(), a.years)
    else:
        serve(a.data, a.port)


if __name__ == "__main__":
    main()
