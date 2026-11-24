from collections import Counter
import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
from src.application import VideoDownloader
from src.core.errors import CommandCancelledError
from src.core.session_settings import session_setting
from src.downloads.queue_state import read_batch


class ManagerFailures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = VideoDownloader.__new__(VideoDownloader)
        app = self.app
        app.save_path = SimpleNamespace(get=lambda: self.temp.name)
        app.merge_audio = SimpleNamespace(get=lambda: False)
        app.cancel_flag = threading.Event()
        app.max_concurrent = 1
        app.download_threads = []
        app.download_start_time = 0
        app.manual_processing_dir = Path(self.temp.name)/'manual'
        for name in ('log','record_problem','finish_download','write_problem_session_summary',
                     'update_progress','save_failed_downloads_session'):
            setattr(app,name,Mock())
        app.cleanup_stale_download_temp_files = Mock(return_value=0)
        app.cleanup_stale_download_temp_dirs = Mock(return_value=0)
        app.is_url_downloaded = Mock(return_value=False)
        app.convert_videos_to_audio = Mock(return_value=[])
        app.download_single_video_task = Mock()

    def test_directory_error_still_finishes_and_reports_failure(self):
        with patch('src.downloads.manager.Path.mkdir', side_effect=PermissionError('denied')):
            self.app.download_manager(['url'])
        self.app.finish_download.assert_called_once()
        self.assertEqual(self.app.write_problem_session_summary.call_args.args[0], 'failed')

    def test_worker_exception_is_observed_and_link_retained(self):
        self.app.download_single_video_task.side_effect = RuntimeError('worker failed')
        self.app.download_manager(['url'])
        self.app.save_failed_downloads_session.assert_called_once_with(['url'])
        self.app.finish_download.assert_called_once()
        self.assertEqual(self.app.write_problem_session_summary.call_args.args[0], 'failed')

    def test_converter_error_still_finishes(self):
        self.app.convert_videos_to_audio.side_effect = OSError('conversion failed')
        self.app.download_manager(['url'])
        self.app.finish_download.assert_called_once()
        self.assertEqual(self.app.write_problem_session_summary.call_args.args[0], 'failed')

    def test_finish_follows_conversion_and_optional_merge(self):
        app = self.app
        steps = []
        app.merge_audio = SimpleNamespace(get=lambda: True)
        app.download_single_video_task.side_effect = (
            lambda url, index, videos, failed, problematic: videos.append('video.mp4')
        )
        app.convert_videos_to_audio.side_effect = lambda videos, target: (
            steps.append('converted') or ['audio.mp3']
        )
        app.sanitize_filename = lambda name: name
        app.merge_audio_files = Mock(side_effect=lambda *args: steps.append('merged'))
        app.finish_download.side_effect = lambda: steps.append('finished')
        app.download_manager(['url'])
        self.assertEqual(steps, ['converted', 'merged', 'finished'])
        self.assertEqual(app.download_outcome, 'completed')

    def prepare_recovered_video(self, *other_urls):
        app = self.app
        root = Path(self.temp.name)
        app.file_lock = threading.Lock()
        app._batch_lock = threading.Lock()
        app.started_queues_dir = root / 'queues'
        app.history_dir = root / 'history'
        app.downloaded_sessions_dir = root / 'downloaded'
        app.file_logger = Mock()
        app.current_session_start = None
        app.current_history_session_file = None
        app.current_download_log_session_file = None
        app.current_session_download_count = 0
        app.downloaded_url_hashes = set()
        app.downloaded_video_ids = set()
        app.validate_downloaded_video = Mock(return_value=(True, 'fixture approved'))
        del app.is_url_downloaded
        url = 'https://example.invalid/video'
        video = root / 'Видео' / 'готово.mp4'
        video.parent.mkdir()
        video.write_bytes(b'already published video')
        urls = [url, *other_urls]
        app.begin_download_batch(urls, urls, str(root), datetime.now().astimezone())
        app.prepare_media_receipt('video_files', url, video, video)
        return url, video

    def prepare_audio_conversion(self):
        app = self.app
        del app.convert_videos_to_audio
        app.audio_quality = SimpleNamespace(get=lambda: '192k')
        app.get_media_duration = Mock(return_value=2.0)
        app.validate_video_packets = Mock(return_value=True)
        app.validate_converted_audio = Mock(return_value=(True, 'fixture', 2.0, 2.0))
        app.run_command = Mock()

    def test_confirmed_no_audio_keeps_video_without_offering_retry(self):
        app = self.app
        url, video = self.prepare_recovered_video()
        original = video.read_bytes()
        self.prepare_audio_conversion()
        app._probe_media = Mock(return_value=(
            {'streams': [{'codec_type': 'video'}]}, SimpleNamespace(returncode=0, stderr=''),
        ))
        app.resume_queue_btn = Mock()
        app.is_downloading = False

        app.download_manager([])
        app._update_resume_button()

        self.assertEqual(video.read_bytes(), original)
        self.assertTrue(app.is_url_downloaded(url))
        self.assertEqual(read_batch(app._batch_path)['status'], 'failed')
        app.resume_queue_btn.grid.assert_not_called()
        app.run_command.assert_not_called()

    def test_unavailable_audio_metadata_leaves_retry_available(self):
        app = self.app
        _, video = self.prepare_recovered_video()
        original = video.read_bytes()
        self.prepare_audio_conversion()
        app._probe_media = Mock(return_value=(
            {}, SimpleNamespace(returncode=1, stderr='fixture transient metadata failure'),
        ))

        app.download_manager([])

        self.assertEqual(video.read_bytes(), original)
        self.assertEqual(read_batch(app._batch_path)['status'], 'audio_pending')
        app.run_command.assert_not_called()

    def test_mp3_failure_keeps_video_and_manual_continue_retries_only_audio(self):
        app = self.app
        failed_link = 'https://example.invalid/network-error'
        silent_url = 'https://example.invalid/silent'
        url, video = self.prepare_recovered_video(failed_link, silent_url)
        silent_video = video.with_name('Без звуковой дорожки.mp4')
        silent_video.write_bytes(b'video with no audio track')
        app.prepare_media_receipt('video_files', silent_url, silent_video, silent_video)
        root, archive = Path(self.temp.name), app._batch_path
        original = video.read_bytes()
        self.prepare_audio_conversion()

        def probe(path):
            streams = [{'codec_type': 'video'}]
            if Path(path) == video:
                streams.append({'codec_type': 'audio'})
            return {'streams': streams}, SimpleNamespace(returncode=0, stderr='')

        app._probe_media = Mock(side_effect=probe)
        convert_attempts = []

        def encode(command, timeout, operation, context):
            self.assertEqual(operation, 'convert_video_to_audio')
            self.assertEqual(Path(context['video_file']).read_bytes(), original)
            convert_attempts.append(command)
            if len(convert_attempts) == 1:
                Path(command[-1]).write_bytes(b'partial')
                return SimpleNamespace(returncode=1, stderr='encoder failure', stdout='')
            if len(convert_attempts) == 2:
                reopened.cancel_flag.set()
                self.assertTrue(reopened.confirm_queue_pause())
                raise CommandCancelledError('fixture cancelled')
            Path(command[-1]).write_bytes(b'retried mp3')
            return SimpleNamespace(returncode=0, stderr='', stdout='')

        app.run_command = Mock(side_effect=encode)
        app.download_single_video_task.side_effect = (
            lambda link, index, videos, failed, problematic: failed.append(link)
        )
        app.download_manager([failed_link])

        self.assertTrue(video.is_file(), 'MP3 failure moved the verified original')
        self.assertEqual(video.read_bytes(), original)
        self.assertTrue(app.is_url_downloaded(url))
        self.assertEqual(list((root / 'Аудио').glob('*.mp3')), [])
        partial, = (app.manual_processing_dir / 'Неполные MP3').glob('*.mp3')
        self.assertEqual(partial.read_bytes(), b'partial')
        self.assertEqual(read_batch(archive)['status'], 'audio_pending')
        app.save_failed_downloads_session.assert_called_once_with([failed_link])

        reopened = VideoDownloader.__new__(VideoDownloader)
        reopened.__dict__.update(vars(app))
        reopened.download_archive_dir = root / 'reopened-archive'
        reopened.root = Mock()
        reopened.url_text = Mock()
        reopened.url_text.get.return_value = ''
        reopened.save_path = Mock()
        reopened.save_path.get.return_value = str(root)
        reopened.is_downloading = False
        reopened.resume_queue_btn = Mock()
        reopened.initialize_queue_recovery()
        reopened.started_queues_dir = archive.parent
        def start(*, resume_urls):
            reopened.cancel_flag.clear()
            reopened.download_manager(resume_urls)

        reopened.start_download = Mock(side_effect=start)
        reopened.resume_interrupted_download()
        reopened.start_download.assert_not_called()
        reopened.resume_queue_btn.grid.assert_called_once()

        reopened.resume_paused_download()
        self.assertEqual(reopened.download_outcome, 'cancelled')
        self.assertEqual(read_batch(archive)['status'], 'audio_pending')
        self.assertEqual(list((root / 'Аудио').glob('*.mp3')), [])
        self.assertEqual(video.read_bytes(), original)
        reopened.resume_paused_download()

        self.assertEqual(reopened.start_download.call_args_list, [call(resume_urls=[]), call(resume_urls=[])])
        self.assertEqual(app.download_single_video_task.call_count, 1)
        self.assertEqual(video.read_bytes(), original)
        self.assertEqual(silent_video.read_bytes(), b'video with no audio track')
        mp3, = (root / 'Аудио').glob('*.mp3')
        self.assertEqual(mp3.read_bytes(), b'retried mp3')
        batch = read_batch(archive)
        self.assertEqual(batch['status'], 'failed')
        self.assertEqual(reopened._unfinished_batch_urls(batch), [failed_link])
        self.assertEqual(reopened.existing_batch_audio(video, mp3.parent), str(mp3))

    def test_video_decode_is_reused_for_65_unchanged_batch_outputs(self):
        app = self.app
        video_dir = Path(self.temp.name) / 'Видео'
        video_dir.mkdir()
        paths = [video_dir / f'video-{index}.mp4' for index in range(66)]
        for path in paths:
            path.write_bytes(b'original')
        probe_counts = Counter()

        def decode(command, timeout, operation, context):
            self.assertEqual(operation, 'check_video_decode')
            path = Path(context['media_path'])
            probe_counts[path] += 1
            invalid = path.read_bytes() == b'corrupt!'
            return SimpleNamespace(returncode=int(invalid), stderr='', stdout='')

        def worker(url, index, videos, failed, problematic):
            path = paths[index]
            if app.validate_video_packets(path):
                videos.append(str(path))

        checks = []

        def convert(videos, audio_dir):
            info = paths[-1].stat()
            replacement = paths[-1].with_suffix('.replacement')
            replacement.write_bytes(b'corrupt!')
            os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
            replacement.replace(paths[-1])
            for video in map(Path, videos):
                checks.append(app.validate_video_packets(video))
            return [str(path.with_suffix('.mp3')) for path in paths[:-1]]

        app.run_command = Mock(side_effect=decode)
        app.download_single_video_task.side_effect = worker
        app.convert_videos_to_audio.side_effect = convert
        app.download_manager([f'https://example.invalid/{i}' for i in range(len(paths))])

        self.assertEqual(checks, [True] * 65 + [False])
        self.assertEqual(probe_counts, Counter({path: 1 + (path == paths[-1]) for path in paths}))

    def test_recovered_video_backfills_history_idempotently(self):
        app = self.app
        url, video = self.prepare_recovered_video()
        app.convert_videos_to_audio.return_value = ['restored.mp3']

        app.download_manager([])

        app.download_single_video_task.assert_not_called()
        app.convert_videos_to_audio.assert_called_once_with([str(video)], Path(self.temp.name) / 'Аудио')
        human_history, = app.history_dir.glob('downloaded_*.txt')
        machine_history, = app.downloaded_sessions_dir.glob('downloaded_log_*.txt')
        contents = [path.read_bytes() for path in (human_history, machine_history)]
        self.assertTrue(app.is_url_downloaded(url))
        self.assertIn(f'1. {url}'.encode(), contents[0])
        self.assertIn(f'\t{url}\t{url}'.encode(), contents[1])

        app.downloaded_url_hashes.clear()
        app.downloaded_video_ids.clear()
        app.load_download_log()
        app.download_manager([])

        self.assertEqual([path.read_bytes() for path in (human_history, machine_history)], contents)
        app.validate_downloaded_video.assert_called_once_with(str(video), url)

    def test_recovered_corrupt_video_cannot_create_download_history(self):
        app = self.app
        url, video = self.prepare_recovered_video()
        app.validate_downloaded_video.return_value = (False, 'fixture corrupt frame')
        manual_video = Path(self.temp.name) / 'сохранённый повреждённый файл.mp4'
        app.move_video_to_manual_processing = Mock(side_effect=lambda path, _: str(Path(path).rename(manual_video)))

        app.download_manager([])

        self.assertEqual(app.download_outcome, 'failed')
        app.validate_downloaded_video.assert_called_once_with(str(video), url)
        app.convert_videos_to_audio.assert_called_once_with([], Path(self.temp.name) / 'Аудио')
        app.save_failed_downloads_session.assert_called_once_with([url])
        self.assertFalse(app.is_url_downloaded(url))
        self.assertFalse(app.history_dir.exists())
        self.assertEqual(manual_video.read_bytes(), b'already published video')

    def test_human_history_keeps_exact_url_if_machine_append_failed(self):
        app = self.app
        self.prepare_recovered_video()
        url = "HTTPS://vimeo.com/123?key=quoted'value)"
        self.assertTrue(app.validate_url(url))
        history_path, machine_path = app.ensure_download_session_files()

        def reject_machine_append(path, *args, **kwargs):
            if Path(path) == machine_path:
                raise PermissionError('fixture machine history locked')
            return open(path, *args, **kwargs)

        with patch('src.downloads.history.open', side_effect=reject_machine_append, create=True):
            self.assertTrue(app.save_download_log(url, if_missing=True))
        self.assertIn(url, history_path.read_text(encoding='utf-8'))
        self.assertNotIn(url, machine_path.read_text(encoding='utf-8'))

        legacy_url = 'https://www.dailymotion.com/video/legacy'
        (app.history_dir / 'downloaded_legacy.txt').write_text(f'Сеанс: ({legacy_url}).', encoding='utf-8')
        reopened = VideoDownloader.__new__(VideoDownloader)
        reopened.history_dir, reopened.downloaded_sessions_dir = app.history_dir, app.downloaded_sessions_dir
        reopened.downloaded_url_hashes, reopened.downloaded_video_ids = set(), set()
        reopened.file_logger = Mock()
        reopened.load_download_log()

        self.assertTrue(reopened.is_url_downloaded(url))
        self.assertFalse(reopened.is_url_downloaded('https://vimeo.com/123?key=quoted'))
        self.assertTrue(reopened.is_url_downloaded(legacy_url))

    def test_terminal_archive_failure_reports_failure_and_reuses_merge_on_restart(self):
        app = self.app
        url, _ = self.prepare_recovered_video()
        root = Path(self.temp.name)
        audio = root / 'Аудио' / 'готово.mp3'
        audio.parent.mkdir()
        audio.write_bytes(b'retained input')
        app.convert_videos_to_audio.return_value = [str(audio)]
        app.merge_audio = SimpleNamespace(get=lambda: True)
        app.sanitize_filename = lambda name: name
        stage = root / 'Разбивка аудио' / '.merge_fixture'
        stage.mkdir(parents=True)
        staged, final = stage / 'merged.mp3', stage.parent / 'готово.mp3'

        def publish(files, *_):
            staged.write_bytes(b'retained merge result')
            prepare = getattr(app, 'prepare_batch_merge_receipt', None)
            if prepare is not None:
                prepare(files, [(staged, final)])
            staged.rename(final)

        app.merge_audio_files = Mock(side_effect=publish)
        write = app._atomic_write_json_file

        def reject_terminal_only(path, batch):
            if batch['status'] == 'completed':
                raise PermissionError('fixture terminal write denied')
            write(path, batch)

        with patch.object(app, '_atomic_write_json_file', side_effect=reject_terminal_only):
            app.download_manager([])

        self.assertEqual(app.download_outcome, 'failed')
        self.assertEqual(app.write_problem_session_summary.call_args.args[0], 'failed')
        self.assertEqual(app.record_problem.call_args.args[2], 'download_batch_finish_write_failed')
        app.finish_download.assert_called_once()
        self.assertEqual(read_batch(app._batch_path)['status'], 'active')
        self.assertEqual(final.read_bytes(), b'retained merge result')
        app._batch = read_batch(app._batch_path)

        app.download_manager([])

        self.assertEqual(app.download_outcome, 'completed')
        app.merge_audio_files.assert_called_once()
        self.assertEqual(read_batch(app._batch_path)['status'], 'completed')
        self.assertTrue(app.is_url_downloaded(url))

    def test_cancelled_run_is_not_success(self):
        self.app.cancel_flag.set()
        self.app.download_manager(['url'])
        self.app.convert_videos_to_audio.assert_not_called()
        self.assertEqual(self.app.download_outcome, 'cancelled')
        self.app.finish_download.assert_called_once()

    def test_empty_temp_roots_removed_after_workers_finish(self):
        video_dir = Path(self.temp.name) / 'Видео'
        roots = [video_dir / name for name in ('_temp_downloads', '_quality_candidates')]

        def completed_job(*args):
            for root in roots:
                job = root / 'current_job'
                job.mkdir(parents=True)
                job.rmdir()
            (video_dir / 'готовое.mp4').write_bytes(b'published output')

        self.app.download_single_video_task.side_effect = completed_job
        self.app.download_manager(['url'])
        self.assertTrue(all(not root.exists() for root in roots))
        self.assertEqual((video_dir / 'готовое.mp4').read_bytes(), b'published output')

    def test_empty_temp_roots_also_pruned_on_session_failure(self):
        video_dir = Path(self.temp.name) / 'Видео'
        roots = [video_dir / name for name in ('_temp_downloads', '_quality_candidates')]
        for root in roots:
            root.mkdir(parents=True)
        self.app._run_download_session = Mock(side_effect=RuntimeError('fixture failure'))
        self.app.download_manager(['url'])
        self.assertTrue(all(not root.exists() for root in roots))
        self.assertEqual(self.app.download_outcome, 'failed')
        self.app.finish_download.assert_called_once()

    def test_reentrant_start_is_ignored(self):
        self.app.is_downloading = True
        self.app.start_download()
        self.app.download_single_video_task.assert_not_called()

    def test_session_setting_falls_back_when_proxy_ui_control_is_absent(self):
        app = SimpleNamespace(settings={
            'proxy_enabled': True,
            'proxy_url': 'socks5://127.0.0.1:1080',
        })
        self.assertTrue(session_setting(app, 'proxy_enabled'))
        self.assertEqual(
            session_setting(app, 'proxy_url'),
            'socks5://127.0.0.1:1080',
        )

    def test_worker_session_setting_prefers_start_snapshot(self):
        app = SimpleNamespace(
            settings={'proxy_enabled': True},
            download_settings_snapshot={'proxy_enabled': False},
        )
        result = []
        worker = threading.Thread(
            target=lambda: result.append(session_setting(app, 'proxy_enabled'))
        )
        worker.start()
        worker.join(timeout=2)
        self.assertEqual(result, [False])

    def test_existing_video_is_downloaded_when_skip_downloaded_is_disabled(self):
        app = self.app
        app.is_url_downloaded.return_value = True
        app.settings = {"skip_downloaded": True}
        app._run_download_session(['url'])
        app.download_single_video_task.assert_not_called()
        app.settings["skip_downloaded"] = False
        app._run_download_session(['url'])
        app.download_single_video_task.assert_called_once()

    def test_finish_ui_reports_cancellation(self):
        app = self.app
        app.download_outcome = 'cancelled'
        app.download_btn = Mock()
        app.cancel_btn = Mock()
        app.main_progress_frame = Mock()
        app.save_path_entry = Mock()
        app.browse_path_btn = Mock()
        app.format_duration = Mock(return_value='0s')
        app.show_completion_notification = Mock()
        app._finish_download_ui()
        self.assertFalse(app.is_downloading)
        self.assertEqual(app.log.call_args.args[1], 'WARNING')
