"""Network selection must preserve Windows routing and explicit proxy choice."""

import unittest
from pathlib import Path
from threading import Lock
from unittest.mock import Mock, patch

from src.downloads.history import DownloadHistoryMixin
from src.downloads.task import DownloadTaskMixin
from src.downloads.youtube_access import YouTubeAccessMixin
from src.downloads.youtube_command import build_ytdlp_download_command


class AccessApp(YouTubeAccessMixin):
    def __init__(self):
        self.settings = {
            'proxy_enabled': False,
            'proxy_url': 'socks5://127.0.0.1:1080',
            'download_subtitles': False,
        }

    def is_youtube_url(self, url):
        return False


class NetworkAccessTests(unittest.TestCase):
    def test_system_connection_does_not_force_a_proxy_or_bypass_it(self):
        app = AccessApp()
        self.assertEqual(app.build_proxy_args(), [])

    def test_explicit_proxy_can_be_disabled_between_sessions_without_restart(self):
        app = AccessApp()
        app.settings['proxy_enabled'] = True
        self.assertEqual(app.build_proxy_args(), ['--proxy', 'socks5://127.0.0.1:1080'])
        app.settings['proxy_enabled'] = False
        self.assertEqual(app.build_proxy_args(), [])
        self.assertEqual(app.settings['proxy_url'], 'socks5://127.0.0.1:1080')

    def test_download_command_preserves_selected_connection(self):
        app = AccessApp()
        for enabled in (False, True, False):
            with self.subTest(explicit_proxy=enabled):
                app.settings['proxy_enabled'] = enabled
                command, *_ = build_ytdlp_download_command(
                    app, 'https://example.invalid/video', {'args': []}, [],
                    Path('synthetic-output'), app.build_proxy_args(),
                )
                self.assertEqual('--proxy' in command, enabled)
                if enabled:
                    self.assertEqual(command[command.index('--proxy') + 1], app.settings['proxy_url'])


class DownloadStart(DownloadTaskMixin, DownloadHistoryMixin):
    pass


class DownloadPacingTests(unittest.TestCase):
    def setUp(self):
        self.app = DownloadStart()
        self.app.total_files = Mock()
        self.app.log = Mock()
        self.app.record_problem = Mock()
        self.app.file_logger = Mock()
        self.app.validate_url = Mock(return_value=True)
        self.app.should_skip_downloaded = Mock(return_value=False)
        self.app.cancel_flag = Mock()
        self.app.cancel_flag.is_set.return_value = False
        self.app.cancel_flag.wait.side_effect = self.advance_clock
        self.app.youtube_strategy_state_lock = Lock()
        self.app.run_yt_dlp_with_download_slot = Mock(return_value=None)
        self.app.update_progress = Mock()
        self.now = 10.0
        clock = patch('src.downloads.task.monotonic', side_effect=lambda: self.now, create=True)
        clock.start()
        self.addCleanup(clock.stop)

    def advance_clock(self, elapsed):
        self.assertFalse(self.app.youtube_strategy_state_lock.locked())
        self.now += elapsed
        return False

    def download(self, url, index):
        self.app.download_single_video(url, index, [], [], [])

    def test_only_consecutive_youtube_starts_need_remaining_pause(self):
        self.download('https://vimeo.com/1', 5)
        self.download('https://youtu.be/abcdefghijk', 6)
        self.now += 1.0
        self.download('https://dailymotion.com/video/1', 7)
        self.download('https://www.youtube.com/watch?v=lmnopqrstuv', 8)
        self.now += 20.0
        self.download('https://youtube.com/watch?v=12345678901', 9)

        self.app.cancel_flag.wait.assert_called_once_with(2.0)
        self.assertEqual(self.app.run_yt_dlp_with_download_slot.call_count, 5)

    def test_cancellation_interrupts_pause_before_youtube_download(self):
        self.download('https://youtube.com/watch?v=12345678901', 0)
        self.app.cancel_flag.wait.side_effect = None
        self.app.cancel_flag.wait.return_value = True
        self.download('https://youtu.be/abcdefghijk', 1)

        self.app.cancel_flag.wait.assert_called_once_with(3.0)
        self.app.run_yt_dlp_with_download_slot.assert_called_once()


if __name__ == '__main__':
    unittest.main()
