"""Guard user-facing application identity and Windows package metadata."""
from pathlib import Path
import unittest

from src.core import app_updates


ROOT = Path(__file__).resolve().parents[1]


class BrandingTests(unittest.TestCase):
    def test_release_identity(self):
        self.assertEqual(app_updates.REPOSITORY, "zeter1/ZeTer-Video-Downloader")
        self.assertEqual(app_updates.EXE_NAME, "ZeTer-Video-Downloader.exe")

    def test_windows_version_metadata(self):
        metadata = (ROOT / "packaging" / "windows_version_info.txt").read_text(encoding="utf-8")
        for entry in (
            "StringStruct('FileDescription', 'ZeTer Video Downloader')",
            "StringStruct('ProductName', 'ZeTer Video Downloader')",
            "StringStruct('OriginalFilename', 'ZeTer-Video-Downloader.exe')",
        ):
            with self.subTest(entry=entry):
                self.assertIn(entry, metadata)

    def test_build_workflow_uses_branded_artifact(self):
        workflow = (ROOT / ".github" / "workflows" / "python-check.yml").read_text(encoding="utf-8")
        self.assertIn("'--version-file','packaging/windows_version_info.txt'", workflow)
        self.assertIn("ZeTer-Video-Downloader-Windows-x64", workflow)
        self.assertNotIn("'--name','Universal-Video-Downloader'", workflow)


if __name__ == "__main__":
    unittest.main()
