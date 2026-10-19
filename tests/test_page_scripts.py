"""Every page's JavaScript must parse: one syntax error stops the whole
page (v0.7.92: an apostrophe in a string left the phone at "connecting...").
Checked with Node's --check when Node is installed (here and on GitHub)."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from pouncer import web

PAGES = ("INDEX_HTML", "HELP_HTML", "ZONES_HTML", "TIMELINE_HTML", "SUMMARY_HTML",
         "SETTINGS_HTML")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PageScriptTests(unittest.TestCase):
    def test_all_scripts_parse(self) -> None:
        node = shutil.which("node")
        with tempfile.TemporaryDirectory() as d:
            for name in PAGES:
                html = getattr(web, name).replace("__VERSION__", "0.0.0")
                scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
                self.assertTrue(scripts or name == "SUMMARY_HTML", name)
                for i, js in enumerate(scripts):
                    path = os.path.join(d, f"{name}_{i}.js")
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(js)
                    r = subprocess.run([node, "--check", path], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, f"{name} script {i}: {r.stderr[-600:]}")


if __name__ == "__main__":
    unittest.main()
