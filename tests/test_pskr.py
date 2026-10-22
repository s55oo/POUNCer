"""PSK Reporter check: one call watched, FT8 near the spot = not SSB."""

import json
import queue
import socket
import struct
import threading
import time
import types
import unittest

from pouncer import pskr
from pouncer.rbn import Spot, digital_overlap, digital_sure, guess_mode


def publish(topic: str, payload: dict) -> bytes:
    body = pskr._str(topic) + json.dumps(payload).encode()
    return bytes([0x30]) + pskr._varint(len(body)) + body


class FakeBroker:
    """Answers CONNECT / SUBSCRIBE and sends a report of the subscribed call."""

    def __init__(self, report_khz: float, mode: str = "FT8") -> None:
        self.a, self.b = socket.socketpair()
        self.topics = []
        self.report_khz, self.mode = report_khz, mode
        threading.Thread(target=self.run, daemon=True).start()

    def run(self) -> None:
        f = self.b
        try:
            while True:
                first = f.recv(1)
                if not first:
                    return
                n, mult = 0, 1
                while True:
                    b = f.recv(1)[0]
                    n += (b & 0x7F) * mult
                    mult *= 128
                    if not b & 0x80:
                        break
                body = f.recv(n) if n else b""
                kind = first[0] >> 4
                if kind == 1:
                    f.sendall(b"\x20\x02\x00\x00")
                elif kind == 8:
                    pid = body[:2]
                    tl = struct.unpack("!H", body[2:4])[0]
                    topic = body[4:4 + tl].decode()
                    self.topics.append(topic)
                    f.sendall(b"\x90\x03" + pid + b"\x00")
                    call = topic.split("/")[5]
                    f.sendall(publish(f"pskr/filter/v2/40m/{self.mode}/{call}/X/JN76/JO/1/2",
                                      {"sc": call, "f": int(self.report_khz * 1000),
                                       "md": self.mode, "rp": -10, "rc": "DL1X"}))
                elif kind == 14:
                    return
        except OSError:
            return


class PacketTests(unittest.TestCase):
    def test_topic_and_packets(self) -> None:
        self.assertEqual(pskr.topic_for("k1abc"), "pskr/filter/v2/+/+/K1ABC/#")
        self.assertIsNone(pskr.topic_for("EA8/S55OO"))
        self.assertEqual(pskr._varint(321), bytes([0xC1, 0x02]))
        self.assertTrue(pskr.connect_packet("x").startswith(b"\x10"))
        self.assertEqual(pskr.subscribe_packet(1, "a")[0], 0x82)

    def test_windows(self) -> None:
        self.assertEqual(digital_overlap(7076.0), "FT8")     # inside the 40 m SSB segment
        self.assertEqual(digital_overlap(1841.0), "FT8")     # 160 m
        self.assertEqual(digital_overlap(50314.0), "")       # 6 m: not checked
        self.assertEqual(digital_sure(7076.0), "")
        self.assertEqual(digital_sure(14075.0), "FT8")       # below 20 m SSB: sure
        self.assertEqual(guess_mode(7076.0, "59"), "SSB")    # unclear: SSB first
        self.assertEqual(guess_mode(14075.0, "cq"), "FT8")


class CheckerTests(unittest.TestCase):
    def test_ft8_report_near_the_spot(self) -> None:
        broker = FakeBroker(7075.8)
        c = pskr.PskChecker(connector=lambda: broker.a, watch_s=60)
        c.ask("K1ABC", 7076.0)
        for _ in range(50):
            if c.digital("K1ABC", 7076.0):
                break
            time.sleep(0.05)
        self.assertEqual(c.digital("K1ABC", 7076.0), "FT8")
        self.assertEqual(c.digital("K1ABC", 7150.0), "")     # far from it: not him there
        self.assertEqual(broker.topics, ["pskr/filter/v2/+/+/K1ABC/#"])
        self.assertNotIn("K1ABC", c._want)                   # confirmed: no longer watched
        for _ in range(60):                                  # ... and the line is hung up
            if c._thread is None or not c._thread.is_alive():
                break
            time.sleep(0.05)
        self.assertFalse(c._thread.is_alive())
        c.stop()

    def test_no_connection_confirms_nothing(self) -> None:
        def fail():
            raise OSError("down")
        c = pskr.PskChecker(connector=fail)
        c.ask("K1ABC")
        time.sleep(0.2)
        self.assertEqual(c.digital("K1ABC", 7076.0), "")


class AppTests(unittest.TestCase):
    def _fake(self, contest=""):
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        checker = types.SimpleNamespace(asked=[], known={}, watch_s=120)
        checker.ask = lambda call, khz=None: checker.asked.append(call)
        checker.digital = lambda call, khz: checker.known.get(call, "")
        f = types.SimpleNamespace(pskr=checker, _overlap_keys=set(), spots={}, _dirty=False,
                                  worked=types.SimpleNamespace(contest_name=contest))
        for n in ("_check_overlap", "_relabel_overlap", "_phone_contest"):
            setattr(f, n, getattr(P, n).__get__(f))
        return f, checker

    def spot(self, khz=7076.0):
        return Spot(dxcall="K1ABC", freq_khz=khz, mode="SSB", snr=None, wpm=None, spot_type="",
                    spotter="DL1X", time_utc="1200Z", received=time.time())

    def test_asked_then_relabelled(self) -> None:
        f, c = self._fake()
        s = self.spot()
        f._check_overlap(s)
        f.spots[s.key] = s
        self.assertEqual(c.asked, ["K1ABC"])
        f._relabel_overlap()
        self.assertEqual(s.mode, "SSB")                    # no answer yet: SSB
        c.known["K1ABC"] = "FT8"
        f._relabel_overlap()
        self.assertEqual(s.mode, "FT8")
        self.assertTrue(f._dirty)

    def test_not_in_ssb_contest_or_elsewhere(self) -> None:
        f, c = self._fake(contest="CQWWSSB")
        f._check_overlap(self.spot())
        f2, c2 = self._fake()
        f2._check_overlap(self.spot(7150.0))
        self.assertEqual((c.asked, c2.asked), ([], []))


if __name__ == "__main__":
    unittest.main()
