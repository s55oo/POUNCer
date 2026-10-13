"""Tests for the exe auto-update (fake GitHub, temp files)."""

import hashlib
import json
import os
import tempfile
import unittest

from pouncer import update as up
from pouncer.lights import update_light


class VersionTests(unittest.TestCase):
    def test_compare(self) -> None:
        self.assertEqual(up.parse_version("v0.7.10"), (0, 7, 10))
        self.assertTrue(up.is_newer("v0.7.10", "0.7.9"))
        self.assertFalse(up.is_newer("v0.7.3", "0.7.3"))
        self.assertFalse(up.is_newer("warmup", "0.7.3"))
        self.assertFalse(up.is_newer("v0.6.0", "0.7.3"))

    def test_expected_sha(self) -> None:
        h = "AB" * 32
        self.assertEqual(up.expected_sha256({"body": f"**POUNCer.exe** SHA-256: `{h}`"}, {}),
                         h.lower())
        self.assertEqual(up.expected_sha256({}, {"digest": "sha256:" + "c" * 64}), "c" * 64)
        self.assertIsNone(up.expected_sha256({"body": "nothing"}, {}))


class UpdaterTests(unittest.TestCase):
    def _run(self, tag: str, blob: bytes, sha: str):
        d = tempfile.mkdtemp()
        exe = os.path.join(d, "POUNCer.exe")
        with open(exe, "wb") as fh:
            fh.write(b"old exe")
        release = {"tag_name": tag, "body": f"SHA-256: `{sha}`",
                   "assets": [{"name": "POUNCer.exe", "browser_download_url": "http://x/exe"}]}

        def fetch(url, accept="", timeout=0):
            return json.dumps(release).encode() if url == up.LATEST_URL else blob

        u = up.Updater("0.7.3", exe_path=exe, fetch=fetch)
        u.check_now()
        return u, exe

    def test_newer_release_replaces_exe_and_keeps_old(self) -> None:
        blob = b"new exe bytes"
        u, exe = self._run("v0.7.4", blob, hashlib.sha256(blob).hexdigest())
        self.assertEqual(u.state, up.READY)
        self.assertEqual(open(exe, "rb").read(), blob)
        self.assertEqual(open(exe + ".old", "rb").read(), b"old exe")
        self.assertEqual(update_light(u.state, u.text()), ("new", "Update v0.7.4 ready - restart"))
        up.Updater("0.7.4", exe_path=exe)           # next start: .old removed
        self.assertFalse(os.path.exists(exe + ".old"))

    def test_bad_hash_keeps_old_exe(self) -> None:
        u, exe = self._run("v0.7.4", b"tampered", "0" * 64)
        self.assertEqual(u.state, up.FAILED)
        self.assertIn("mismatch", u.error)
        self.assertEqual(open(exe, "rb").read(), b"old exe")

    def test_same_version_is_current(self) -> None:
        u, exe = self._run("v0.7.3", b"x", "0" * 64)
        self.assertEqual(u.state, up.CURRENT)
        self.assertEqual(update_light(u.state, u.text())[0], "ok")

    def test_offline_and_source_runs(self) -> None:
        def fetch(*a, **k):
            raise OSError("offline")
        with tempfile.TemporaryDirectory() as d:
            u = up.Updater("0.7.3", exe_path=os.path.join(d, "P.exe"), fetch=fetch)
            u.check_now()
            self.assertEqual(u.state, up.FAILED)
        self.assertEqual(up.Updater("0.7.3").state, up.OFF)   # not frozen: off


if __name__ == "__main__":
    unittest.main()
