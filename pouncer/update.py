"""Auto-update of POUNCer.exe from the GitHub releases.

At start (and every 6 hours) the exe asks GitHub for the latest release. If
it is newer than the running version, POUNCer.exe is downloaded in the
background, its SHA-256 is checked against the one GitHub publishes for the
release (asset digest, or the hash in the release notes) and it replaces
the exe on disk: Windows can't overwrite a running exe but can rename it,
so the running one becomes ``POUNCer.exe.old`` (deleted at the next start);
a copy of it is kept as ``versions/POUNCer-<version>.exe`` (the last
``KEEP_VERSIONS``) so ``rollback()`` can go back to it
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
RELEASES_URL = "https://api.github.com/repos/s55oo/POUNCer/releases?per_page=15"
ASSET = "POUNCer.exe"
SUMS_ASSET = "SHA256SUMS.txt"
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


def parse_sums(text: str) -> dict[str, str]:
    """``<sha256>  <file>`` lines (sha256sum format) -> {file: sha256}."""
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"\s*([0-9A-Fa-f]{64})\s+\*?(\S.*?)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def expected_hashes(release: dict, asset: dict, sums_text: str | None) -> dict[str, str]:
    """Every published SHA-256 of the asset: GitHub's own record ('github')
    and the release's SHA256SUMS.txt ('sums'); the release notes ('notes')
    only for older releases that have neither."""
    out = {}
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        out["github"] = digest.split(":", 1)[1].lower()
    if sums_text is not None:
        h = parse_sums(sums_text).get(asset.get("name") or "")
        if h:
            out["sums"] = h
    if not out:
        m = re.search(r"SHA-256[^0-9A-Fa-f]{0,40}([0-9A-Fa-f]{64})", release.get("body") or "")
        if m:
            out["notes"] = m.group(1).lower()
    return out


def check_hashes(data: bytes, hashes: dict[str, str], tag: str = "") -> None:
    """Raise unless the published checksums agree with each other and with
    the file (two sources from v0.7.140 on: GitHub + SHA256SUMS.txt)."""
    if not hashes:
        raise ValueError(f"{tag}: no SHA-256 published")
    if len(set(hashes.values())) > 1:
        raise ValueError(f"{tag}: the published SHA-256 sums disagree ({', '.join(hashes)})")
    got = hashlib.sha256(data).hexdigest()
    if got != next(iter(hashes.values())):
        raise ValueError(f"{tag}: SHA-256 mismatch")


KEEP_VERSIONS = 3


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
            self.new_version = tag
            self.state = DOWNLOADING
            data = self.download_verified(release)
            self._install(data)
            self.state = READY
            log.info("update %s installed, used from the next start", tag)
        except Exception as exc:  # offline, GitHub down, ...: try again later
            self.error = str(exc)
            self.state = FAILED
            log.info("update check failed: %s", exc)
        finally:
            self._busy.release()

    def download_verified(self, release: dict) -> bytes:
        """POUNCer.exe of a release, checked against every published
        SHA-256 (GitHub's record + SHA256SUMS.txt must agree with the file)."""
        tag = str(release.get("tag_name") or "")
        assets = release.get("assets") or []
        asset = next((a for a in assets if a.get("name") == ASSET), None)
        if asset is None:
            raise ValueError(f"{tag} has no {ASSET}")
        sums_asset = next((a for a in assets if a.get("name") == SUMS_ASSET), None)
        sums_text = None
        if sums_asset is not None:
            sums_text = self._fetch(sums_asset["browser_download_url"],
                                    accept="application/octet-stream", timeout=60).decode(
                                        "utf-8", "replace")
        hashes = expected_hashes(release, asset, sums_text)
        if sums_asset is not None and "sums" not in hashes:
            raise ValueError(f"{tag}: {ASSET} missing from {SUMS_ASSET}")
        data = self._fetch(asset["browser_download_url"], accept="application/octet-stream",
                           timeout=300)
        check_hashes(data, hashes, tag)
        log.info("update %s: SHA-256 checked against %s", tag, " + ".join(hashes))
        return data

    # --- any released version (Help > Switch version...) ----------------------
    def list_releases(self) -> list[dict]:
        """The published versions with a POUNCer.exe, newest first:
        [{"tag", "date", "release"}] (no pre-releases)."""
        out = []
        for rel in json.loads(self._fetch(RELEASES_URL).decode()):
            if rel.get("draft") or rel.get("prerelease"):
                continue
            if not any(a.get("name") == ASSET for a in rel.get("assets") or []):
                continue
            out.append({"tag": str(rel.get("tag_name") or ""),
                        "date": str(rel.get("published_at") or "")[:10], "release": rel})
        out.sort(key=lambda r: parse_version(r["tag"]) or (0,), reverse=True)
        return out

    def install_release(self, release: dict) -> None:
        """Switch to that version (newer or older), in the background; state
        READY when it is in place (the running one is kept in versions\\)."""
        if not self.exe or not self._busy.acquire(blocking=False):
            return

        def work():
            tag = str(release.get("tag_name") or "")
            try:
                self.new_version = tag
                self.state = DOWNLOADING
                self._install(self.download_verified(release))
                self.state = READY
                log.info("switched to %s, used from the next start", tag)
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                self.state = FAILED
                log.info("switch to %s failed: %s", tag, exc)
            finally:
                self._busy.release()

        threading.Thread(target=work, name="switch-version", daemon=True).start()

    # --- previous versions ------------------------------------------------
    def versions_dir(self) -> str:
        return os.path.join(os.path.dirname(self.exe), "versions") if self.exe else ""

    def keep_current(self) -> str:
        """Copy the running exe to versions/POUNCer-<version>.exe (keep the
        last KEEP_VERSIONS). The path, or "" when it couldn't."""
        d = self.versions_dir()
        if not d or not os.path.exists(self.exe):
            return ""
        try:
            os.makedirs(d, exist_ok=True)
            dest = os.path.join(d, f"POUNCer-{self.current}.exe")
            if not os.path.exists(dest):
                import shutil
                shutil.copy2(self.exe, dest)
            olds = sorted(self.previous_versions(), key=lambda p: parse_version(p[0]))
            for _ver, path in olds[:-(KEEP_VERSIONS - 1)]:   # + the one just kept
                try:
                    os.remove(path)
                except OSError:
                    pass
            return dest
        except OSError as exc:
            log.info("could not keep a copy of %s: %s", self.current, exc)
            return ""

    def previous_versions(self) -> list[tuple[str, str]]:
        """[(version, path)] in versions/, newest first, not the running one."""
        d = self.versions_dir()
        out = []
        try:
            for name in os.listdir(d):
                m = re.match(r"POUNCer-(\d+(?:\.\d+)*)\.exe$", name)
                if m and m.group(1) != self.current:
                    out.append((m.group(1), os.path.join(d, name)))
        except OSError:
            return []
        return sorted(out, key=lambda p: parse_version(p[0]), reverse=True)

    def rollback(self, path: str) -> None:
        """Put a kept version in place of the running exe (used from the next
        start / restart)."""
        with open(path, "rb") as fh:
            data = fh.read()
        self._install(data, keep=True)
        m = re.search(r"POUNCer-([\d.]+)\.exe$", path)
        self.new_version = "v" + (m.group(1) if m else "?")
        self.state = READY
        log.info("going back to %s (from %s)", self.new_version, path)

    def _install(self, data: bytes, keep: bool = True) -> None:
        if keep:
            self.keep_current()            # the version we replace stays in versions/
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
