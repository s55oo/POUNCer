"""Updates: two checksums (GitHub + SHA256SUMS.txt) and Switch version."""

import hashlib
import json
import unittest

from pouncer import update as up

EXE = b"MZ pretend exe"
H = hashlib.sha256(EXE).hexdigest()


def release(tag="v0.7.140", digest=H, sums=H, notes="", extra=()):
    assets = [{"name": "POUNCer.exe", "browser_download_url": "u/exe",
               "digest": f"sha256:{digest}" if digest else None}]
    if sums is not None:
        assets.append({"name": "SHA256SUMS.txt", "browser_download_url": "u/sums"})
    assets.extend(extra)
    return {"tag_name": tag, "body": notes, "assets": assets,
            "_sums": f"{sums}  POUNCer.exe\n0000000000000000000000000000000000000000000000000000000000000000  config.ini\n" if sums else ""}


def updater_for(rel, data=EXE):
    def fetch(url, accept=None, timeout=None):
        return {"u/exe": data, "u/sums": rel["_sums"].encode()}[url]
    return up.Updater("0.7.139", exe_path="C:/x/POUNCer.exe", fetch=fetch)


class TwoChecksumTests(unittest.TestCase):
    def test_both_agree(self) -> None:
        rel = release()
        self.assertEqual(updater_for(rel).download_verified(rel), EXE)

    def test_sums_disagree_with_github(self) -> None:
        rel = release(sums="ab" * 32)
        with self.assertRaisesRegex(ValueError, "disagree"):
            updater_for(rel).download_verified(rel)

    def test_file_changed(self) -> None:
        rel = release()
        with self.assertRaisesRegex(ValueError, "mismatch"):
            updater_for(rel, data=b"evil").download_verified(rel)

    def test_exe_missing_from_sums(self) -> None:
        rel = release()
        rel["_sums"] = "00" * 32 + "  config.ini\n"
        with self.assertRaisesRegex(ValueError, "missing"):
            updater_for(rel).download_verified(rel)

    def test_older_release_github_only(self) -> None:
        rel = release(tag="v0.7.120", sums=None)
        self.assertEqual(updater_for(rel).download_verified(rel), EXE)

    def test_oldest_release_notes_only(self) -> None:
        rel = release(tag="v0.7.50", digest=None, sums=None, notes=f"SHA-256: `{H.upper()}`")
        self.assertEqual(updater_for(rel).download_verified(rel), EXE)

    def test_nothing_published(self) -> None:
        rel = release(digest=None, sums=None)
        with self.assertRaisesRegex(ValueError, "no SHA-256"):
            updater_for(rel).download_verified(rel)

    def test_parse_sums(self) -> None:
        self.assertEqual(up.parse_sums(f"{H.upper()} *POUNCer.exe\r\n\njunk\n"), {"POUNCer.exe": H})


class InstallReleaseTests(unittest.TestCase):
    def test_switch_installs_after_the_checks(self) -> None:
        import time
        rel = release(tag="v0.7.120", sums=None)
        u = updater_for(rel)
        put = []
        u._install = put.append
        u.install_release(rel)
        for _ in range(100):
            if u.state in ("ready", "failed"):
                break
            time.sleep(0.02)
        self.assertEqual((u.state, u.new_version, put), ("ready", "v0.7.120", [EXE]))

    def test_bad_file_not_installed(self) -> None:
        import time
        rel = release()
        u = updater_for(rel, data=b"evil")
        put = []
        u._install = put.append
        u.install_release(rel)
        for _ in range(100):
            if u.state in ("ready", "failed"):
                break
            time.sleep(0.02)
        self.assertEqual((u.state, put), ("failed", []))


class ListReleasesTests(unittest.TestCase):
    def test_newest_first_no_prereleases(self) -> None:
        rels = [
            {"tag_name": "v0.7.9", "published_at": "2026-10-12T10:00:00Z",
             "assets": [{"name": "POUNCer.exe"}]},
            {"tag_name": "v0.7.139", "published_at": "2026-11-07T10:00:00Z",
             "assets": [{"name": "POUNCer.exe"}]},
            {"tag_name": "screenshots", "prerelease": True, "assets": [{"name": "a.png"}]},
            {"tag_name": "warmup", "assets": [{"name": "warm.sqlite"}]},
        ]
        u = up.Updater("0.7.139", fetch=lambda url, **k: json.dumps(rels).encode())
        got = u.list_releases()
        self.assertEqual([r["tag"] for r in got], ["v0.7.139", "v0.7.9"])
        self.assertEqual(got[0]["date"], "2026-11-07")


if __name__ == "__main__":
    unittest.main()
