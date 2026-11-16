"""The app icon is there for both exes (the .spec files and the windows use it)."""

import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class IconTests(unittest.TestCase):
    def test_files_and_specs(self) -> None:
        folder = os.path.join(HERE, "pouncer", "icon")
        for name in ("pouncer.ico", "pouncer.png"):
            self.assertGreater(os.path.getsize(os.path.join(folder, name)), 1000, name)
        with open(os.path.join(folder, "pouncer.ico"), "rb") as fh:
            head = fh.read(6)
        self.assertEqual(head[:4], b"\x00\x00\x01\x00")          # an icon file
        self.assertGreaterEqual(int.from_bytes(head[4:6], "little"), 5)   # 16 ... 256
        for spec in ("POUNCer.spec", "N1MMautostarter.spec"):
            text = open(os.path.join(HERE, spec), encoding="utf-8").read()
            self.assertIn("pouncer/icon/pouncer.ico", text, spec)
            self.assertIn("('pouncer/icon', 'pouncer/icon')", text, spec)


if __name__ == "__main__":
    unittest.main()
