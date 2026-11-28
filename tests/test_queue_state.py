import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from src.app.profile_lock import hold_download_profile
from src.application import VideoDownloader
from src.core.errors import CommandCancelledError
from src.downloads.queue_state import read_batch


class QueueRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Восстановление очереди ")
        self.addCleanup(temp.cleanup)
        self.root_path = Path(temp.name)
        self.destination = self.root_path / "Папка видео"
        self.destination.mkdir()
        # DownloadManagerMixin.start_download stores a resolved output folder.
        self.destination = self.destination.resolve(strict=True)
        self.urls = [f"https://example.invalid/video/{index}" for index in range(89)]

    def app(self, text=""):
        app = VideoDownloader.__new__(VideoDownloader)
        app.download_archive_dir = self.root_path / "История скачиваний"
        app.download_links_dir = app.download_archive_dir / "Ссылки на скачивания"
        app.record_problem = Mock()
        app.root = Mock()
        app.url_text = Mock()
        app.url_text.get.return_value = text
        app.log = Mock()
        app.is_downloading = False
        app.is_url_downloaded = Mock(return_value=False)
        app.save_path = Mock()
        app.start_download = Mock()
        app.initialize_queue_recovery()
        return app

    def new_batch(self, app):
        app.begin_download_batch(
            [*self.urls, self.urls[0], "черновик"], self.urls,
            str(self.destination), datetime.now().astimezone(),
        )
        return app._batch_path

    def test_draft_is_recovered_without_shutdown(self):
        text = "\n".join(self.urls)
        first = self.app(text)
        first._queue_text_modified(None)
        self.assertEqual(first.draft_queue_path.read_text(encoding="utf-8"), text)
        reopened = self.app()
        reopened.url_text.insert.assert_called_once_with("1.0", text)

    def test_full_archive_and_restore_skip_completed_links(self):
        first = self.app()
        archive = self.new_batch(first)
        legacy = read_batch(archive)
        legacy.pop("respect_batch_urls", None)
        first._atomic_write_json_file(archive, legacy)
        first._batch = legacy
        with patch.object(first, "problem_logging_enabled", False, create=True):
            first._change_active_batch(url=self.urls[62])
        self.assertEqual(read_batch(archive)["entered"], [*self.urls, self.urls[0], "черновик"])
        reopened = self.app()
        reopened.is_url_downloaded.side_effect = lambda url: url == self.urls[65]
        reopened.resume_interrupted_download()
        reopened.save_path.set.assert_called_once_with(str(self.destination))
        remaining = [url for url in self.urls if url not in {self.urls[62], self.urls[65]}]
        reopened.start_download.assert_called_once_with(resume_urls=remaining)
        self.assertEqual(reopened._batch_path, archive)

    def test_interrupted_explicit_download_uses_batch_links_over_new_history(self):
        first = self.app()
        archive = self.new_batch(first)
        reopened = self.app()
        reopened.settings = {"skip_downloaded": True}
        reopened.is_url_downloaded.return_value = True

        reopened.resume_interrupted_download()

        reopened.start_download.assert_called_once_with(resume_urls=self.urls)
        self.assertEqual(read_batch(archive)["status"], "active")

    def test_completed_updates_serialize_across_workers(self):
        app = self.app()
        archive = self.new_batch(app)
        workers = [threading.Thread(target=app._change_active_batch, kwargs={"url": url})
                   for url in self.urls[:12]]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(set(read_batch(archive)["downloaded"]), set(self.urls[:12]))

    def test_explicit_pause_retains_queue_without_autostart(self):
        first = self.app()
        archive = self.new_batch(first)
        self.assertTrue(first.confirm_queue_pause())
        first._change_active_batch(url=self.urls[0])
        first._change_active_batch(status="completed")
        self.assertEqual(read_batch(archive)["status"], "paused")
        reopened = self.app()
        reopened.resume_interrupted_download()
        reopened.start_download.assert_not_called()

    def test_manual_continue_reopens_paused_archive_and_reuses_published_video(self):
        first = self.app()
        url, pending = self.urls[:2]
        first.begin_download_batch([url, pending], [url, pending], str(self.destination),
                                   datetime.now().astimezone(), audio_options={"audio_quality": "256k"})
        originals = first._batch_path
        video_dir = self.destination / "Видео"
        video_dir.mkdir()
        final = video_dir / "first.mp4"
        final.write_bytes(b"published MP4 content")
        first.prepare_media_receipt("video_files", url, final, final)
        first._change_active_batch(url=url)
        self.assertTrue(first.confirm_queue_pause())
        legacy = read_batch(originals)
        legacy.pop("respect_batch_urls", None)
        first._atomic_write_json_file(originals, legacy)

        reopened = self.app()
        reopened.is_url_downloaded.return_value = True
        reopened.resume_interrupted_download()
        reopened.start_download.assert_not_called()
        reopened.resume_paused_download()
        self.assertEqual(reopened._batch_path, originals)
        reopened.start_download.assert_called_once_with(resume_urls=[pending])
        self.assertEqual(reopened.batch_existing_videos(), {url: str(final)})
        self.assertEqual(read_batch(originals)["status"], "paused")

        self.assertTrue(reopened.clear_queue_draft())
        reopened.start_download.reset_mock()
        reopened.resume_paused_download()
        reopened.start_download.assert_not_called()

    def test_cancel_stops_workers_even_when_pause_cannot_be_saved(self):
        app = self.app()
        archive = self.new_batch(app)
        app.is_downloading = True
        app.cancel_flag = threading.Event()

        def terminate():
            self.assertTrue(app.cancel_flag.is_set())
            return 1

        def show_error(*args):
            app.terminate_active_processes.assert_called_once_with()

        app.terminate_active_processes = Mock(side_effect=terminate)
        with (patch.object(app, "_replace_atomic_temp_file", side_effect=PermissionError),
              patch("src.downloads.queue_state.messagebox.showerror", side_effect=show_error) as error):
            app.cancel_download()

        self.assertTrue(app.cancel_flag.is_set())
        app.terminate_active_processes.assert_called_once_with()
        error.assert_called_once()
        self.assertIn("снова", error.call_args.args[1])
        self.assertEqual(read_batch(archive)["status"], "active")

    def test_removed_links_stay_in_archive(self):
        app = self.app("\n".join(self.urls[:3]))
        self.assertTrue(app.save_queue_draft(edited=True))
        app.url_text.get.return_value = self.urls[0]
        self.assertTrue(app.save_queue_draft(edited=True))
        archived, = app.download_links_dir.glob("links_*.txt")
        self.assertEqual(archived.read_text(encoding="utf-8").splitlines()[-2:], self.urls[1:3])

    def test_clear_queue_empties_dashboard_and_draft_and_pauses_auto_resume(self):
        text = "\n".join(self.urls[:3])
        app = self.app(text)
        batch_path = self.new_batch(app)
        app._dashboard_session = object()
        app.main_progress = {}
        app.progress_percent = Mock()
        app.refresh_download_dashboard = Mock()
        history = app.download_archive_dir / "История ссылок" / "downloaded_test.txt"
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text(self.urls[0], encoding="utf-8")
        app.downloaded_url_hashes = {"saved hash"}
        app.downloaded_video_ids = {"saved id"}

        with patch('src.ui.layout.messagebox.askyesno', return_value=True):
            app._studio_confirm_clear_queue()

        app.url_text.delete.assert_called_once_with("1.0", "end")
        self.assertIsNone(app._dashboard_session)
        app.refresh_download_dashboard.assert_called_once()
        self.assertEqual(app.draft_queue_path.read_text(encoding="utf-8"), "")
        self.assertEqual(read_batch(batch_path)["status"], "paused")
        self.assertEqual(history.read_text(encoding="utf-8"), self.urls[0])
        self.assertEqual(app.downloaded_url_hashes, {"saved hash"})
        self.assertEqual(app.downloaded_video_ids, {"saved id"})
        archived, = app.download_links_dir.glob("links_*.txt")
        self.assertEqual(archived.read_text(encoding="utf-8").splitlines()[-3:], self.urls[:3])
        reopened = self.app()
        reopened.resume_interrupted_download()
        reopened.start_download.assert_not_called()

    def test_clear_queue_keeps_visible_queue_when_draft_write_fails(self):
        app = self.app(self.urls[0])
        self.assertTrue(app.save_queue_draft(edited=True))
        archive = self.new_batch(app)
        previous_batch = archive.read_bytes()
        replace = app._replace_atomic_temp_file

        def reject_draft_only(temp_path, path):
            if path == app.draft_queue_path:
                raise PermissionError("draft is locked")
            replace(temp_path, path)

        with (patch('src.ui.layout.messagebox.askyesno', return_value=True),
              patch('src.ui.layout.messagebox.showerror') as error,
              patch.object(app, '_replace_atomic_temp_file', side_effect=reject_draft_only)):
            app._studio_confirm_clear_queue()
        app.url_text.delete.assert_not_called()
        error.assert_called_once()
        self.assertEqual(app.draft_queue_path.read_text(encoding="utf-8"), self.urls[0])
        self.assertEqual(archive.read_bytes(), previous_batch)
        self.assertEqual(app._batch["status"], "active")

        reopened = self.app(self.urls[0])
        self.assertTrue(reopened.clear_queue_draft())
        self.assertEqual(read_batch(archive)["status"], "paused")
        self.assertEqual(app.draft_queue_path.read_text(encoding="utf-8"), "")
        reopened.url_text.delete.assert_called_once_with("1.0", "end")

    def test_restore_button_adds_only_missing_unfinished_links(self):
        first = self.app()
        self.new_batch(first)
        first._change_active_batch(url=self.urls[0], status="paused")
        reopened = self.app(self.urls[1])
        reopened.add_urls_with_check = Mock()
        reopened.restore_unfinished_links()
        reopened.add_urls_with_check.assert_called_once_with(self.urls[2:], check_history=False)

    def test_restore_button_keeps_saved_link_even_when_history_filter_is_enabled(self):
        first = self.app()
        url = self.urls[0]
        first.begin_download_batch([url], [url], str(self.destination), datetime.now().astimezone())
        self.assertTrue(first.confirm_queue_pause())
        reopened = self.app()
        reopened.settings = {"skip_downloaded": True}
        reopened.is_url_downloaded.return_value = True

        with patch("src.downloads.urls.messagebox.showwarning") as warning:
            reopened.restore_unfinished_links()
            warning.assert_not_called()
            reopened.url_text.insert.assert_called_once_with("end", url)
            reopened.start_download.assert_not_called()

            reopened.url_text.insert.reset_mock()
            self.assertEqual(reopened.add_urls_with_check([url]), 0)
            warning.assert_called_once()
            reopened.url_text.insert.assert_not_called()

    def test_published_video_restarts_at_conversion_without_redownloading(self):
        first = self.app()
        video_dir = self.destination / "Видео"
        video_dir.mkdir()
        source = video_dir / "staged.mp4"
        source.write_bytes(b"approved synthetic video")
        final = video_dir / "готово.mp4"
        url = self.urls[0]
        first.begin_download_batch([url], [url], str(self.destination), datetime.now().astimezone())
        first.prepare_media_receipt("video_files", url, source, final)
        self.assertEqual(first.batch_existing_videos(), {})
        source.rename(final)
        self.assertEqual(read_batch(first._batch_path)["downloaded"], [])
        reopened = self.app()
        reopened.resume_interrupted_download()
        reopened.start_download.assert_called_once_with(resume_urls=[])
        self.assertEqual(reopened.batch_existing_videos(), {url: str(final)})

    def test_retry_rejects_missing_or_replaced_source_receipt(self):
        app = self.app()
        batch_path = self.new_batch(app)
        url, missing_url = self.urls[:2]
        video = self.destination / "Видео" / "готово.mp4"
        video.parent.mkdir()
        video.write_bytes(b"verified original")
        app.prepare_media_receipt("video_files", url, video, video)
        self.assertFalse(app.can_retry_queue_audio(url))  # An active batch is reserved for resume.
        app._change_active_batch(status="audio_pending")
        archive = batch_path.read_bytes()

        self.assertFalse(app.can_retry_queue_audio(missing_url))
        self.assertFalse(app.retry_queue_audio(missing_url))
        self.assertTrue(app.can_retry_queue_audio(url))
        app.is_downloading = True
        self.assertFalse(app.can_retry_queue_audio(url))
        app.is_downloading = False
        video.write_bytes(b"replacement with a different fingerprint")
        self.assertFalse(app.retry_queue_audio(url))
        video.unlink()
        self.assertFalse(app.retry_queue_audio(url))
        self.assertEqual(batch_path.read_bytes(), archive)
        app.start_download.assert_not_called()

    def test_replaced_audio_does_not_match_prepared_file(self):
        first = self.app()
        self.new_batch(first)
        directory = self.destination / "Аудио"
        directory.mkdir()
        video = Path("готово.mp4")
        temp = directory / "staged.mp3"
        temp.write_bytes(b"verified synthetic audio")
        final = directory / "готово.mp3"
        first.prepare_media_receipt("audio_files", video.name, temp, final)
        self.assertIsNone(first.existing_batch_audio(video, directory))
        temp.rename(final)
        reopened = self.app()
        reopened.resume_interrupted_download()
        self.assertEqual(reopened.existing_batch_audio(video, directory), str(final))
        final.write_bytes(b"different content")
        self.assertIsNone(reopened.existing_batch_audio(video, directory))

    def test_same_name_replacement_video_invalidates_previous_audio_receipt(self):
        first = self.app()
        archive = self.new_batch(first)
        video_dir = self.destination / "Видео"
        audio_dir = self.destination / "Аудио"
        video_dir.mkdir()
        audio_dir.mkdir()
        video = video_dir / "готово.mp4"
        video.write_bytes(b"original video")
        audio = audio_dir / "готово.mp3"
        audio.write_bytes(b"audio of original video")
        url = self.urls[0]
        first.prepare_media_receipt("video_files", url, video, video)
        first.prepare_media_receipt("audio_files", video.name, audio, audio)
        self.assertEqual(first.existing_batch_audio(video, audio_dir), str(audio))

        source = video_dir / "новое.mp4"
        source.write_bytes(b"different video payload")
        first.prepare_media_receipt("video_files", url, source, video)
        source.replace(video)

        reopened = self.app()
        reopened._batch_path, reopened._batch = archive, read_batch(archive)
        self.assertEqual(reopened.batch_existing_videos(), {url: str(video)})
        self.assertIsNone(reopened.existing_batch_audio(video, audio_dir))
        self.assertEqual(audio.read_bytes(), b"audio of original video")

    def test_merge_receipt_reuses_complete_set_or_finishes_staged_set(self):
        app = self.app()
        archive = self.new_batch(app)
        audio = self.destination / "Аудио" / "вход.mp3"
        audio.parent.mkdir()
        audio.write_bytes(b"retained input")
        split_dir = self.destination / "Разбивка аудио"
        stage = split_dir / ".split_fixture"
        stage.mkdir(parents=True)
        outputs = []
        for number in (1, 2):
            source = stage / f"part_{number}.mp3"
            source.write_bytes(f"part {number}".encode())
            outputs.append((source, split_dir / f"часть_{number}.mp3"))
        app.prepare_batch_merge_receipt([str(audio)], outputs)
        outputs[0][0].rename(outputs[0][1])

        reopened = self.app()
        reopened._batch_path, reopened._batch = archive, read_batch(archive)
        reopened.file_lock = threading.Lock()
        reopened.cancel_flag = threading.Event()
        reopened.cancel_flag.set()
        with self.assertRaises(CommandCancelledError):
            reopened.has_batch_merge_result([str(audio)])
        self.assertFalse(outputs[1][1].exists())
        reopened.cancel_flag.clear()
        self.assertTrue(reopened.has_batch_merge_result([str(audio)]))
        self.assertTrue(reopened.has_batch_merge_result([str(audio)]))
        self.assertCountEqual([path.name for path in split_dir.glob("*.mp3")],
                              [path.name for _, path in outputs])
        self.assertEqual(outputs[1][1].read_bytes(), b"part 2")
        audio.write_bytes(b"replacement input")
        self.assertFalse(reopened.has_batch_merge_result([str(audio)]))

    def test_original_folder_missing_preserves_batch(self):
        first = self.app()
        archive = self.new_batch(first)
        self.destination.rmdir()
        reopened = self.app()
        reopened.resume_interrupted_download()
        reopened.start_download.assert_not_called()
        reopened.save_path.set.assert_not_called()
        self.assertEqual(read_batch(archive)["status"], "active")
        self.assertEqual(read_batch(archive)["urls"], self.urls)

    def test_missing_output_drive_preserves_audio_only_resume_and_options(self):
        first = self.app()
        url = self.urls[0]
        saved_audio_options = {"audio_quality": "192", "merge_audio": True, "split_hours": 3}
        first.begin_download_batch(
            [url], [url], str(self.destination), datetime.now().astimezone(),
            audio_options=saved_audio_options,
        )
        video_dir = self.destination / "Видео"
        video_dir.mkdir()
        staged_video = video_dir / "staged.mp4"
        staged_video.write_bytes(b"published video fixture")
        final_video = video_dir / "готово.mp4"
        first.prepare_media_receipt("video_files", url, staged_video, final_video)
        staged_video.rename(final_video)
        first._change_active_batch(url=url)
        archive = first._batch_path
        original_batch = archive.read_bytes()

        offline = self.destination.rename(self.root_path / "Недоступный носитель")
        with_drive_missing = self.app()
        with_drive_missing.resume_interrupted_download()
        with_drive_missing.start_download.assert_not_called()
        self.assertEqual(archive.read_bytes(), original_batch)

        offline.rename(self.destination)
        self.assertTrue(first.confirm_queue_pause())
        legacy = read_batch(archive)
        legacy.pop("respect_batch_urls", None)
        first._atomic_write_json_file(archive, legacy)
        reopened = self.app(url)
        reopened.start_download = Mock(wraps=VideoDownloader.start_download.__get__(reopened))
        reopened.save_path.get.return_value = str(self.destination)
        reopened.missing_deps = ["yt-dlp"]
        reopened.settings = {
            "audio_quality": "320", "merge_audio": False, "split_hours": 0,
            "download_subtitles": False, "proxy_enabled": False, "proxy_url": "",
        }
        reopened.concurrent_var = Mock()
        reopened.concurrent_var.get.return_value = 1
        reopened.cancel_flag = threading.Event()
        reopened.youtube_strategy_state_lock = threading.Lock()
        for name in (
            "save_current_settings", "set_save_path_controls_enabled", "total_files",
            "completed_files", "download_btn", "cancel_btn", "main_progress_frame",
            "update_main_progress", "write_problem_session_summary",
        ):
            setattr(reopened, name, Mock())
        reopened.main_progress = {}
        with patch("src.downloads.manager.threading.Thread") as worker:
            reopened.resume_interrupted_download()
            reopened.start_download.assert_not_called()
            reopened.resume_paused_download()

        self.assertEqual(read_batch(archive)["status"], "active")
        self.assertTrue(read_batch(archive)["respect_batch_urls"])
        reopened.start_download.assert_called_once_with(resume_urls=[])
        worker.assert_called_once_with(target=reopened.download_manager, args=([],), daemon=True)
        worker.return_value.start.assert_called_once_with()
        self.assertEqual(
            {key: reopened.download_settings_snapshot[key] for key in saved_audio_options},
            {**saved_audio_options, "audio_quality": "192k"},
        )
        self.assertEqual(reopened.batch_existing_videos(), {url: str(final_video)})
        self.assertFalse(reopened.download_settings_snapshot["skip_downloaded"])

    def test_failed_replace_leaves_previous_draft_and_batch_on_disk(self):
        app = self.app(self.urls[0])
        self.assertTrue(app.save_queue_draft(edited=True))
        archive = self.new_batch(app)
        app.url_text.get.return_value = self.urls[1]
        with patch.object(app, "_replace_atomic_temp_file", side_effect=PermissionError):
            self.assertFalse(app.save_queue_draft(edited=True))
            self.assertFalse(app._change_active_batch(url=self.urls[0]))
        self.assertEqual(app.draft_queue_path.read_text(encoding="utf-8"), self.urls[0])
        self.assertEqual(read_batch(archive)["downloaded"], [])
        self.assertEqual(list(app.download_archive_dir.rglob("*.tmp")), [])

    def test_close_during_download_keeps_recovery_active(self):
        app = self.app()
        archive = self.new_batch(app)
        app.is_downloading = True
        app.cancel_flag = threading.Event()
        app._download_manager_thread = Mock()
        app.save_download_links_session = Mock()
        app.save_current_settings = Mock()
        app.finalize_problem_diagnostics = Mock()
        with patch("src.ui.window.messagebox.askyesno", return_value=True):
            app.on_closing()
            app.on_closing()
        self.assertTrue(app.cancel_flag.is_set())
        app.root.destroy.assert_not_called()
        app.root.after.assert_called_once()
        _, callback, *args = app.root.after.call_args.args
        app._download_manager_thread.is_alive.return_value = False
        callback(*args)
        app.root.destroy.assert_called_once()
        self.assertEqual(read_batch(archive)["status"], "active")

    def test_profile_lock_prevents_second_writer_and_releases_on_exit(self):
        script = (
            "import sys; from pathlib import Path; "
            "from src.app.profile_lock import hold_download_profile, ProfileInUseError\n"
            "try:\n with hold_download_profile(Path(sys.argv[1])): pass\n"
            "except ProfileInUseError: sys.exit(7)\n"
        )
        command = [sys.executable, "-B", "-c", script, str(self.root_path)]
        with hold_download_profile(self.root_path):
            blocked = subprocess.run(command, cwd=Path(__file__).resolve().parent.parent, timeout=5)
        released = subprocess.run(command, cwd=Path(__file__).resolve().parent.parent, timeout=5)
        self.assertEqual((blocked.returncode, released.returncode), (7, 0))
