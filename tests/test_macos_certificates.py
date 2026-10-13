"""The frozen macOS build uses bundled roots while preserving custom trust."""

import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest import mock


class CertificateHookTests(unittest.TestCase):
    def run_hook(self, platform, environment):
        hook = Path(__file__).resolve().parents[1] / "packaging" / "macos_certificates.py"
        with mock.patch.object(sys, "platform", platform), \
                mock.patch.object(sys, "_MEIPASS", "/bundle", create=True), \
                mock.patch.dict(os.environ, environment, clear=True):
            runpy.run_path(str(hook))
            return os.environ.get("SSL_CERT_FILE")

    def test_mac_uses_bundled_roots(self):
        self.assertEqual(self.run_hook("darwin", {}), "/bundle/certificates/cacert.pem")

    def test_custom_trust_store_is_preserved(self):
        self.assertEqual(self.run_hook("darwin", {"SSL_CERT_FILE": "/custom.pem"}), "/custom.pem")

    def test_windows_keeps_its_default_trust_store(self):
        self.assertIsNone(self.run_hook("win32", {}))
