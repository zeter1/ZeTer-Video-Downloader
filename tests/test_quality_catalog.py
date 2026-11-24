import subprocess
import unittest

from src.downloads.youtube_candidate_formats import quality_catalog_miss, require_better_video
from src.downloads.youtube_command import video_format_args


class QualityCatalogTests(unittest.TestCase):
    def select(self, formats, reserve, **options):
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            self.skipTest('yt-dlp is required to run the real format selector')
        args = require_better_video(video_format_args(**options), reserve)
        with YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True,
                        'format': args[1], 'format_sort_force': True,
                        'format_sort': args[-1].split(','), 'ignore_no_formats_error': True}) as ydl:
            return ydl.process_ie_result({'id': 'fixture', 'title': 'fixture', 'formats': formats},
                                         download=False).get('format_id')

    @staticmethod
    def stream(name, height, width, *, combined=True, language='ru', protocol='https'):
        return dict(format_id=name, url='https://example.invalid/' + name,
                    protocol=protocol, height=height, width=width, language=language,
                    ext='mp4' if height else 'm4a', vcodec='h264' if height else 'none',
                    acodec='aac' if combined else 'none')

    def test_only_known_non_improvements_are_excluded_in_every_profile(self):
        for options in ({}, {'fallback_any': True}, {'progressive': True}):
            with self.subTest(options):
                identical_id = self.stream('same-id', 720, 1280)
                above_cap = self.stream('4k', 2160, 3840)
                self.assertIsNone(self.select([identical_id, above_cap], 720, **options))
                # Another catalog may reuse a format ID for a higher-quality stream.
                different_profile = self.stream('same-id', 1080, 1920)
                self.assertEqual(self.select([different_profile, above_cap], 720, **options), 'same-id')

    def test_cropped_fullhd_and_unknown_width_are_still_candidates(self):
        for width in (1920, None):
            with self.subTest(width=width):
                crop = self.stream('crop', 800, width)
                lower = self.stream('lower', 800, 1200)
                self.assertEqual(self.select([crop, lower], 900), 'crop')
        self.assertIsNone(self.select([self.stream('strip', 720, 1920)], 900))

    def test_separate_audio_language_and_protocol_filters_remain_effective(self):
        direct = self.stream('direct', 1080, 1920, combined=False)
        hls = self.stream('hls', 1080, 1920, protocol='m3u8_native')
        audio = self.stream('audio', None, None)
        formats = [direct, hls, audio]
        self.assertEqual(self.select(formats, 720, preferred_audio_language='ru'), 'direct+audio')
        self.assertEqual(self.select([hls], 720, fallback_any=True), 'hls')

    def test_only_clean_selection_miss_is_reusable(self):
        miss = 'ERROR: [youtube] x: Requested format is not available. Use --list-formats for a list of available formats'
        with_warning = 'WARNING: [youtube] Some formats were missing\n' + miss
        with_age_notice = 'WARNING: Your yt-dlp version (2026.08.19) is older than 90 days!\n' + miss
        for output, expected in ((miss, (True, True)), (with_warning, (True, False)),
                                 (with_age_notice, (True, True)),
                                 ('ERROR: HTTP Error 403\n' + miss, (False, False)),
                                 ('[DOWNLOAD_PLAN] id=x\n' + miss, (False, False))):
            with self.subTest(output=output):
                self.assertEqual(quality_catalog_miss(subprocess.CompletedProcess([], 1, output, '')), expected)
