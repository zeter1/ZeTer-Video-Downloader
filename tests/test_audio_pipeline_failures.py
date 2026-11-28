import tempfile
import threading
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from src.application import VideoDownloader
from src.core.errors import CommandCancelledError
from src.downloads.session_view import DownloadSessionView


class AudioPipelineFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="audio ' тест ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'input.mp3'
        self.source.write_bytes(b'a'*2000)
        app = self.app = VideoDownloader.__new__(VideoDownloader)
        app.log = Mock()
        app.record_problem = Mock()
        app.file_logger = Mock()
        app.file_lock = threading.Lock()
        app.cancel_flag = threading.Event()
        app.audio_quality = SimpleNamespace(get=lambda: '320k')
        app.split_hours = SimpleNamespace(get=lambda: 0)
        app.get_audio_duration = Mock(return_value=2.)
        app.get_total_audio_duration = Mock(return_value=2.)
        app.get_decoded_audio_duration = Mock(return_value=2.)
        app.get_audio_sample_rate = Mock(return_value=48000)
        app.format_duration = str
        app.run_command = Mock(return_value=subprocess.CompletedProcess([],1,'','failure'))

    def test_merge_failure_is_not_swallowed(self):
        with self.assertRaises(RuntimeError):
            self.app.merge_audio_files([str(self.source)],str(self.root),'output')
        self.assertTrue(self.source.exists())
        command = self.app.run_command.call_args.args[0]
        self.assertEqual(command[command.index('-protocol_whitelist') + 1], 'file')
        playlist = Path(command[command.index('-i') + 1])
        input_path = self.source.as_posix().replace("'", r"'\''")
        self.assertEqual(playlist.read_text(encoding='utf-8').splitlines(),
                         ['ffconcat version 1.0', f"file '{input_path}'"])
        self.assertEqual(list(playlist.parent.iterdir()), [playlist])

    def test_split_failure_preserves_source_and_old_files(self):
        self.app._dashboard_session = Mock()
        old=self.root/'part_000.mp3'
        old.write_bytes(b'old')
        with self.assertRaises(RuntimeError):
            self.app.split_audio_file(self.source,self.root,'output',1/3600)
        self.app._dashboard_session.phase.assert_called_once_with("Разбивка аудио на части")
        self.assertEqual(self.source.read_bytes(),b'a'*2000)
        self.assertEqual(old.read_bytes(),b'old')

    def test_zero_exit_without_parts_is_failure(self):
        self.app.run_command.return_value.returncode=0
        self.app.run_command.return_value.stderr=''
        with self.assertRaises(RuntimeError):
            self.app.split_audio_file(self.source,self.root,'output',1/3600)
        self.assertTrue(self.source.exists())

    def test_merge_rejects_missing_input(self):
        with self.assertRaises(FileNotFoundError):
            self.app.merge_audio_files([str(self.source),str(self.root/'missing.mp3')],str(self.root),'output')
        self.app.run_command.assert_not_called()

    def test_cancel_propagates(self):
        self.app.run_command.side_effect=CommandCancelledError('cancelled')
        with self.assertRaises(CommandCancelledError):
            self.app.merge_audio_files([str(self.source)],str(self.root),'output')

    def test_cancel_during_merge_receipt_preserves_unpublished_mp3(self):
        app = self.app
        inputs = [str(self.source)]

        def run(cmd, *_):
            Path(cmd[-1]).write_bytes(b'complete merged audio')
            return subprocess.CompletedProcess(cmd, 0, '', '')

        def cancel_during_receipt(audio_files, publications):
            self.assertEqual(audio_files, inputs)
            self.assertEqual(len(publications), 1)
            app.cancel_flag.set()

        app.run_command.side_effect = run
        app.prepare_batch_merge_receipt = Mock(side_effect=cancel_during_receipt)
        with self.assertRaises(CommandCancelledError):
            app.merge_audio_files(inputs, str(self.root), 'output')
        output_dir = self.root / 'Разбивка аудио'
        staged, = output_dir.glob('.merge_*/merged.mp3')
        self.assertEqual(staged.read_bytes(), b'complete merged audio')
        self.assertEqual(self.source.read_bytes(), b'a' * 2000)
        self.assertFalse(list(output_dir.glob('output_*.mp3')))

    def test_cancel_during_split_receipt_preserves_every_staged_part(self):
        app = self.app
        inputs = [str(self.source)]
        app.get_decoded_audio_duration.side_effect = lambda p: 2. if p == self.source else 1.

        def run(cmd, *_):
            for part in ('part_000.mp3', 'part_001.mp3'):
                Path(cmd[-1]).with_name(part).write_bytes(part.encode('ascii'))
            return subprocess.CompletedProcess(cmd, 0, '', '')

        def cancel_during_receipt(audio_files, publications):
            self.assertEqual(audio_files, inputs)
            self.assertEqual(len(publications), 2)
            app.cancel_flag.set()

        app.run_command.side_effect = run
        app.prepare_batch_merge_receipt = Mock(side_effect=cancel_during_receipt)
        with self.assertRaises(CommandCancelledError):
            app.split_audio_file(self.source, self.root, 'output', 1 / 3600, audio_files=inputs)
        staged = sorted(self.root.glob('.split_*/part_*.mp3'))
        self.assertEqual([part.read_bytes() for part in staged], [b'part_000.mp3', b'part_001.mp3'])
        self.assertEqual(self.source.read_bytes(), b'a' * 2000)
        self.assertFalse(list(self.root.glob('output_*.mp3')))

    def test_merge_duration_mismatch_is_failure(self):
        def run(cmd,*args):
            Path(cmd[-1]).write_bytes(b'a'*2000)
            return subprocess.CompletedProcess(cmd,0,'','')
        self.app.run_command.side_effect=run
        self.app.get_audio_duration.side_effect=lambda p: 2. if Path(p)==self.source else 20.
        with self.assertRaises(RuntimeError):
            self.app.merge_audio_files([str(self.source)],str(self.root),'output')

    def test_existing_output_is_preserved(self):
        out=self.root/'Разбивка аудио'
        out.mkdir()
        old=out/'output_Объединенное.mp3'
        old.write_bytes(b'old data')
        def run(cmd,*args):
            Path(cmd[-1]).write_bytes(b'new data'*300)
            return subprocess.CompletedProcess(cmd,0,'','')
        self.app.run_command.side_effect=run
        self.app.merge_audio_files([str(self.source)],str(self.root),'output')
        self.assertEqual(old.read_bytes(),b'old data')
        self.assertTrue((out/'output_Объединенное (2).mp3').exists())

    def test_conversion_does_not_reuse_unrelated_same_name_mp3(self):
        video=self.root/'video.mkv'
        video.write_bytes(b'video')
        old=self.root/'video.mp3'
        old.write_bytes(b'unrelated audio')
        self.app.get_media_duration=Mock(return_value=2.)
        self.app.probe_audio_stream_presence=Mock(return_value=True)
        self.app.validate_video_packets=Mock(return_value=True)
        self.app.validate_converted_audio=Mock(return_value=(True,'ok',2.,2.))
        def run(cmd,*args):
            Path(cmd[-1]).write_bytes(b'new audio')
            return subprocess.CompletedProcess(cmd,0,'','')
        self.app.run_command.side_effect=run
        result=self.app.convert_videos_to_audio([str(video)],self.root)
        self.assertEqual(result,[str(self.root/'video (2).mp3')])
        self.assertEqual(old.read_bytes(),b'unrelated audio')

    def test_unavailable_audio_probe_keeps_a_different_status_from_missing_audio(self):
        video = self.root / "video.mp4"
        video.write_bytes(b"video")
        app = self.app
        app.get_media_duration = Mock(return_value=2.0)
        app.validate_video_packets = Mock(return_value=True)
        app.save_failed_conversions_session = Mock(return_value=None)
        app.manual_processing_dir = self.root
        for presence, expected in ((None, "audio_error"), (False, "audio_missing")):
            with self.subTest(presence=presence):
                app.probe_audio_stream_presence = Mock(return_value=presence)
                app._dashboard_session = session = DownloadSessionView(["url"])
                session.update("url", "video", video)
                self.assertEqual(app.convert_videos_to_audio([str(video)], self.root), [])
                self.assertEqual(dict(session.snapshot()[1])["url"][0], expected)
                self.assertEqual(session.media_paths("url"), (video, None))

    def test_second_part_publish_failure_then_retry_has_one_set(self):
        from unittest.mock import patch
        self.app.get_decoded_audio_duration.side_effect=lambda p: 2. if p==self.source else 1.
        def run(cmd,*args):
            for part in ('part_000.mp3', 'part_001.mp3'):
                Path(cmd[-1]).with_name(part).write_bytes(b'part'*500)
            return subprocess.CompletedProcess(cmd,0,'','')
        self.app.run_command.side_effect=run
        rename=Path.rename
        def fail_second(path,target):
            if path.name=='part_001.mp3':
                raise PermissionError('second rename failed')
            return rename(path,target)
        with patch.object(Path,'rename',fail_second):
            with self.assertRaises(PermissionError):
                self.app.split_audio_file(self.source,self.root,'out',1/3600)
        self.assertFalse(list(self.root.glob('out_Часть_*.mp3')))
        self.app.split_audio_file(self.source,self.root,'out',1/3600)
        self.assertEqual(sorted(p.name for p in self.root.glob('out_Часть_*.mp3')),
                         ['out_Часть_001.mp3','out_Часть_002.mp3'])
        self.assertTrue(self.source.exists())

    def test_real_merge_split_tone_order_and_paths(self):
        import shutil, array
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg/ffprobe required')
        app=VideoDownloader.__new__(VideoDownloader)
        app.log=Mock(); app.record_problem=Mock(); app.file_logger=Mock()
        app.file_lock=threading.Lock(); app.cancel_flag=threading.Event()
        app.subprocess_flags=0
        app.audio_quality=SimpleNamespace(get=lambda:'320k')
        app.split_hours=SimpleNamespace(get=lambda:0)
        app.run_command=lambda cmd,timeout,*args: subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        inputs=[]
        for i,freq in enumerate((440,880)):
            path=self.root/f"тон '{i}.mp3"
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
                            f'sine=frequency={freq}:sample_rate=48000','-t','1',
                            '-c:a','libmp3lame','-b:a','320k',str(path)],check=True,timeout=20)
            inputs.append(str(path))
        app.merge_audio_files(inputs,str(self.root),'tones')
        output_dir=self.root/'Разбивка аудио'
        merged=output_dir/'tones_Объединенное.mp3'
        self.assertTrue(merged.exists())
        app.split_audio_file(merged,output_dir,'tones',1/3600)
        parts=sorted(output_dir.glob('tones_Часть_*.mp3'))
        self.assertGreaterEqual(len(parts),2)
        self.assertTrue(merged.exists())
        def frequency(path,start):
            result=subprocess.run(['ffmpeg','-v','error','-ss',str(start),'-i',str(path),
                                   '-t','0.25','-ar','48000','-ac','1','-f','s16le','pipe:1'],
                                  capture_output=True,check=True,timeout=20)
            samples=array.array('h',result.stdout)
            crossings=sum(a<=0<b for a,b in zip(samples,samples[1:]))
            return crossings/(len(samples)/48000)
        self.assertAlmostEqual(frequency(merged,.2),440,delta=12)
        self.assertAlmostEqual(frequency(merged,1.2),880,delta=12)
        self.assertAlmostEqual(frequency(parts[0],.2),440,delta=12)
        self.assertAlmostEqual(frequency(parts[1],.2),880,delta=12)

    def test_mp3_split_preserves_decoded_samples_at_cut(self):
        import array
        import shutil
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        app = self.app
        del app.get_audio_duration
        del app.get_decoded_audio_duration
        del app.get_audio_sample_rate
        app.subprocess_flags = 0
        app.run_command = lambda cmd, timeout, *_: subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout)
        source = self.root / 'пример.mp3'
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            r'aevalsrc=if(lt(t\,1)\,0.4*sin(2*PI*440*t)\,0.4*sin(2*PI*880*t)):s=48000:d=1.381',
            '-c:a', 'libmp3lame', '-b:a', '320k', str(source),
        ], check=True, timeout=20)

        def samples(path):
            raw = subprocess.check_output([
                'ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
                '-map', '0:a:0', '-f', 's16le', '-ac', '1', 'pipe:1',
            ], timeout=20)
            return array.array('h', raw)

        original = source.read_bytes()
        expected = samples(source)
        app.split_audio_file(source, self.root, 'готово', 1 / 3600)
        parts = sorted(self.root.glob('готово_Часть_*.mp3'))
        self.assertEqual(len(parts), 2)
        first, second = map(samples, parts)
        self.assertEqual(len(first), 48000)
        self.assertEqual(len(first) + len(second), len(expected))
        self.assertEqual(source.read_bytes(), original)
        for actual, reference in ((first[-4096:], expected[43904:48000]),
                                  (second[:4096], expected[48000:52096])):
            error = sum(abs(a - b) for a, b in zip(actual, reference)) / 4096
            self.assertLess(error, 500)

    def test_mp3_split_bounds_windows_command_for_long_output_paths(self):
        split_dir = self.root / ('д' * max(10, 205 - len(str(self.root))))
        self.app.get_decoded_audio_duration.side_effect = lambda p: 100 * 3600 if p == self.source else 3600.
        commands = []

        def run(cmd, *_):
            commands.append(cmd)
            for path in cmd:
                if Path(path).name.startswith('part_') and path.endswith('.mp3'):
                    Path(path).write_bytes(b'part')
            return subprocess.CompletedProcess(cmd, 0, '', '')

        self.app.run_command.side_effect = run
        self.app.split_audio_file(self.source, split_dir, 'итог', 1)
        single_command = []
        for command in commands:
            single_command.extend(command[command.index('-abort_on') + 2:])
            self.assertLess(len(subprocess.list2cmdline(command).encode('utf-16-le')) // 2 + 1, 32767)
        self.assertGreater(
            len(subprocess.list2cmdline(single_command))
            + sum(len(command[command.index('-filter_complex') + 1]) for command in commands), 32767,
        )
        self.assertGreater(len(commands), 1)
        self.assertEqual(len(list(split_dir.glob('итог_Часть_*.mp3'))), 100)

    def test_real_split_preserves_samples_between_encoder_batches(self):
        import array
        import shutil
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        app = self.app
        del app.get_decoded_audio_duration
        del app.get_audio_sample_rate
        app.subprocess_flags = 0
        app.audio_quality = SimpleNamespace(get=lambda: 'VBR-0 (лучшее)')
        operations = []

        def run(cmd, timeout, operation, _):
            operations.append(operation)
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

        app.run_command = run
        source = self.root / 'много частей.mp3'
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            r'aevalsrc=if(lt(t\,1.92)\,0.4*sin(2*PI*440*t)\,0.4*sin(2*PI*880*t)):s=48000:d=2.08',
            '-c:a', 'libmp3lame', '-q:a', '0', str(source),
        ], check=True, timeout=20)
        app.split_audio_file(source, self.root, 'готово', 0.12 / 3600)
        parts = sorted(self.root.glob('готово_Часть_*.mp3'))
        self.assertEqual(operations.count('split_audio_file'), 2)
        self.assertEqual(len(parts), 17)

        def samples(path):
            return array.array('h', subprocess.check_output([
                'ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
                '-map', '0:a:0', '-f', 's16le', 'pipe:1',
            ], timeout=20))

        cut = 92160
        expected = samples(source)[cut - 4096:cut + 4096]
        actual = samples(parts[15])[-4096:] + samples(parts[16])[:4096]
        self.assertEqual(len(actual), len(expected))
        self.assertLess(sum(abs(a - b) for a, b in zip(actual, expected)) / len(expected), 500)
