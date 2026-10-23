"""Network selection must preserve Windows routing and explicit proxy choice."""

import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
