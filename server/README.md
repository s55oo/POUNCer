# POUNCer run list server

> Simplest: no server at all - copy `runlist.sqlite` (from a build, or the
> one shipped with a POUNCer release) next to POUNCer's `config.ini`
> (`[ui] run_list`). The server is only needed to share one list with
> several PCs or to rebuild it on a schedule.

Small Docker service that knows, for every station in a contest, how often
it **runs** (calls CQ) - from S53ZO's public
[Hamradio-Contest-logs-Archives](https://github.com/s53zo/Hamradio-Contest-logs-Archives).
POUNCer loads the whole list at once, so the R% / ⚡ marks are there the
moment a call is spotted.

## Start (on the Docker host, e.g. reachable over ZeroTier)

```sh
git clone https://github.com/s55oo/POUNCer.git
cd POUNCer
docker compose -f server/docker-compose.yml up -d --build
docker logs -f pouncer-runlist      # first start builds the list (~4 min)
```

Then in POUNCer's `config.ini`, `[ui]`:

```ini
run_server = http://<docker host ZeroTier IP>:8077
```

## What the first start does

- Partial `git clone` of the archive (file list only), then checks out just
  `CQWW/cw` and `CQWW/ph` of the last 3 years (+ current year). Other
  contests' files are never downloaded.
- Measured: 47 879 logs (CQWW CW+SSB 2023-2025), 3.3 GB of logs, **3.5 min**,
  result `runlist.sqlite` = 2.6 MB. 12 190 CW calls.
- Run % per log = QSOs on the same frequency (±0.3 kHz) as one of the last
  two QSOs on that band; logs with fewer than 30 QSOs are skipped; the
  call comes from the log's `CALLSIGN:` header.

Rebuild (new logs in the archive; only changed files are fetched):

```sh
docker compose -f server/docker-compose.yml run --rm runlist python server/runlist.py build
```

Other contest / more years: set `CONTEST` (archive folder, e.g. `CQWPX`)
and `YEARS` in `docker-compose.yml`. One list per container for now.

## API

| GET | Returns |
| --- | --- |
| `/runs?contest=CQWW&mode=CW` | `{"CALL": run %, ...}`, newest year per call (≈130 KB for CQWW CW, 0.09 s) |
| `/run?call=OH2BH&contest=CQWW&mode=CW` | `{"call":"OH2BH","run":9,"year":2025,"qsos":314}` |
| `/status` | logs per contest / mode / year |

Modes: `CW`, `PH` (SSB), `RTTY`. No login, read-only - keep it on ZeroTier / LAN.
