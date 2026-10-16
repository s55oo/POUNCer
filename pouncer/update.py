"""Auto-update of POUNCer.exe from the GitHub releases.

At start (and every 6 hours) the exe asks GitHub for the latest release. If
it is newer than the running version, POUNCer.exe is downloaded in the
background, its SHA-256 is checked against the one GitHub publishes for the
release (asset digest, or the hash in the release notes) and it replaces
the exe on disk: Windows can't overwrite a running exe but can rename it,
so the running one becomes ``POUNCer.exe.old`` (deleted at the next start)
and the new file takes its place. The running POUNCer is never stopped by
itself - the new version starts at the next start, or right away with the
"Restart" button.

Only the built Windows exe (``sys.frozen``) installs updates. A built macOS
POUNCer only *discovers* them: the Update light turns blue with "vX
available" (download the macOS archive from the Releases page); it never
downloads the Windows asset. Running from source does nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

LATEST_URL = "https://api.github.com/repos/s55oo/POUNCer/releases/latest"
ASSET = "POUNCer.exe"
CHECK_EVERY_S = 6 * 3600

# states (for the light in the main window)
OFF, CHECKING, CURRENT, DOWNLOADING, READY, FAILED, AVAILABLE = (
    "off", "checking", "current", "downloading", "ready", "failed", "available")


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.7.3' -> (0, 7, 3); anything unparsable -> ()."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(tag: str, current: str) -> bool:
    new, cur = parse_version(tag), parse_version(current)
    return bool(new) and bool(cur) and new > cur


def expected_sha256(release: dict, asset: dict) -> str | None:
    """SHA-256 GitHub gives for the asset, else the one in the release notes."""
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    m = re.search(r"SHA-256[^0-9A-Fa-f]{0,40}([0-9A-Fa-f]{64})", release.get("body") or "")
    return m.group(1).lower() if m else None


def _get(url: str, accept: str = "application/json", timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "POUNCer", "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


class Updater:
    def __init__(self, current: str, exe_path: str | None = None,
                 enabled: bool = True, fetch=None) -> None:
        self.current = current
        frozen = getattr(sys, "frozen", False)
        self.exe = exe_path or (sys.executable if frozen and sys.platform == "win32" else "")
        # macOS (and other) builds: look for new versions, don't install them
        self.notify_only = not self.exe and bool(frozen)
        self.enabled = enabled and (bool(self.exe) or self.notify_only)
        self._fetch = fetch or _get
        self.state = OFF if not self.enabled else CHECKING
        self.new_version = ""
        self.error = ""
        self._last_check = 0.0
        self._busy = threading.Lock()
        if self.enabled and self.exe:
            self.cleanup_old()

    # --- status ----------------------------------------------------------
    def text(self) -> str:
        return {
            OFF: "Update off",
            CHECKING: "Update: checking",
            CURRENT: f"Up to date ({self.current}) \u21bb",   # click = check now
            DOWNLOADING: f"Update {self.new_version}: downloading",
            READY: f"Update {self.new_version} ready - restart",
            AVAILABLE: f"{self.new_version} available on GitHub",
            FAILED: "Update check failed \u21bb",
        }[self.state]

    # --- work ------------------------------------------------------------
    def cleanup_old(self) -> None:
        old = self.exe + ".old"
        for path in (old, self.exe + ".new"):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass  # still in use (the old one just exited): next time

    def maybe_check(self) -> None:
        """Start a background check if due (at start, then every 6 h)."""
        if not self.enabled or self.state in (READY, DOWNLOADING, AVAILABLE):
            return
        if time.time() - self._last_check < CHECK_EVERY_S:
            return
        self._last_check = time.time()
        threading.Thread(target=self.check_now, name="update", daemon=True).start()

    def check_in_background(self) -> bool:
        """Check right now (the Update light was clicked). False when off or
        already downloading / ready."""
        if not self.enabled or self.state in (DOWNLOADING, READY):
            return False
        self._last_check = time.time()
        threading.Thread(target=self.check_now, name="update", daemon=True).start()
        return True

    def check_now(self) -> None:
        if not self._busy.acquire(blocking=False):
            return
        try:
            self.state = CHECKING
            release = json.loads(self._fetch(LATEST_URL).decode())
            tag = str(release.get("tag_name") or "")
            if not is_newer(tag, self.current):
                self.state = CURRENT
                return
            if self.notify_only:          # macOS: just say so
                self.new_version = tag
                self.state = AVAILABLE
                return
            asset = next((a for a in release.get("assets") or []
                          if a.get("name") == ASSET), None)
            if asset is None:
                raise ValueError(f"{tag} has no {ASSET}")
            want = expected_sha256(release, asset)
            if not want:
                raise ValueError(f"{tag}: no SHA-256 published")
            self.new_version = tag
            self.state = DOWNLOADING
            data = self._fetch(asset["browser_download_url"],
                               accept="application/octet-stream", timeout=300)
            got = hashlib.sha256(data).hexdigest()
            if got != want:
                raise ValueError(f"{tag}: SHA-256 mismatch")
            self._install(data)
            self.state = READY
            log.info("update %s installed, used from the next start", tag)
        except Exception as exc:  # offline, GitHub down, ...: try again later
            self.error = str(exc)
            self.state = FAILED
            log.info("update check failed: %s", exc)
        finally:
            self._busy.release()

    def _install(self, data: bytes) -> None:
        new, old = self.exe + ".new", self.exe + ".old"
        with open(new, "wb") as fh:
            fh.write(data)
        if os.path.exists(old):
            os.remove(old)
        os.replace(self.exe, old)          # a running exe can be renamed
        try:
            os.replace(new, self.exe)
        except OSError:
            os.replace(old, self.exe)      # put the old one back
            raise

    def restart(self, before_exit=None) -> None:
        """Start the (new) exe and quit this one."""
        if before_exit is not None:
            before_exit()                  # free the ports first
        env = dict(os.environ)
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # a fresh onefile instance
        env.pop("_MEIPASS2", None)
        env["POUNCER_RESTARTED"] = "1"   # the new one doesn't autostart programs again
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([self.exe, *sys.argv[1:]], cwd=os.getcwd(), env=env,
                         creationflags=flags, close_fds=True)
        os._exit(0)
