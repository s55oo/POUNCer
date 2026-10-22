"""Kept versions: an update keeps the one it replaces; going back; team update."""

import os
import tempfile
import threading
import unittest

from pouncer.update import Updater


class VersionTests(unittest.TestCase):
    def _updater(self, ver="0.7.126"):
        d = tempfile.mkdtemp()
        exe = os.path.join(d, "POUNCer.exe")
        with open(exe, "wb") as fh:
            fh.write(b"exe " + ver.encode())
        u = Updater(ver, exe_path=exe, enabled=True, fetch=lambda *a, **k: b"")
        return u, exe

    def test_install_keeps_the_old_one_and_rollback(self) -> None:
        u, exe = self._updater()
        u._install(b"exe 0.7.127")
        with open(exe, "rb") as fh:
            self.assertEqual(fh.read(), b"exe 0.7.127")
        kept = os.path.join(os.path.dirname(exe), "versions", "POUNCer-0.7.126.exe")
        with open(kept, "rb") as fh:
            self.assertEqual(fh.read(), b"exe 0.7.126")
        u2 = Updater("0.7.127", exe_path=exe, enabled=True, fetch=lambda *a, **k: b"")
        prev = u2.previous_versions()
        self.assertEqual([v for v, _ in prev], ["0.7.126"])
        u2.rollback(prev[0][1])
        with open(exe, "rb") as fh:
            self.assertEqual(fh.read(), b"exe 0.7.126")
        self.assertEqual((u2.state, u2.new_version), ("ready", "v0.7.126"))
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(exe), "versions",
                                                    "POUNCer-0.7.127.exe")))

    def test_only_the_last_three(self) -> None:
        u, exe = self._updater("0.7.120")
        d = os.path.join(os.path.dirname(exe), "versions")
        os.makedirs(d)
        for v in ("0.7.100", "0.7.110", "0.7.115", "0.7.119"):
            open(os.path.join(d, f"POUNCer-{v}.exe"), "wb").close()
        u.keep_current()
        self.assertEqual(sorted(os.listdir(d)),
                         ["POUNCer-0.7.115.exe", "POUNCer-0.7.119.exe", "POUNCer-0.7.120.exe"])


class TeamUpdateTests(unittest.TestCase):
    def test_only_from_the_master(self) -> None:
        from pouncer.team import Team
        got = []
        t = Team.__new__(Team)
        t._lock = threading.Lock()
        t.peers = {}
        t.id, t.started = "me", 5.0
        t._on_update = lambda: got.append(1)
        t._on_spot = t._on_runhist = t._on_self = None
        t.handle({"t": "hi", "id": "m", "started": 1}, "10.0.0.2")
        t.handle({"t": "hi", "id": "x", "started": 9}, "10.0.0.3")
        t.handle({"t": "update", "id": "x"}, "10.0.0.3")    # not the master: ignored
        t.handle({"t": "update", "id": "m"}, "10.0.0.2")
        self.assertEqual(got, [1])


if __name__ == "__main__":
    unittest.main()
