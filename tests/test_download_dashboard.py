"""Status receipt contract without launching the desktop application."""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from threading import Lock
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.downloads.session_view import DownloadSessionView
from src.downloads.urls import UrlQueueMixin
from src.ui.download_dashboard import DownloadDashboardMixin, download_speed


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
        phase, rows, events = view.snapshot()
        self.assertEqual(phase, "Завершено с ошибками")
        self.assertEqual(dict(rows), {
            "one": ("done", "Первый"), "two": ("failed", ""), "three": ("audio_error", "Третий"),
        })
        self.assertEqual(events[0], phase)

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

    def test_draft_uses_same_validation_as_start_and_reuses_results_when_idle(self):
        class Dashboard(DownloadDashboardMixin, UrlQueueMixin):
            pass

        app = Dashboard()
        supported = 'https://vimeo.com/123'
        unsupported = 'https://vimeo.com.example/123'
        app.url_text = Mock()
        app.url_text.get.return_value = f'{supported}\n{unsupported}'
        for name in ('_dashboard_table', '_thumbnails', 'studio_session_status', 'root', '_dashboard_hint'):
            setattr(app, name, Mock())
        app._thumbnails.collect.return_value = False
        app._dashboard_search = SimpleNamespace(get=lambda: '')
        app._dashboard_filter = SimpleNamespace(get=lambda: 'Все')
        app._dashboard_tabs = {name: Mock() for name in ('Все', 'В работе', 'Готовые', 'Ошибки')}
        app._dashboard_title_results = None
        app._dashboard_archive_titles = {}
        app._dashboard_session_titles = {}
        app._dashboard_draft = app._dashboard_last = None
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
