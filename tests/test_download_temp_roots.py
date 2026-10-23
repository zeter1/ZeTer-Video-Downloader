"""Empty-only maintenance: isolated bytes/paths, no downloads or app startup."""

import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.downloads.files import DownloadFilesMixin


class DownloadTempRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='пустые папки ')
        self.addCleanup(self.temp.cleanup)
        self.video_dir = Path(self.temp.name) / 'Видео'
        self.video_dir.mkdir()
        self.app = DownloadFilesMixin()
        self.app.record_problem = Mock()
        self.roots = [self.video_dir / name for name in ('_temp_downloads', '_quality_candidates')]

    def test_only_named_empty_roots_are_removed_and_repeat_is_safe(self):
        for root in self.roots:
            root.mkdir()
        foreign = self.video_dir / 'чужая пустая папка'
        foreign.mkdir()
        output = self.video_dir / 'готовое.mp4'
        output.write_bytes(b'published video')
        self.assertEqual(self.app.cleanup_empty_download_temp_roots(self.video_dir), 2)
        self.assertFalse(any(root.exists() for root in self.roots))
        self.assertTrue(foreign.is_dir())
        self.assertEqual(output.read_bytes(), b'published video')
        self.assertEqual(self.app.cleanup_empty_download_temp_roots(self.video_dir), 0)

    def test_active_foreign_and_recovery_content_are_preserved_byte_for_byte(self):
        originals = {}
        for root in self.roots:
            job = root / 'active_job'
            job.mkdir(parents=True)
            for name, data in (('video.part', b'active'), ('.preserve_download', b''),
                               ('candidate.mp4', b'only recovery copy')):
                path = job / name
                path.write_bytes(data)
                originals[path] = data
        self.assertEqual(self.app.cleanup_empty_download_temp_roots(self.video_dir), 0)
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)

    def test_access_failure_is_reported_and_other_empty_root_is_removed(self):
        for root in self.roots:
            root.mkdir()
        real_rmdir = Path.rmdir

        def locked(path):
            if path == self.roots[0]:
                raise PermissionError('fixture lock')
            return real_rmdir(path)

        with patch.object(Path, 'rmdir', locked):
            self.assertEqual(self.app.cleanup_empty_download_temp_roots(self.video_dir), 1)
        self.assertTrue(self.roots[0].is_dir())
        self.assertFalse(self.roots[1].exists())
        self.app.record_problem.assert_called_once()
        self.assertEqual(self.app.record_problem.call_args.args[1:3],
                         ('WARNING', 'cleanup_empty_download_temp_roots'))

    def test_reparse_attribute_is_refused_before_rmdir(self):
        for root in self.roots:
            root.mkdir()
        real_lstat = Path.lstat

        def reparse(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if path == self.roots[0]:
                return SimpleNamespace(st_mode=info.st_mode,
                    st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                    st_dev=info.st_dev, st_ino=info.st_ino)
            return info

        with patch.object(Path, 'lstat', reparse):
            self.assertEqual(self.app.cleanup_empty_download_temp_roots(self.video_dir), 1)
        self.assertTrue(self.roots[0].is_dir())
        self.assertFalse(self.roots[1].exists())

    def test_real_windows_junction_and_its_external_target_are_untouched(self):
        try:
            import _winapi
        except ImportError:
            self.skipTest('Windows junction test')
        target = Path(self.temp.name) / 'вне Видео'
        target.mkdir()
        sentinel = target / 'сохранить.txt'
        sentinel.write_bytes(b'outside sentinel')
        link = self.roots[0]
        try:
            _winapi.CreateJunction(str(target), str(link))
        except OSError as error:
            self.skipTest(f'Cannot create fixture junction: {error}')
        self.addCleanup(link.rmdir)
        self.roots[1].mkdir()
        self.assertEqual(self.app.cleanup_empty_download_temp_roots(self.video_dir), 1)
        self.assertTrue(link.is_dir())
        self.assertEqual(sentinel.read_bytes(), b'outside sentinel')
        self.assertFalse(self.roots[1].exists())


if __name__ == '__main__':
    unittest.main()
