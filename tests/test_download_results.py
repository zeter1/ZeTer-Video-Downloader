import logging
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.application import VideoDownloader
from src.core.download_temp import RESUME_MARKER, resume_workspace_key
from src.downloads.youtube_success import find_completed_attempt_output, handle_successful_attempt


class DownloadResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = VideoDownloader.__new__(VideoDownloader)
        self.app.file_lock = threading.Lock()
        self.app.cancel_flag = threading.Event()
        self.app.subprocess_flags = 0
        self.app.log = Mock()
        self.app.record_problem = Mock()
        self.app.file_logger = logging.getLogger('result-tests')
        self.app.run_command = self._run_ffmpeg

    @staticmethod
    def _run_ffmpeg(command, timeout, _operation, _context):
        return subprocess.run(command, timeout=timeout, capture_output=True, text=True)

    def media(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * 2000)
        return path

    def test_dash_fragment_is_not_a_finished_download(self):
        fragment = self.media('job/title.f137.mp4')
        result = self.app.parse_yt_dlp_output(
            '[download] Destination: ' + str(fragment), 'https://example.invalid',
            fragment.parent, set())
        self.assertIsNone(result)
        self.assertTrue(fragment.exists())

    def test_output_cannot_select_file_outside_current_job(self):
        outside = self.media('other-job/video.mp4')
        job = self.root / 'job'
        job.mkdir()
        result = self.app.parse_yt_dlp_output(str(outside), 'https://example.invalid', job, set())
        self.assertIsNone(result)
        self.assertEqual(outside.stat().st_size, 2000)

    def test_existing_id_match_is_not_new_output(self):
        old = self.media('old [videoid].mp4')
        self.assertIsNone(self.app.find_newest_file(self.root, {old}, 'videoid'))

    def test_ambiguous_outputs_are_not_guessed(self):
        self.media('one.mp4')
        self.media('two.mkv')
        self.assertIsNone(self.app.find_newest_file(self.root, set(), 'id'))

    def test_exact_new_output_is_returned(self):
        output = self.media('job/видео into тест.mkv')
        result = self.app.parse_yt_dlp_output(
            '[Merger] Merging formats into "' + str(output) + '"',
            'https://example.invalid', output.parent, set())
        self.assertEqual(result, str(output))

    def _resume_workspace(self):
        video_dir = self.root / 'Готовые видео'
        video_dir.mkdir(exist_ok=True)
        url = 'https://vimeo.com/123'
        workspace = self.app.open_video_download_workspace(video_dir, url, None)
        prefix = f'ZVD-{resume_workspace_key(workspace)[:8]} [native-https-18] '
        return video_dir, url, workspace, prefix

    def test_reported_titles_with_temp_or_dash_text_are_published_and_confirmed(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        sample = self.root / 'sample.mp4'
        self._make_mp4(sample, '96x64')
        expected_bytes = sample.read_bytes()
        for name in ('lesson.f137.mp4', 'lesson.temp.mp4', 'урок.temp.часть.mp4'):
            with self.subTest(name=name):
                video_dir, url, workspace, prefix = self._resume_workspace()
                selected = workspace / (prefix + name)
                selected.write_bytes(expected_bytes)
                command = ['yt-dlp']
                result = subprocess.CompletedProcess(command, 0, f'[ZVD_RESULT] {selected}', '')
                decision = handle_successful_attempt(
                    self.app, result=result, url=url, attempt=1,
                    strategy={'name': 'synthetic progressive', 'progressive_format': True},
                    command=command, download_temp_dir=workspace, video_dir=video_dir,
                    quality_candidate_dir=video_dir / '_quality_candidates' / 'candidate-1',
                    video_files_before=set(), expected_video_id=None, best_candidate_path=None,
                    best_candidate_height=-1, best_candidate_width=-1, attempt_summaries=[],
                )
                self.assertEqual(decision.action, 'success')
                published = video_dir / name
                self.assertEqual(decision.video_path, str(published))
                self.assertTrue(self.app.wait_for_file(decision.video_path, timeout=0))
                self.assertEqual(published.read_bytes(), expected_bytes)
                self.assertFalse(selected.exists())

    def test_owned_workspace_requires_current_unique_complete_report(self):
        _, url, workspace, prefix = self._resume_workspace()
        old = workspace / (prefix + 'old.mp4')
        second = workspace / (prefix + 'other.mp4')
        partial = workspace / (prefix + 'unfinished.mp4.part')
        foreign = workspace / 'foreign.mp4'
        outside = self.root / (prefix + 'outside.mp4')
        linked = workspace / (prefix + 'linked.mp4')
        for path in (old, second, partial, foreign, outside, linked):
            path.write_bytes(b'x' * 2000)

        def identify(*reports):
            output = '\n'.join(f'[ZVD_RESULT] {path}' for path in reports)
            result = subprocess.CompletedProcess(['yt-dlp'], 0, output, '')
            return find_completed_attempt_output(self.app, result, url, workspace, {old}, None)

        for reports in ((), (partial,), (foreign,), (outside,), (old, second), (old, partial)):
            with self.subTest(reports=reports):
                self.assertIsNone(identify(*reports))
        self.assertEqual(identify(old), str(old))
        original_lstat = Path.lstat
        for link_info in (
            SimpleNamespace(st_mode=stat.S_IFLNK, st_nlink=1),
            SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=2),
            SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1,
                            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT),
        ):
            with self.subTest(link_info=link_info), patch.object(
                Path, 'lstat', lambda path: link_info if path == linked else original_lstat(path)
            ):
                self.assertIsNone(identify(linked))
                self.assertFalse(self.app.wait_for_file(str(linked), timeout=0))
        (workspace / RESUME_MARKER).write_bytes(b'foreign marker')
        self.assertIsNone(identify(old))
        self.assertEqual(outside.read_bytes(), b'x' * 2000)

    def test_temporary_alternative_does_not_satisfy_wait(self):
        self.media('movie.temp.mp4')
        with patch('src.downloads.ytdlp_results.time.sleep'):
            self.assertFalse(self.app.wait_for_file(str(self.root/'movie.mp4'), timeout=.001))

    def test_exhausted_names_never_return_existing_target(self):
        with patch.object(Path, 'exists', return_value=True):
            with self.assertRaises(FileExistsError):
                self.app.make_unique_media_path(self.root/'video.mp4')



    def test_download_plan_dimensions_are_available_when_ffprobe_metadata_fails(self):
        output = (
            "[DOWNLOAD_PLAN] id=x format_id=333+140 protocol=https+https "
            "ext=mp4 height=854 width=480 vcodec=vp9.2 acodec=mp4a.40.2"
        )
        self.assertEqual(self.app.parse_download_plan_dimensions(output), (480, 854))

    def test_finalize_normalizes_non_mp4_with_lossless_remux(self):
        source = self.media('job/movie.mkv')
        self.app.validate_video_packets = Mock(return_value=True)
        calls = []

        def fake_run(command, timeout, operation, context=None, allow_cancel=True):
            calls.append((operation, list(command)))
            Path(command[-1]).write_bytes(b'mp4' * 1000)
            return subprocess.CompletedProcess(command, 0, '', '')

        self.app.run_command = fake_run
        self.app.validate_normalized_mp4 = Mock(return_value=True)
        result = self.app.finalize_downloaded_video_file(str(source), self.root/'final', 'id')
        self.assertIsNotNone(result)
        self.assertEqual(Path(result).suffix.lower(), '.mp4')
        self.assertTrue(Path(result).exists())
        self.assertFalse(source.exists())
        self.assertEqual(calls[0][0], 'mp4_output_remux')
        self.assertIn('copy', calls[0][1])
        self.assertEqual(len(calls), 1)

    def test_finalize_uses_h264_aac_only_if_stream_copy_remux_fails(self):
        source = self.media('job/movie.webm')
        self.app.validate_video_packets = Mock(return_value=True)
        calls = []

        def fake_run(command, timeout, operation, context=None, allow_cancel=True):
            calls.append((operation, list(command)))
            if operation == 'mp4_output_remux':
                return subprocess.CompletedProcess(command, 1, '', 'mux unsupported')
            Path(command[-1]).write_bytes(b'mp4' * 1000)
            return subprocess.CompletedProcess(command, 0, '', '')

        self.app.run_command = fake_run
        self.app.validate_normalized_mp4 = Mock(return_value=True)
        result = self.app.finalize_downloaded_video_file(str(source), self.root/'final', 'id')
        self.assertIsNotNone(result)
        self.assertEqual(Path(result).suffix.lower(), '.mp4')
        self.assertEqual([name for name, _ in calls], ['mp4_output_remux', 'mp4_output_transcode'])
        self.assertIn('libx264', calls[1][1])
        self.assertIn('aac', calls[1][1])

    def test_normalization_final_publish_failure_preserves_original_and_mp4_collision(self):
        source = self.media('job/movie.mkv')
        self.app.validate_video_packets = Mock(return_value=True)
        collision = source.with_suffix('.mp4')
        collision.write_bytes(b'prior unrelated MP4')

        def fake_run(command, *_args):
            Path(command[-1]).write_bytes(b'mp4' * 1000)
            return subprocess.CompletedProcess(command, 0, '', '')

        self.app.run_command = fake_run
        self.app.validate_normalized_mp4 = Mock(return_value=True)
        with patch('src.downloads.files.shutil.move', side_effect=PermissionError('locked')):
            self.assertIsNone(self.app.finalize_downloaded_video_file(
                str(source), self.root / 'final', 'id'))
        self.assertTrue(source.exists())
        self.assertTrue(collision.exists())
        self.assertEqual(source.read_bytes(), b'x' * 2000)
        self.assertEqual(collision.read_bytes(), b'prior unrelated MP4')

        published = self.app.finalize_downloaded_video_file(str(source), self.root / 'final', 'id')
        self.assertEqual(Path(published), self.root / 'final' / 'movie.mp4')
        self.assertEqual(Path(published).read_bytes(), b'mp4' * 1000)
        self.assertEqual(collision.read_bytes(), b'prior unrelated MP4')
        self.assertFalse(source.exists())

    def test_normalization_rejects_invalid_zero_exit_output(self):
        source = self.media('job/movie.mkv')
        self.app.validate_video_packets = Mock(return_value=True)

        def fake_run(command, *_args):
            Path(command[-1]).write_bytes(b'bad mp4' * 1000)
            return subprocess.CompletedProcess(command, 0, '', '')

        self.app.run_command = fake_run
        self.assertIsNone(self.app.finalize_downloaded_video_file(str(source), self.root / 'final', 'id'))
        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), b'x' * 2000)

    def test_direct_mp4_reuses_probe_through_final_validation(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        source = self.root / 'job' / 'короткое видео.mp4'
        source.parent.mkdir()
        self._make_mp4(source, '96x64')
        url = 'https://example.invalid/video'

        with patch('src.downloads.media_probe.subprocess.run', wraps=subprocess.run) as probe:
            self.assertTrue(self.app.validate_downloaded_video(str(source), url)[0])
            self.assertEqual(self.app.get_video_dimensions(str(source)), (96, 64))
            result = self.app.finalize_downloaded_video_file(str(source), self.root / 'final', 'id')
            self.assertEqual(result, str(self.root / 'final' / source.name))
            self.app._log_video_info(result)
            self.assertTrue(self.app.validate_downloaded_video(result, url)[0])
            # Windows keeps the same file identity and metadata after this rename.
            commands = [call.args[0][0] for call in probe.call_args_list]
            expected_per_tool = 1 if os.name == 'nt' else 2
            self.assertEqual(commands.count('ffprobe'), expected_per_tool)
            self.assertEqual(commands.count('ffmpeg'), expected_per_tool)
        self.assertFalse(source.exists())
        metadata_log = next(args[0] for args, _ in self.app.log.call_args_list if '96x64 (64p)' in args[0])
        self.assertIn('kbps', metadata_log)
        self.assertIn('00:00:02', metadata_log)

    def test_replacement_at_same_path_is_probed_again(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        original = self.root / 'file.mp4'
        replacement = self.root / 'replacement.mp4'
        self._make_mp4(original, '96x64')
        self._make_mp4(replacement, '128x96')
        # Size and last-write time alone do not prove that this is the same file.
        original_stat = original.stat()
        size = max(original_stat.st_size, replacement.stat().st_size)
        for path in (original, replacement):
            with path.open('ab') as media:
                media.truncate(size)
            os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        with patch('src.downloads.media_probe.subprocess.run', wraps=subprocess.run) as probe:
            self.assertEqual(self.app.get_video_dimensions(str(original)), (96, 64))
            replacement.replace(original)
            self.assertEqual(self.app.get_video_dimensions(str(original)), (128, 96))
            self.assertEqual(probe.call_count, 2)

    def test_failed_probe_is_not_reused(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        source = self.root / 'video.mp4'
        self._make_mp4(source, '96x64')
        real_run = subprocess.run
        failed = False

        def fail_once(command, *args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                return subprocess.CompletedProcess(command, 1, '', 'temporarily unreadable')
            return real_run(command, *args, **kwargs)

        with patch('src.downloads.media_probe.subprocess.run', side_effect=fail_once) as probe:
            self.assertEqual(self.app.get_media_duration(str(source)), 0)
            self.assertAlmostEqual(self.app.get_media_duration(str(source)), 2, delta=.1)
            self.assertTrue(self.app.has_audio_stream(str(source)))
            self.assertEqual(probe.call_count, 2)

    def test_transcode_rechecks_same_staging_path_after_rejected_remux(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        source = self.root / 'original.mkv'
        remux = self.root / 'wrong.mp4'
        transcode = self.root / 'correct.mp4'
        for path, dimensions in ((source, '96x64'), (remux, '128x96'), (transcode, '96x64')):
            self._make_mp4(path, dimensions)
        length = max(remux.stat().st_size, transcode.stat().st_size)
        for path in (remux, transcode):
            with path.open('ab') as media:
                media.truncate(length)
        operations = []
        source_stat = source.stat()

        def run(command, timeout, operation, context):
            if operation == 'check_video_decode':
                return self._run_ffmpeg(command, timeout, operation, context)
            target = Path(command[-1])
            candidate = remux if operation == 'mp4_output_remux' else transcode
            target.write_bytes(candidate.read_bytes())
            os.utime(target, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            operations.append((operation, target, target.stat()))
            return subprocess.CompletedProcess(command, 0, '', '')

        self.app.run_command = run
        result = self.app.finalize_downloaded_video_file(str(source), self.root / 'final', 'id')
        self.assertEqual([item[0] for item in operations], ['mp4_output_remux', 'mp4_output_transcode'])
        _, first_path, first_stat = operations[0]
        _, second_path, second_stat = operations[1]
        self.assertEqual(first_path, second_path)
        self.assertEqual(first_stat.st_ino, second_stat.st_ino)
        self.assertEqual(first_stat.st_size, second_stat.st_size)
        self.assertEqual(first_stat.st_mtime_ns, second_stat.st_mtime_ns)
        self.assertEqual(result, str(self.root / 'final' / 'original.mp4'))
        self.assertEqual(Path(result).read_bytes(), transcode.read_bytes())
        self.assertEqual(self.app.get_video_dimensions(result), (96, 64))
        self.assertFalse(source.exists())

    @staticmethod
    def _make_mp4(path, dimensions):
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=size={dimensions}:rate=10',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '2',
            '-c:v', 'mpeg4', '-c:a', 'aac', str(path),
        ], check=True, timeout=20)

    def test_truncated_mp4_is_rejected_before_publication(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        source = self.root / 'full.mp4'
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=1920x1080:rate=8:duration=4', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=4', '-c:v', 'mpeg4',
            '-c:a', 'aac', '-movflags', '+faststart', str(source),
        ], check=True, timeout=30)
        full = source.read_bytes()
        self.assertTrue(self.app.validate_downloaded_video(str(source), 'url')[0])

        for length in (len(full) * 55 // 100, len(full) - 16):
            with self.subTest(length=length):
                damaged = self.media('job/damaged.mp4')
                prefix = full[:length]
                damaged.write_bytes(prefix)
                self.assertAlmostEqual(self.app.get_media_duration(str(damaged)), 4, delta=.1)
                self.assertFalse(self.app.validate_downloaded_video(str(damaged), 'url')[0])
                self.assertIsNone(self.app.finalize_downloaded_video_file(str(damaged), self.root / 'final', 'id'))
                self.assertEqual(damaged.read_bytes(), prefix)
                self.assertFalse((self.root / 'final' / damaged.name).exists())

    def test_readable_but_corrupt_h264_packet_is_not_published(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        path = self.root / 'corrupt.mp4'
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=96x64:rate=8:duration=3', '-c:v', 'libx264',
            '-preset', 'ultrafast', str(path),
        ], check=True, timeout=20)
        packets = json.loads(subprocess.check_output([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_packets',
            '-show_entries', 'packet=pos,size', '-of', 'json', str(path),
        ], timeout=20))['packets']
        packet = packets[16]
        with path.open('r+b') as video:
            # Preserve the length prefix and slice header that the demuxer reads.
            video.seek(int(packet['pos']) + 32)
            video.write(b'\0' * (int(packet['size']) - 32))
        original_bytes = path.read_bytes()
        stream_copy = subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-err_detect', 'explode',
            '-i', str(path), '-map', '0:V:0', '-c', 'copy', '-f', 'null', '-',
        ], capture_output=True, timeout=20)
        self.assertEqual((stream_copy.returncode, stream_copy.stderr), (0, b''))
        self.assertFalse(self.app.validate_downloaded_video(str(path), 'url')[0])
        self.assertIsNone(self.app.finalize_downloaded_video_file(str(path), self.root / 'final', 'id'))
        self.assertEqual(path.read_bytes(), original_bytes)

    def test_mp4_checks_secondary_real_video_even_when_primary_is_acceptable(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        path = self.root / 'secondary.mp4'
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=96x64:rate=8:duration=2', '-f', 'lavfi', '-i',
            'color=size=64x1200:rate=8:duration=2', '-map', '0:v', '-map', '1:v',
            '-c:v', 'mpeg4', '-disposition:v:0', '0', '-disposition:v:1', 'default', str(path),
        ], check=True, timeout=20)
        original_bytes = path.read_bytes()
        self.assertEqual(self.app.get_video_dimensions(str(path)), (96, 64))
        self.assertFalse(self.app.validate_downloaded_video(str(path), 'url')[0])
        self.assertIsNone(self.app.finalize_downloaded_video_file(str(path), self.root / 'final', 'id'))
        self.assertEqual(path.read_bytes(), original_bytes)

    def test_mp4_ignores_large_attached_cover(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        original = self.root / 'original.mp4'
        self._make_mp4(original, '96x64')
        cover = self.root / 'cover.png'
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            'color=size=64x1200', '-frames:v', '1', str(cover),
        ], check=True, timeout=20)
        path = self.root / 'with cover.mp4'
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-i', str(original), '-i', str(cover),
            '-map', '0', '-map', '1:v', '-c', 'copy', '-disposition:v:1', 'attached_pic', str(path),
        ], check=True, timeout=20)
        original_bytes = path.read_bytes()
        self.assertTrue(self.app.validate_downloaded_video(str(path), 'url')[0])
        final = self.app.finalize_downloaded_video_file(str(path), self.root / 'final', 'id')
        self.assertEqual(Path(final).read_bytes(), original_bytes)

    def test_real_remux_keeps_audio_tracks_in_order_to_the_end(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        source = self.root / 'два тона.mp4'
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:size=64x64:rate=10',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
            '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
            '-t', '2', '-map', '0:v', '-map', '1:a', '-map', '2:a',
            '-c:v', 'mpeg4', '-c:a', 'aac', '-f', 'matroska', str(source),
        ], check=True, timeout=20)
        operations = []

        def run(command, timeout, operation, _context):
            if operation != 'check_video_decode':
                operations.append(operation)
            return self._run_ffmpeg(command, timeout, operation, _context)

        self.app.run_command = run
        url = 'https://example.invalid/video'
        real_run = subprocess.run
        commands = []

        def count_probes(command, *args, **kwargs):
            if command[0] == 'ffprobe':
                commands.append(command)
            return real_run(command, *args, **kwargs)

        with patch('src.downloads.media_probe.subprocess.run', side_effect=count_probes):
            self.assertTrue(self.app.validate_downloaded_video(str(source), url)[0])
            self.assertEqual(self.app.get_video_dimensions(str(source)), (64, 64))
            result = self.app.finalize_downloaded_video_file(str(source), self.root / 'final', 'id')
            self.assertEqual(result, str(self.root / 'final' / 'два тона.mp4'))
            self.app._log_video_info(result)
            self.assertTrue(self.app.validate_downloaded_video(result, url)[0])
            self.assertEqual(self.app.get_video_dimensions(result), (64, 64))
            self.assertAlmostEqual(self.app.get_media_duration(result), 2, delta=.1)
        self.assertEqual(operations, ['mp4_output_remux'])
        self.assertFalse(source.exists())
        actual_container = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries', 'format=format_name',
            '-of', 'default=nw=1:nk=1', result,
        ], capture_output=True, text=True, check=True, timeout=20).stdout.strip()
        self.assertIn('mp4', actual_container.split(','))
        from array import array
        for track, expected_frequency in enumerate((440, 880)):
            decoded = subprocess.run([
                'ffmpeg', '-v', 'error', '-ss', '1.4', '-i', result, '-map', f'0:a:{track}',
                '-t', '0.5', '-f', 's16le', '-ar', '8000', 'pipe:1',
            ], capture_output=True, check=True, timeout=20).stdout
            samples = array('h', decoded)
            self.assertEqual(len(samples), 4000)
            crossings = sum(before <= 0 < after for before, after in zip(samples, samples[1:]))
            self.assertAlmostEqual(crossings * 2, expected_frequency, delta=5)
        self.assertEqual(len(commands), 2 if os.name == 'nt' else 3)
        self.assertEqual(commands[0][-1], str(source))
        self.assertTrue(Path(commands[1][-1]).name.startswith('.zvd-normalize-'))

    def test_download_duration_must_be_finite(self):
        output = self.media('movie.mp4')
        self.app.has_video_stream = Mock(return_value=True)
        self.app.has_audio_stream = Mock(return_value=True)
        self.app.format_duration = Mock(return_value='unknown')
        for duration in (float('nan'), float('inf')):
            self.app.get_media_duration = Mock(return_value=duration)
            self.assertFalse(self.app.validate_downloaded_video(str(output), 'url')[0])
