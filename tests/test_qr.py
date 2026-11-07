"""QR code for the phone page: the encoder, the addresses, and (in the build)
an independent check that a real QR reader decodes it back."""

import os
import unittest

from pouncer import qr
from pouncer.web import phone_addresses

URLS = ("http://10.66.66.5:8080/", "http://192.168.178.123:8080/", "http://10.0.0.9:8090/",
        "http://172.30.250.201:18080/", "http://localhost:8080/")


class EncoderTests(unittest.TestCase):
    def test_sizes_and_finder_patterns(self) -> None:
        for text, size in (("hi", 21), ("http://10.66.66.5:8080/", 25),
                           ("http://192.168.178.123:8080/", 29), ("w" * 100, 41)):
            m = qr.qr_matrix(text)
            self.assertEqual(len(m), size)
            self.assertTrue(all(len(row) == size for row in m))
            for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
                self.assertTrue(all(m[r0][c0 + i] for i in range(7)))
                self.assertFalse(m[r0 + 1][c0 + 1])
                self.assertTrue(m[r0 + 3][c0 + 3])

    def test_too_long(self) -> None:
        with self.assertRaises(ValueError):
            qr.qr_matrix("x" * 200)

    def test_known_format_bits(self) -> None:
        self.assertEqual(qr._format_bits(0), 0b101010000010010)   # level M, mask 0


class AddressTests(unittest.TestCase):
    def test_lan_and_zerotier(self) -> None:
        ad = [("Ethernet adapter Ethernet", "192.168.1.20"),
              ("Ethernet adapter ZeroTier One [8056c2e21c000001]", "10.66.66.5")]
        self.assertEqual(phone_addresses("0.0.0.0", ad, "192.168.1.20"),
                         [("LAN", "192.168.1.20"), ("ZeroTier", "10.66.66.5")])

    def test_lan_only(self) -> None:
        self.assertEqual(phone_addresses("0.0.0.0", [("Ethernet", "192.168.1.20")], "192.168.1.20"),
                         [("LAN", "192.168.1.20")])

    def test_fixed_host(self) -> None:
        self.assertEqual(phone_addresses("10.0.0.9"), [("Address", "10.0.0.9")])


@unittest.skipUnless(__import__("sys").platform == "win32", "Windows only")
class WindowsAdapterTests(unittest.TestCase):
    """The build's Windows runner: the addresses straight from Windows."""

    def test_windows_adapters(self) -> None:
        import socket
        from pouncer.web import adapter_ipv4s, windows_adapters
        found = windows_adapters()
        self.assertTrue(found, "no IPv4 address from GetIpAddrTable")
        for descr, ip in found:
            self.assertIsInstance(descr, str)
            socket.inet_aton(ip)                       # a valid IPv4
            self.assertFalse(ip.startswith(("127.", "169.254.")))
        self.assertEqual(adapter_ipv4s(), found)


class DecodeTests(unittest.TestCase):
    """Decoded by OpenCV's QR reader - run in the build (POUNCER_QR_DECODE=1,
    OpenCV installed there); skipped elsewhere."""

    def test_a_real_reader_decodes_it(self) -> None:
        required = os.environ.get("POUNCER_QR_DECODE") == "1"
        try:
            import cv2
            import numpy as np
        except ImportError:
            if required:
                self.fail("POUNCER_QR_DECODE=1 but OpenCV is not installed")
            self.skipTest("no OpenCV here")
        det = cv2.QRCodeDetector()
        for url in URLS:
            m = qr.qr_matrix(url)
            box, quiet = 8, 4
            side = (len(m) + 2 * quiet) * box
            img = np.full((side, side), 255, dtype=np.uint8)
            for r, row in enumerate(m):
                for c, dark in enumerate(row):
                    if dark:
                        y, x = (r + quiet) * box, (c + quiet) * box
                        img[y:y + box, x:x + box] = 0
            text, _pts, _ = det.detectAndDecode(img)
            self.assertEqual(text, url, f"OpenCV read {text!r} for {url}")


if __name__ == "__main__":
    unittest.main()
