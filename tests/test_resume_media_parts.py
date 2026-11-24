"""Synthetic yt-dlp restarts without network access or application startup."""

from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from src.core.download_temp import DownloadTempMixin
from src.core.errors import CommandCancelledError
from src.downloads.files import DownloadFilesMixin, _publish_staged_subtitle
from src.downloads.youtube_downloader import YouTubeDownloaderMixin
from src.downloads.ytdlp_results import YtDlpResultsMixin


class SyntheticDownloader(YouTubeDownloaderMixin, DownloadFilesMixin, DownloadTempMixin, YtDlpResultsMixin):
    def __init__(self, root, run_command):
        self.settings = {'save_path': str(root), 'download_subtitles': False}
        self.cancel_flag = threading.Event()
        self.file_lock = threading.Lock()
        self.extract_video_id = Mock(return_value=None)
        self.is_youtube_url = Mock(return_value=False)
        self.get_proxy_url = Mock(return_value='')
        self.build_proxy_args = Mock(return_value=[])
        self.mask_proxy_url = Mock(return_value='')
        self.youtube_ejs_status_text = Mock(return_value='synthetic')
        self.proxy_status_text = Mock(return_value='synthetic')
        self.log = Mock()
        self.record_problem = Mock()
        self.file_logger = Mock()
        self._log_video_info = Mock()
        self.get_video_dimensions = Mock(return_value=(1920, 1080))
        self.validate_downloaded_video = Mock(return_value=(True, 'synthetic verified media'))
        self.validate_video_packets = Mock(return_value=True)
        self.is_mp4_container = Mock(return_value=True)
        self.video_tracks_within_height_limit = Mock(return_value=True)
        self.run_command = run_command


class ResumeMediaPartsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='загрузка с пробелами ')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.video_dir = self.root / 'Видео'
        self.url = 'https://example.invalid/media?a=1'
        strategy = {'name': 'synthetic', 'args': []}
        self.plan = patch('src.downloads.youtube_downloader.build_ytdlp_strategy_plan',
                          return_value=([strategy], [], '1'))
        self.plan.start()
        self.addCleanup(self.plan.stop)

    @staticmethod
    def media_path(command):
        template = command[command.index('-o') + 1]
        return Path(template.replace('%(title).150B', 'Моё видео')
                    .replace('%(ext)s', 'mp4')
                    .replace('%(format_id)s', '18')
                    .replace('%(protocol)s', 'https'))

    def test_cancel_new_session_resume_and_publish_without_touching_other_files(self):
        partial_bytes = b'earlier bytes' * 300
        tail = b'completed tail'
        created = {}

        def cancelled(command, *_args):
            output = self.media_path(command)
            part = output.with_suffix('.mp4.part')
            index = output.with_suffix('.mp4.ytdl')
            part.write_bytes(partial_bytes)
            index.write_bytes(b'synthetic fragment index')
            created.update(part=part, index=index)
            first.cancel_flag.set()
            raise CommandCancelledError()

        first = SyntheticDownloader(self.root, cancelled)
        self.assertIsNone(first.method_yt_dlp(self.url))
        self.assertEqual(created['part'].read_bytes(), partial_bytes)
        foreign = self.video_dir / 'независимый проект.part'
        foreign.write_bytes(b'foreign file')
        previous = self.video_dir / 'Моё видео.mp4'
        previous.write_bytes(b'earlier published output')

        def resumed(command, *_args):
            output = self.media_path(command)
            part = output.with_suffix('.mp4.part')
            self.assertIn('--continue', command)
            self.assertEqual(part, created['part'])
            self.assertEqual(part.read_bytes(), partial_bytes)
            self.assertEqual(output.with_suffix('.mp4.ytdl').read_bytes(), b'synthetic fragment index')
            output.write_bytes(part.read_bytes() + tail)
            return subprocess.CompletedProcess(command, 0, f'[ZVD_RESULT] {output}', '')

        second = SyntheticDownloader(self.root, resumed)
        second.cleanup_stale_download_temp_files(self.video_dir)
        second.cleanup_stale_download_temp_dirs(self.video_dir)
        result = second.method_yt_dlp(self.url)

        self.assertEqual(result, str(self.video_dir / 'Моё видео (2).mp4'))
        self.assertEqual(Path(result).read_bytes(), partial_bytes + tail)
        self.assertEqual(previous.read_bytes(), b'earlier published output')
        self.assertEqual(foreign.read_bytes(), b'foreign file')
        self.assertFalse(created['part'].exists())
        self.assertFalse(created['index'].exists())

    def test_publish_selected_subtitles_with_video_collision(self):
        self.video_dir.mkdir()
        old_video = self.video_dir / 'Моё видео.mp4'
        old_video.write_bytes(b'old video')
        old_subtitle = self.video_dir / 'Моё видео (2).en.vtt'
        old_subtitle.write_bytes(b'old subtitles')
        output = {}

        def downloaded(command, *_args):
            selected = self.media_path(command)
            selected.write_bytes(b'video' * 500)
            selected.with_suffix('.ru.vtt').write_bytes(b'WEBVTT\n\nRussian\n')
            selected.with_suffix('.en.vtt').write_bytes(b'WEBVTT\n\nEnglish\n')
            foreign = selected.parent / 'unrelated.en.vtt'
            foreign.write_bytes(b'unrelated')
            wrong = selected.with_name(selected.name.replace('-18]', '-137]'))
            wrong.with_suffix('.en.vtt').write_bytes(b'wrong format')
            selected.with_suffix('.en.vtt.part').write_bytes(b'incomplete')
            output.update(foreign=foreign)
            return subprocess.CompletedProcess(command, 0, f'[ZVD_RESULT] {selected}', '')

        app = SyntheticDownloader(self.root, downloaded)
        app.settings['download_subtitles'] = True
        path = app.method_yt_dlp(self.url)
        self.assertEqual(path, str(self.video_dir / 'Моё видео (3).mp4'))
        self.assertEqual(Path(path).with_suffix('.en.vtt').read_bytes(), b'WEBVTT\n\nEnglish\n')
        self.assertEqual(Path(path).with_suffix('.ru.vtt').read_bytes(), b'WEBVTT\n\nRussian\n')
        self.assertEqual(old_subtitle.read_bytes(), b'old subtitles')
        self.assertEqual(old_video.read_bytes(), b'old video')
        self.assertEqual(output['foreign'].read_bytes(), b'unrelated')
        self.assertFalse(list(self.video_dir.glob('*.part')))

    def test_lower_quality_candidate_keeps_its_own_subtitles(self):
        def downloaded(command, *_args):
            selected = self.media_path(command)
            selected.write_bytes(b'video' * 500)
            selected.with_suffix('.en.vtt').write_bytes(b'WEBVTT\n\nFallback\n')
            return subprocess.CompletedProcess(command, 0, f'[ZVD_RESULT] {selected}', '')

        app = SyntheticDownloader(self.root, downloaded)
        app.settings['download_subtitles'] = True
        app.get_video_dimensions.return_value = (640, 360)
        path = app.method_yt_dlp(self.url)
        self.assertEqual(path, str(self.video_dir / 'Моё видео.mp4'))
        self.assertEqual(Path(path).with_suffix('.en.vtt').read_bytes(), b'WEBVTT\n\nFallback\n')

    def test_restart_reuses_complete_subtitle_left_before_video_publication(self):
        self.video_dir.mkdir()
        video = self.video_dir / 'Моё видео.mp4'
        subtitle = video.with_suffix('.en.vtt')
        text = b'WEBVTT\n\nMatching subtitle\n'
        subtitle.write_bytes(text)
        old_mtime = subtitle.stat().st_mtime_ns

        first = SyntheticDownloader(self.root, Mock())
        interrupted = first.open_video_download_workspace(self.video_dir, self.url, None)
        (interrupted / '.preserve_download').touch()

        def downloaded(command, *_args):
            selected = self.media_path(command)
            self.assertNotEqual(selected.parent, interrupted)
            selected.write_bytes(b'video' * 500)
            selected.with_suffix('.en.vtt').write_bytes(text)
            selected.with_suffix('.ru.vtt').write_bytes(b'WEBVTT\n\nSecond subtitle\n')
            return subprocess.CompletedProcess(command, 0, f'[ZVD_RESULT] {selected}', '')

        app = SyntheticDownloader(self.root, downloaded)
        app.settings['download_subtitles'] = True
        self.assertEqual(app.method_yt_dlp(self.url), str(video))
        self.assertEqual(list(self.video_dir.glob('*.mp4')), [video])
        self.assertEqual(set(self.video_dir.glob('*.vtt')), {subtitle, video.with_suffix('.ru.vtt')})
        self.assertEqual(subtitle.read_bytes(), text)
        self.assertEqual(subtitle.stat().st_mtime_ns, old_mtime)

    def test_interrupted_subtitle_copy_never_exposes_partial_final_file(self):
        def downloaded(command, *_args):
            selected = self.media_path(command)
            selected.write_bytes(b'video' * 500)
            selected.with_suffix('.en.vtt').write_bytes(b'WEBVTT\n\nFull subtitle\n')
            return subprocess.CompletedProcess(command, 0, f'[ZVD_RESULT] {selected}', '')

        app = SyntheticDownloader(self.root, downloaded)
        app.settings['download_subtitles'] = True
        seen_public_files = []

        def interrupted_copy(data, output):
            output.write(data.read(6))
            output.flush()
            seen_public_files.extend(self.video_dir.glob('*.vtt'))
            raise OSError('interrupted copy')

        with patch('src.downloads.files.shutil.copyfileobj', interrupted_copy):
            self.assertIsNone(app.method_yt_dlp(self.url))
        self.assertEqual(seen_public_files, [])
        self.assertFalse(list(self.video_dir.glob('*.vtt')))
        self.assertFalse(list(self.video_dir.glob('*.mp4')))

    def test_subtitle_publication_failure_leaves_recoverable_attempt(self):
        created = {}

        def downloaded(command, *_args):
            selected = self.media_path(command)
            selected.write_bytes(b'video' * 500)
            subtitle = selected.with_suffix('.en.vtt')
            subtitle.write_bytes(b'WEBVTT\n\nRecoverable\n')
            second = selected.with_suffix('.ru.vtt')
            second.write_bytes(b'WEBVTT\n\nSecond\n')
            created.update(video=selected, subtitle=subtitle, second=second)
            return subprocess.CompletedProcess(command, 0, f'[ZVD_RESULT] {selected}', '')

        app = SyntheticDownloader(self.root, downloaded)
        app.settings['download_subtitles'] = True
        final_writes = []

        def lock_final_subtitle(staged, target):
            if target.parent == self.video_dir:
                final_writes.append(target)
                if len(final_writes) == 2:
                    raise PermissionError('locked subtitle')
            _publish_staged_subtitle(staged, target)

        with patch('src.downloads.files._publish_staged_subtitle', lock_final_subtitle):
            self.assertIsNone(app.method_yt_dlp(self.url))
        self.assertEqual(created['video'].read_bytes(), b'video' * 500)
        self.assertEqual(created['subtitle'].read_bytes(), b'WEBVTT\n\nRecoverable\n')
        self.assertEqual(created['second'].read_bytes(), b'WEBVTT\n\nSecond\n')
        self.assertEqual(len(final_writes), 2)
        self.assertFalse(list(self.video_dir.glob('*.vtt')))
        self.assertFalse(list(self.video_dir.glob('*.mp4')))

        path = app.finalize_downloaded_video_file(str(created['video']), self.video_dir, None, url=self.url)
        self.assertEqual(path, str(self.video_dir / 'Моё видео.mp4'))
        self.assertEqual(Path(path).with_suffix('.en.vtt').read_bytes(), b'WEBVTT\n\nRecoverable\n')
        self.assertEqual(Path(path).with_suffix('.ru.vtt').read_bytes(), b'WEBVTT\n\nSecond\n')


if __name__ == '__main__':
    unittest.main()
