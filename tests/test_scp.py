"""Super Check Partial: busted-call marks and the list update (no network)."""

import os
import tempfile
import time
import unittest

from pouncer import scp as S

CALLS = [f"K{i}ABC" for i in range(S.MIN_CALLS)] + ["S50A", "HB9CBR", "EA8AY", "9A1A"]
TEXT = "# Super Check Partial\n# comment\n" + "\n".join(CALLS) + "\n"


class ParseTests(unittest.TestCase):
    def test_parse_and_base_calls(self) -> None:
        calls = S.parse("# x\nS50A\n\nhb9cbr\n")
        self.assertEqual(calls, {"S50A", "HB9CBR"})
        self.assertEqual(S.base_calls("HB9CBR/P"), ["HB9CBR/P", "HB9CBR"])
        self.assertEqual(S.base_calls("EA8/K1ABC"), ["EA8/K1ABC", "K1ABC"])
        self.assertEqual(S.base_calls("K1ABC/VE3"), ["K1ABC/VE3", "K1ABC"])
        self.assertEqual(S.base_calls("S50A"), ["S50A"])


class ScpListTests(unittest.TestCase):
    def test_busted_marks(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sl = S.ScpList(os.path.join(d, "cache"), url="", fetch=lambda u: b"")
            self.assertFalse(sl.busted("PHB9CBR"))          # no list: no marks at all
            sl._calls = frozenset(S.parse(TEXT))
            self.assertFalse(sl.busted("HB9CBR"))
            self.assertFalse(sl.busted("HB9CBR/P"))         # portable: home call known
            self.assertTrue(sl.busted("PHB9CBR"))           # skimmer error
            self.assertTrue(sl.busted("HB9CBP"))

    def test_uses_n1mm_copy_until_downloaded(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            n1mm = os.path.join(d, "N1MM Logger+")
            os.makedirs(os.path.join(n1mm, "SupportFiles"))
            with open(os.path.join(n1mm, "SupportFiles", "MASTER.SCP"), "w") as fh:
                fh.write(TEXT)

            def offline(url):
                raise OSError("offline")
            sl = S.ScpList(os.path.join(d, "cache"), n1mm_ini=os.path.join(n1mm, "N1MM Logger.ini"),
                           fetch=offline)
            sl.load_and_update()
            self.assertEqual(sl.count(), len(CALLS))
            self.assertIn("N1MM copy", sl.status)
            self.assertFalse(sl.busted("S50A"))

    def test_download_when_old_and_copy_to_n1mm(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cache")
            n1mm = os.path.join(d, "N1MM Logger+")
            os.makedirs(os.path.join(n1mm, "SupportFiles"))
            urls = []
            sl = S.ScpList(cache, n1mm_ini=os.path.join(n1mm, "N1MM Logger.ini"),
                           fetch=lambda u: urls.append(u) or TEXT.encode(), update_n1mm=True)
            sl.load_and_update()                              # nothing yet: downloads
            self.assertEqual(urls, [S.SCP_URL])
            self.assertTrue(sl.loaded)
            self.assertTrue(os.path.exists(os.path.join(cache, "MASTER.SCP")))
            with open(os.path.join(n1mm, "SupportFiles", "MASTER.SCP")) as fh:
                self.assertIn("HB9CBR", fh.read())            # N1MM's copy updated too
            # fresh: no download at the next start
            sl2 = S.ScpList(cache, fetch=lambda u: urls.append(u) or TEXT.encode())
            sl2.load_and_update()
            self.assertEqual(len(urls), 1)
            # a week old: downloaded again
            old = time.time() - 8 * 86400
            os.utime(os.path.join(cache, "MASTER.SCP"), (old, old))
            sl2.load_and_update()
            self.assertEqual(len(urls), 2)

    def test_broken_download_keeps_old_list(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cache")
            os.makedirs(cache)
            with open(os.path.join(cache, "MASTER.SCP"), "w") as fh:
                fh.write(TEXT)
            old = time.time() - 30 * 86400
            os.utime(os.path.join(cache, "MASTER.SCP"), (old, old))
            sl = S.ScpList(cache, fetch=lambda u: b"<html>error page</html>")
            sl.load_and_update()
            self.assertEqual(sl.count(), len(CALLS))           # old list kept
            with open(os.path.join(cache, "MASTER.SCP")) as fh:
                self.assertIn("S50A", fh.read())               # not overwritten


if __name__ == "__main__":
    unittest.main()
