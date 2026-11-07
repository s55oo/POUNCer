"""Build check (not a test): fetch HamQTH.com's real APIs once and parse
them with pouncer.hamqth - the result goes to a text file published with
the screenshots, so the live field names are confirmed before a release.

    python tests/ci_hamqth_check.py out.txt
"""

import os
import queue
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pouncer.hamqth import HamQTHFeed  # noqa: E402


def main() -> int:
    out = []
    for kind in ("rbn", "spots"):
        q = queue.Queue()
        f = HamQTHFeed(kind, None, q, version="ci")
        try:
            n = f.poll_once()
            spots = [q.get_nowait() for _ in range(min(q.qsize(), 5))]
            out.append(f"{kind}: OK, {n} spots from {f.url()}")
            out += [f"  {s.spotter:>10} {s.freq_khz:9.1f} {s.dxcall:<10} {s.mode} {s.snr}" for s in spots]
        except Exception as exc:  # noqa: BLE001
            out.append(f"{kind}: FAILED {exc!r}")
    text = "\n".join(out) + "\n"
    print(text)
    if len(sys.argv) > 1:
        os.makedirs(os.path.dirname(sys.argv[1]) or ".", exist_ok=True)
        with open(sys.argv[1], "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
