"""Status receipt contract without launching the desktop application."""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.downloads.history import DownloadHistoryMixin
from src.downloads.session_view import DownloadSessionView
from src.downloads.urls import UrlQueueMixin
from src.ui.download_dashboard import DownloadDashboardMixin, FILTERS, download_speed


class DashboardSessionTests(unittest.TestCase):
    def test_completed_video_and_failed_audio_remain_distinct(self):
        view = DownloadSessionView(["one", "two", "three"])
        with ThreadPoolExecutor(max_workers=2) as workers:
            good = workers.submit(view.update, "one", "video", "Каталог/Первый.mp4")
            failed = workers.submit(view.update, "two", "failed")
            good.result()
            failed.result()
        view.update("three", "video", "Каталог/Третий.mp4")
        view.phase("Создание MP3")
        view.audio("Каталог/Первый.mp4", "done")
        view.audio("Каталог/Третий.mp4", "audio_error")
        view.finish("failed")
        phase, rows = view.snapshot()
        self.assertEqual(phase, "Завершено с ошибками")
        self.assertEqual(dict(rows), {
            "one": ("done", "Первый"), "two": ("failed", ""), "three": ("audio_error", "Третий"),
        })
        self.assertEqual(view.result(), (1, 2))

    def test_retry_snapshot_keeps_siblings_and_counts_only_selected_video(self):
        view = DownloadSessionView(["a", "b"])
        view.update("a", "video", "a.mp4")
        view.audio("a.mp4", "audio_error", reason="Проверка MP3 не прошла")
        view.update("b", "failed")
        view.finish("failed")
        retry = view.for_audio_retry("a")
        self.assertEqual(retry.media_paths("a"), (Path("a.mp4"), None))
        self.assertIsNone(retry.result())
        retry.audio("a.mp4", "done", "a.mp3")
        retry.finish("completed")
        self.assertEqual(retry.media_paths("a"), (Path("a.mp4"), Path("a.mp3")))
        self.assertEqual(retry.audio_error("a"), "")
        self.assertEqual(retry.result(), (1, 0))
        self.assertEqual(dict(retry.snapshot()[1])["b"][0], "failed")
        self.assertEqual(dict(view.snapshot()[1])["a"][0], "audio_error")

    def test_cancellation_preserves_video_and_does_not_pollute_next_session(self):
        view = DownloadSessionView(["one", "two"], ["historical"])
        view.update("one", "video", "Видео.mp4")
        view.update("two", "downloading")
        view.finish("cancelled")
        self.assertEqual(dict(view.snapshot()[1]), {
            "one": ("video", "Видео"), "two": ("cancelled", ""), "historical": ("skipped", ""),
        })
        self.assertEqual(dict(DownloadSessionView(["two"]).snapshot()[1]), {"two": ("waiting", "")})

    def test_row_action_returns_link_without_starting_or_replacing_the_batch(self):
        url = "https://www.youtube.com/watch?v=Dsh4mqo1dVo"
        app = SimpleNamespace(get_urls_from_textbox=lambda: [], url_identity=str,
                              add_urls_with_check=Mock(), show_studio_links=Mock(), start_download=Mock())
        DownloadDashboardMixin._return_dashboard_link(app, url)
        app.add_urls_with_check.assert_called_once_with([url])
        app.start_download.assert_not_called()
        app.show_studio_links.assert_called_once()
        app.get_urls_from_textbox = lambda: [url]
        app.add_urls_with_check.reset_mock()
        DownloadDashboardMixin._return_dashboard_link(app, url)
        app.add_urls_with_check.assert_not_called()

    def test_speed_units_and_unavailable_values(self):
        self.assertEqual(download_speed("944KiB/s"), 944 * 1024)
        self.assertEqual(download_speed("1.38MiB/s"), 1.38 * 1024**2)
        self.assertEqual(download_speed("Unknown"), 0)
        self.assertEqual(download_speed("7iB/s"), 0)

    def make_dashboard(self, text):
        class Dashboard(DownloadDashboardMixin, UrlQueueMixin, DownloadHistoryMixin):
            pass

        app = Dashboard()
        app.url_text = Mock()
        app.url_text.get.return_value = text
        for name in ('_dashboard_table', '_thumbnails', '_dashboard_phase', '_dashboard_phase_action',
                     '_dashboard_clear_search', 'root', '_dashboard_hint', 'progress_percent'):
            setattr(app, name, Mock())
        app._thumbnails.collect.return_value = False
        app._dashboard_search = Mock()
        app._dashboard_search.get.return_value = ""
        app._dashboard_filter = Mock()
        app._dashboard_filter.get.return_value = "Все"
        app._dashboard_tabs = {name: Mock() for name, _ in FILTERS}
        app._place_queue_filters = Mock()
        app.can_retry_queue_audio = Mock(return_value=False)
        app._dashboard_title_results = None
        app._dashboard_archive_entries = {}
        app._dashboard_session_titles = {}
        app._dashboard_draft = app._dashboard_last = app._dashboard_query = None
        app.is_downloading = False
        app.video_list = []
        app.ytdlp_runtime_lock = Lock()
        app.ytdlp_runtime = {}
        app._draw_dashboard_speed = Mock()
        app.skip_downloaded_enabled = Mock(return_value=True)
        app.is_url_downloaded = Mock(return_value=True)
        app.should_skip_downloaded = lambda url: app.skip_downloaded_enabled() and app.is_url_downloaded(url)
        app.log = Mock()
        app.url_identity = Mock(wraps=app.url_identity)
        app._dashboard_identity = lru_cache(maxsize=2048)(app.url_identity)
        app.completed_files = Mock()
        app.total_files = Mock()
        app.completed_files.value.return_value = 0
        app.total_files.value.return_value = 1
        app.max_concurrent = 2
        app.main_progress = {}
        return app

    def test_draft_uses_same_validation_as_start_and_reuses_results_when_idle(self):
        supported = 'https://vimeo.com/123'
        unsupported = 'https://vimeo.com.example/123'
        app = self.make_dashboard(f'{supported}\n{unsupported}')
        app.refresh_download_dashboard()
        self.assertTrue(app.validate_url(supported))
        self.assertFalse(app.validate_url(unsupported))
        self.assertEqual({u: status for u, (status, _) in app._dashboard_rows.items()},
                         {supported: 'skipped', unsupported: 'invalid'})
        app.url_identity.reset_mock()
        app.is_url_downloaded.reset_mock()
        app.refresh_download_dashboard()
        app.url_identity.assert_not_called()
        app.is_url_downloaded.assert_not_called()

        app.skip_downloaded_enabled.return_value = False
        app.refresh_download_dashboard()
        self.assertEqual(app._dashboard_rows[supported][0], 'waiting')

    def test_batch_phase_updates_above_unchanged_rows_and_resets(self):
        app = self.make_dashboard("")
        url = "https://vimeo.com/123"
        app._dashboard_session = session = DownloadSessionView([url])
        app.is_downloading = True
        session.update(url, "downloading")
        app.refresh_download_dashboard()
        app._dashboard_phase.set.assert_called_with("Скачивание видео   ·   Видео обработано: 0 из 1")
        self.assertEqual(app._dashboard_table.update_rows.call_args.args[0][0].status, "Скачивание")
        app._dashboard_hint.set.assert_called_with("Файлов: 1")
        self.assertEqual(app._dashboard_phase_action.configure.call_args.kwargs["text"], "К активным")

        app.completed_files.value.return_value = 1
        session.update(url, "video", "Видео.mp4")
        session.phase("Создание MP3")
        session.audio("Видео.mp4", "audio")
        app.refresh_download_dashboard()
        app._dashboard_phase.set.assert_called_with("Создание MP3")
        self.assertEqual(app._dashboard_table.update_rows.call_args.args[0][0][2], "Создание MP3")
        app._dashboard_hint.set.assert_called_with("Файлов: 1")

        session.audio("Видео.mp4", "done")
        session.phase("Объединение аудио")
        app.refresh_download_dashboard()
        app._dashboard_phase.set.assert_called_with("Объединение аудио")
        app._dashboard_table.update_rows.reset_mock()
        session.phase("Разбивка аудио на части")
        app.refresh_download_dashboard()
        app._dashboard_phase.set.assert_called_with("Разбивка аудио на части")
        app._dashboard_table.update_rows.assert_not_called()

        app.is_downloading = False
        session.finish("failed")
        app.refresh_download_dashboard()
        app._dashboard_phase.set.assert_called_with("Завершено с ошибками   ·   Готово: 1   ·   Ошибки: 0")

        app._dashboard_session = None
        app.refresh_download_dashboard()
        app._dashboard_phase.set.assert_called_with("Готово к загрузке")

    def test_waiting_is_not_active_and_search_counts_the_selected_filter(self):
        urls = ["https://vimeo.com/101", "https://vimeo.com/202"]
        app = self.make_dashboard("\n".join(urls))
        app.skip_downloaded_enabled.return_value = False
        app.refresh_download_dashboard()
        app._dashboard_tabs["В работе"].configure.assert_called_with(text="В работе 0")
        app._dashboard_tabs["К обработке"].configure.assert_called_with(text="К обработке 2")
        app._dashboard_filter.get.return_value = "К обработке"
        app._dashboard_search.get.return_value = "202"
        app.refresh_download_dashboard()
        app._dashboard_hint.set.assert_called_with("Найдено: 1 из 2")
        self.assertEqual([row.url for row in app._dashboard_table.update_rows.call_args.args[0]], urls[1:])
        self.assertTrue(app._dashboard_table.update_rows.call_args.kwargs["reset_scroll"])

    def test_jump_uses_new_active_video_even_when_search_hides_both_rows(self):
        first, second = "https://vimeo.com/101", "https://vimeo.com/202"
        app = self.make_dashboard("")
        app._dashboard_session = session = DownloadSessionView([first, second])
        app._dashboard_search.get.return_value = "other title"
        app.is_downloading = True
        session.update(first, "audio", "first.mp4")
        session.update(second, "done", "second.mp4")
        app.refresh_download_dashboard()
        session.audio("first.mp4", "done")
        session.audio("second.mp4", "audio")
        app.refresh_download_dashboard()
        app._jump_to_queue_activity()
        app._dashboard_table.reveal.assert_called_once_with(second)

    def test_file_actions_use_the_selected_existing_paths(self):
        url = "https://vimeo.com/123"
        app = self.make_dashboard("")
        app.open_folder_path = Mock()
        app._dashboard_session = session = DownloadSessionView([url])
        with TemporaryDirectory() as temp:
            mp4 = Path(temp) / "video.mp4"
            mp4.touch()
            session.update(url, "video", mp4)
            session.audio(mp4, "audio_error", reason="Не пройдена проверка длительности")
            session.finish("failed")
            app.refresh_download_dashboard()
            app.can_retry_queue_audio.return_value = True
            actions = dict(app._queue_row_actions(url))
            self.assertIn("Повторить MP3", actions)
            self.assertNotIn("Открыть папку MP3", actions)
            actions["Открыть папку MP4"]()
            app.open_folder_path.assert_called_once_with(mp4.parent, "с файлом очереди")
            with patch("src.ui.download_dashboard.messagebox.showinfo") as notice:
                actions["Причина ошибки"]()
                self.assertIn("Не пройдена проверка длительности", notice.call_args.args[1])
                mp4.unlink()
                actions["Открыть папку MP4"]()
                self.assertIn("не найден", notice.call_args.args[1])
            self.assertNotIn("Открыть папку MP4", dict(app._queue_row_actions(url)))
