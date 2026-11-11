"""Super Check Partial: busted-call marks and the list update (no network)."""

import os
import tempfile
import time
import unittest

from pouncer import scp as S

CALLS = [f"K{i}ABC" for i in range(S.MIN_CALLS)] + ["S50A", "HB9CBR", "EA8AY", "9A1A"]
TEXT = "# Super Check Partial\n# comment\n" + "\n".join(CALLS) + "\n"
SCP_TEXT_URL = "https://example.org/MASTER.SCP"


def make_dta(calls, groups=6):
    """A MASTER.DTA like S53M's: 4-byte little-endian offsets of each search
    group, then every group's calls, each ending in a zero byte (a call is
    in several groups)."""
    import struct
    bodies = []
    for g in range(groups):
        part = [c for i, c in enumerate(calls) if i % groups == g or i % 3 == 0]
        bodies.append(b"".join(c.encode() + b"\0" for c in part))
    head = 4 * (groups + 2)
    offsets, at = [], head
    for b in bodies:
        offsets.append(at)
        at += len(b)
    offsets += [at, at]                       # the end, twice (as in the real file)
    return struct.pack(f"<{len(offsets)}I", *offsets) + b"".join(bodies)


class DtaTests(unittest.TestCase):
    """v0.7.187: S53M's MASTER.DTA (binary), downloaded every Friday."""

    def test_parse_dta(self) -> None:
        calls = S.parse_dta(make_dta(CALLS))
        self.assertEqual(calls, set(CALLS))               # duplicates folded
        self.assertEqual(S.parse_bytes(make_dta(CALLS), "x/MASTER.DTA"), set(CALLS))
        self.assertEqual(S.parse_dta(TEXT.encode()), set())          # text is no DTA
        self.assertEqual(S.parse_dta(b"<html>error</html>" * 3), set())

    def test_default_is_s53m_dta_and_n1mm_gets_text(self) -> None:
        self.assertEqual(S.SCP_URL, "https://azure.s53m.com/cors/MASTER.DTA")
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cache")
            n1mm = os.path.join(d, "N1MM Logger+")
            os.makedirs(os.path.join(n1mm, "SupportFiles"))
            with open(os.path.join(n1mm, "SupportFiles", "MASTER.SCP"), "w") as fh:
                fh.write(TEXT.replace("S50A\n", ""))
            sl = S.ScpList(cache, n1mm_ini=os.path.join(n1mm, "N1MM Logger.ini"),
                           url=S.SCP_URL, fetch=lambda u: make_dta(CALLS), update_n1mm=True)
            sl.load_and_update()
            self.assertTrue(os.path.exists(os.path.join(cache, "MASTER.DTA")))
            self.assertEqual(sl.count(), len(CALLS))
            self.assertFalse(sl.busted("S50A"))
            with open(os.path.join(n1mm, "SupportFiles", "MASTER.SCP"), "rb") as fh:
                n1mm_scp = fh.read()
            self.assertNotIn(b"\0", n1mm_scp)                 # text, never the binary
            self.assertIn(b"\nS50A\n", n1mm_scp)
            sl2 = S.ScpList(cache, n1mm_ini=os.path.join(n1mm, "N1MM Logger.ini"),
                            url=S.SCP_URL, fetch=lambda u: self.fail("no new week yet"))
            sl2.load_and_update()                              # our DTA, not N1MM's copy
            self.assertIn("MASTER.DTA", sl2.source)

    def test_n1mm_copy_only_before_the_first_download(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            n1mm = os.path.join(d, "N1MM Logger+")
            os.makedirs(os.path.join(n1mm, "SupportFiles"))
            with open(os.path.join(n1mm, "SupportFiles", "MASTER.SCP"), "w") as fh:
                fh.write(TEXT)

            def offline(u):
                raise OSError("offline")
            sl = S.ScpList(os.path.join(d, "cache"), n1mm_ini=os.path.join(n1mm, "x.ini"),
                           url=S.SCP_URL, fetch=offline)
            sl.load_and_update()
            self.assertTrue(sl.loaded)
            self.assertIn("N1MM copy", sl.status)


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
                           url=SCP_TEXT_URL,
                           fetch=lambda u: urls.append(u) or TEXT.encode(), update_n1mm=True)
            sl.load_and_update()                              # nothing yet: downloads
            self.assertEqual(urls, [SCP_TEXT_URL])
            self.assertTrue(sl.loaded)
            self.assertTrue(os.path.exists(os.path.join(cache, "MASTER.SCP")))
            with open(os.path.join(n1mm, "SupportFiles", "MASTER.SCP")) as fh:
                self.assertIn("HB9CBR", fh.read())            # N1MM's copy updated too

    def test_every_start_asks_server_304_when_current(self) -> None:
        """Real HTTP: the server answers 304 to 'changed since?' when our copy
        is current - nothing is downloaded - and the new file when it isn't."""
        import email.utils
        import http.server
        import threading
        state = {"mtime": time.time() - 3 * 86400, "gets": 0, "full": 0, "etag": '"v1"'}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                state["gets"] += 1
                ims = self.headers.get("If-Modified-Since")
                inm = self.headers.get("If-None-Match")
                if inm == state["etag"] or (ims and email.utils.parsedate_to_datetime(ims)
                                            .timestamp() >= int(state["mtime"])):
                    self.send_response(304)
                    self.end_headers()
                    return
                state["full"] += 1
                body = TEXT.encode()
                self.send_response(200)
                self.send_header("Last-Modified", email.utils.formatdate(state["mtime"], usegmt=True))
                self.send_header("ETag", state["etag"])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_port}/MASTER.SCP"
        try:
            with tempfile.TemporaryDirectory() as d:
                cache = os.path.join(d, "cache")
                sl = S.ScpList(cache, url=url)
                sl.load_and_update()                       # no copy: full download
                self.assertEqual((state["gets"], state["full"]), (1, 1))
                self.assertTrue(sl.loaded)
                self.assertAlmostEqual(os.path.getmtime(os.path.join(cache, "MASTER.SCP")),
                                       int(state["mtime"]), delta=1)   # server's date kept
                S.ScpList(cache, url=url).load_and_update()   # same week: no request
                self.assertEqual((state["gets"], state["full"]), (1, 1))
                stamp = os.path.join(cache, "scp-check.txt")
                S.weekly.write_stamp(stamp, time.time() - 8 * 86400)   # a Friday passed
                S.ScpList(cache, url=url).load_and_update()   # asks: 304, a few bytes
                self.assertEqual((state["gets"], state["full"]), (2, 1))
                state["mtime"] = time.time() - 60          # a new list on the server
                state["etag"] = '"v2"'
                S.weekly.write_stamp(stamp, time.time() - 8 * 86400)
                sl3 = S.ScpList(cache, url=url)
                sl3.load_and_update()
                self.assertEqual((state["gets"], state["full"]), (3, 2))
                S.weekly.write_stamp(stamp, time.time() - 8 * 86400)
                sl4 = S.ScpList(cache, url=url)
                sl4.load_and_update()                      # current again: 304
                self.assertIn("up to date", sl4.status)
        finally:
            srv.shutdown()

    def test_broken_download_keeps_old_list(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cache")
            os.makedirs(cache)
            with open(os.path.join(cache, "MASTER.SCP"), "w") as fh:
                fh.write(TEXT)
            old = time.time() - 30 * 86400
            os.utime(os.path.join(cache, "MASTER.SCP"), (old, old))
            sl = S.ScpList(cache, url=SCP_TEXT_URL, fetch=lambda u: b"<html>error page</html>")
            sl.load_and_update()
            self.assertEqual(sl.count(), len(CALLS))           # old list kept
            with open(os.path.join(cache, "MASTER.SCP")) as fh:
                self.assertIn("S50A", fh.read())               # not overwritten


if __name__ == "__main__":
    unittest.main()
