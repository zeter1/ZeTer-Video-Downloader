"""Guard user-facing application identity and Windows package metadata."""
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from src.core import app_updates
from src.core import constants


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

    def test_named_source_launcher_is_the_packaging_entry_point(self):
        workflow = (ROOT / ".github" / "workflows" / "python-check.yml").read_text(encoding="utf-8")
        filename = "ZeTer Video Downloader .py"
        self.assertIn(f"            '{filename}'", workflow)
        self.assertIn(filename, constants.SOURCE_CODE_FILES)
        self.assertEqual(constants.SOURCE_LAUNCHER_NAME, filename)
        self.assertFalse((ROOT / "video_downloader.py").exists())
        with patch("src.bootstrap.main") as main:
            runpy.run_path(str(ROOT / filename), run_name="__main__")
            main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
