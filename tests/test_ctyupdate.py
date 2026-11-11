"""Country file check: every Friday, If-Modified-Since, a new good file is
saved and used, a broken one rejected."""

import os
import tempfile
import time
import unittest

from pouncer.ctyupdate import CtyUpdater, newest


def fake_cty(n=320):
    lines = []
    for i in range(n):
        lines.append(f"Country{i}:  14:  28:  EU:   46.00:   -14.00:    -1.0:  X{i:03d}:\n    X{i:03d};\n")
    return "".join(lines).encode("latin-1")


class UpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.d = tempfile.mkdtemp()
        self.now = [1794398400.0]       # Wed 11 Nov 2026 12:00 UTC (downloads: every Friday)
        self.calls = []
        self.answer = (200, fake_cty())
        self.new = []

    def up(self, **k):
        def fetch(url, headers):
            self.calls.append(headers)
            return self.answer
        return CtyUpdater(self.d, fetch=fetch, clock=lambda: self.now[0], on_new=self.new.append, **k)

    def test_new_file_saved_and_used_then_every_friday(self) -> None:
        u = self.up()
        self.assertTrue(u.due())                       # no copy yet
        self.assertTrue(u.check())
        self.assertTrue(os.path.exists(os.path.join(self.d, "cty.dat")))
        self.assertEqual(self.new, [os.path.join(self.d, "cty.dat")])
        self.assertFalse(u.due())
        self.now[0] += 1 * 86400 + 11 * 3600           # Thursday 23:00 UTC
        self.assertFalse(u.due())
        self.now[0] += 3600                            # Friday 00:00 UTC
        self.assertTrue(u.due())
        u.check()
        self.now[0] += 6 * 86400                       # Thursday after
        self.assertFalse(u.due())
        self.now[0] += 12 * 86400                      # PC off over a Friday
        self.assertTrue(u.due())                       # catches up at once

    def test_old_wl_cty_url_keeps_its_name(self) -> None:
        u = self.up(url="https://www.country-files.com/cty/wl_cty.dat")
        self.assertTrue(u.check())
        self.assertTrue(os.path.exists(os.path.join(self.d, "wl_cty.dat")))

    def test_if_modified_since_and_304(self) -> None:
        u = self.up()
        u.check()
        self.answer = (304, b"")
        self.assertFalse(u.check())
        self.assertIn("If-Modified-Since", self.calls[-1])
        self.assertIn("up to date", u.status)
        self.assertEqual(len(self.new), 1)

    def test_broken_download_rejected(self) -> None:
        self.answer = (200, b"<html>error</html>")
        u = self.up()
        self.assertFalse(u.check())
        self.assertFalse(os.path.exists(os.path.join(self.d, "cty.dat")))
        self.assertIn("rejected", u.status)

    def test_copy_to_n1mm(self) -> None:
        n1mm = os.path.join(self.d, "SupportFiles", "wl_cty.dat")
        os.makedirs(os.path.dirname(n1mm))
        with open(n1mm, "wb") as fh:
            fh.write(b"old")
        u = self.up(n1mm_path=n1mm, update_n1mm=True,
                    url="https://www.country-files.com/cty/wl_cty.dat")
        u.check()
        with open(n1mm, "rb") as fh:
            self.assertGreater(len(fh.read()), 1000)

    def test_full_cty_dat_never_over_n1mms_wl_cty(self) -> None:
        """S53M's cty.dat lacks the WAE entries N1MM's wl_cty.dat has."""
        n1mm = os.path.join(self.d, "SupportFiles", "wl_cty.dat")
        os.makedirs(os.path.dirname(n1mm))
        with open(n1mm, "wb") as fh:
            fh.write(b"old")
        u = self.up(n1mm_path=n1mm, update_n1mm=True)          # default: S53M cty.dat
        self.assertTrue(u.check())
        with open(n1mm, "rb") as fh:
            self.assertEqual(fh.read(), b"old")

    def test_offline_tries_again(self) -> None:
        def fail(url, headers):
            raise OSError("no route")
        u = CtyUpdater(self.d, fetch=fail, clock=lambda: self.now[0])
        self.assertFalse(u.check())
        self.assertTrue(u.due())                       # not marked as checked

    def test_newest(self) -> None:
        a, b = os.path.join(self.d, "a"), os.path.join(self.d, "b")
        for p, t in ((a, 100), (b, 200)):
            open(p, "w").close()
            os.utime(p, (t, t))
        self.assertEqual(newest([a, b, "", "/nope"]), b)


if __name__ == "__main__":
    unittest.main()
