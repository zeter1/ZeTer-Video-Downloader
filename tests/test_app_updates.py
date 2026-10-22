"""Offline regression tests for GitHub Release-based application updates."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.core import app_updates as updates


def release(tag, digest="a" * 64, timestamp="2026-10-22T11:00:00Z"):
    return {
        "tag_name": tag, "draft": False, "published_at": timestamp,
        "assets": [{
            "name": updates.EXE_NAME, "state": "uploaded",
            "digest": "sha256:" + digest,
            "browser_download_url": (
                f"https://github.com/{updates.REPOSITORY}/releases/download/"
                f"{tag}/{updates.EXE_NAME}"
            )
        }],
        "html_url": f"https://github.com/{updates.REPOSITORY}/releases/tag/{tag}",
    }


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class UpdateTests(unittest.TestCase):
    def test_update_and_current(self):
        old = release("build-1111111", timestamp="2026-10-20T00:00:00Z")
        new = release("build-2222222")
        self.assertEqual(updates.parse_releases([old, new], "build-1111111")[0], "available")
        self.assertEqual(updates.parse_releases([old, new], "build-2222222"), ("current", None))

    def test_unknown_does_not_overwrite(self):
        self.assertEqual(updates.parse_releases([release("build-1111111")], None), ("unidentified", None))
        self.assertEqual(
            updates.parse_releases([release("build-1111111")], "build-eeeeeee"),
            ("unidentified", None),
        )

    def test_missing_digest_and_untrusted_asset_are_ignored(self):
        item = release("build-2222222")
        item["assets"][0]["digest"] = None
        self.assertEqual(updates.parse_releases([item], "build-1111111"), ("unavailable", None))
        item = release("build-2222222")
        item["assets"][0]["browser_download_url"] = "https://example.org/malware.exe"
        self.assertEqual(updates.parse_releases([item], "build-1111111"), ("unavailable", None))

    def test_verified_download(self):
        payload = b"MZ" + (b"x" * 2048)
        item = updates.parse_releases(
            [release("build-2222222", hashlib.sha256(payload).hexdigest()),
             release("build-1111111", timestamp="2026-10-20T00:00:00Z")],
            "build-1111111",
        )[1]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(updates, "installed_build_tag", return_value=None):
                output = updates.download_verified(
                    item, tmp, opener=lambda req, timeout: FakeResponse(payload)
                )
            self.assertEqual(output.read_bytes(), payload)
            self.assertFalse((Path(tmp) / (updates.EXE_NAME + ".part")).exists())

    def test_bad_checksum_removes_partial(self):
        item = updates.parse_releases(
            [release("build-2222222"), release("build-1111111",
             timestamp="2026-10-20T00:00:00Z")], "build-1111111"
        )[1]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                updates.download_verified(item, tmp, opener=lambda req, timeout: FakeResponse(b"x" * 2048))
            self.assertFalse((Path(tmp) / (updates.EXE_NAME + ".part")).exists())

    def test_packaged_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "build_info.json"
            manifest.write_text(json.dumps({
                "repository": updates.REPOSITORY, "commit": "a" * 40,
                "tag": "build-aaaaaaa",
            }), encoding="utf-8")
            with mock.patch.object(updates.sys, "frozen", True, create=True), mock.patch.object(
                updates.sys, "_MEIPASS", tmp, create=True
            ):
                self.assertEqual(updates.installed_build_tag(), "build-aaaaaaa")
            manifest.write_text('{"repository": "other", "commit": "' + 'a' * 40 + '", "tag": "build-aaaaaaa"}')
            with mock.patch.object(updates.sys, "frozen", True, create=True), mock.patch.object(
                updates.sys, "_MEIPASS", tmp, create=True
            ):
                self.assertIsNone(updates.installed_build_tag())


if __name__ == "__main__":
    unittest.main()
