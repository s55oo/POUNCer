"""The N1MM log is read in a thread (v0.7.176): the window's tick only
starts the read and takes the finished result - a big log no longer stalls
the window, and nothing half-read is ever shown."""

import os
import sqlite3
import tempfile
import threading
import time
import unittest

from pouncer.worked import WorkedLog
from tests.test_worked import _make_db


class BackgroundReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = os.path.join(self.tmp.name, "test.s3db")
        _make_db(self.db, [("K1ABC", 14.0, "CW"), ("W9XYZ", 7.0, "CW")])
        self.wl = WorkedLog(db_path=self.db)
        self.wl.background = True

    def tearDown(self) -> None:
        if self.wl._job is not None:
            self.wl._job["thread"].join(5)
        self.tmp.cleanup()

    def _until_taken(self) -> bool:
        for _ in range(200):
            if self.wl.maybe_reload():
                return True
            time.sleep(0.01)
        return False

    def test_tick_does_not_wait_for_the_read(self) -> None:
        gate = threading.Event()
        real = self.wl._read

        def slow(*a):
            gate.wait(5)                     # a big log: the read takes long
            return real(*a)
        self.wl._read = slow
        t0 = time.monotonic()
        self.assertFalse(self.wl.maybe_reload())          # started, not waited for
        self.assertFalse(self.wl.maybe_reload())          # still reading: no 2nd read
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertFalse(self.wl.is_worked("K1ABC", 14025.0, "CW"))
        gate.set()
        self.assertTrue(self._until_taken())
        self.assertTrue(self.wl.is_worked("K1ABC", 14025.0, "CW"))
        self.assertEqual(self.wl.qso_count, 2)
        self.assertTrue(self.wl.log_open)

    def test_qso_logged_while_reading_is_read_again(self) -> None:
        gate = threading.Event()
        read = threading.Event()
        real = self.wl._read

        def slow(*a):
            res = real(*a)                   # reads the log BEFORE the new QSO
            read.set()
            gate.wait(5)
            return res
        self.wl._read = slow
        self.wl.maybe_reload()
        self.assertTrue(read.wait(5))        # the thread really read it (slow CI Macs)
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO DXLOG (Call, Band, Mode) VALUES ('DL1ABC', 14.0, 'CW')")
        con.commit()
        con.close()
        self.wl.request_recheck()            # e.g. N1MM's Contacts packet
        gate.set()
        self.assertTrue(self._until_taken())
        self.assertFalse(self.wl.is_worked("DL1ABC", 14025.0, "CW"))   # old read
        self.assertIsNone(self.wl._signature)                          # -> again
        self.wl._read = real
        self.assertTrue(self._until_taken())
        self.assertTrue(self.wl.is_worked("DL1ABC", 14025.0, "CW"))

    def test_reconnect_waits_for_it(self) -> None:
        self.wl.request_recheck()
        self.assertTrue(self.wl.maybe_reload(wait=True))
        self.assertTrue(self.wl.log_open)
        self.assertTrue(self.wl.is_worked("W9XYZ", 7010.0, "CW"))

    def test_a_crashing_read_says_so(self) -> None:
        def boom(*a):
            raise RuntimeError("disk gone")
        self.wl._read = boom
        with self.assertLogs("pouncer.worked", "ERROR"):
            self.wl.maybe_reload()
            self.wl._job["thread"].join(5)
        self.assertTrue(self.wl.maybe_reload())
        self.assertTrue(self.wl.status.startswith("read failed"))


if __name__ == "__main__":
    unittest.main()
