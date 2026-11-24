import hashlib
from pathlib import Path
import tempfile
import unittest

from src.downloads.history import DownloadHistoryMixin
from src.downloads.urls import UrlQueueMixin


class UrlHistory(DownloadHistoryMixin, UrlQueueMixin):
    def __init__(self):
        self.downloaded_url_hashes = set()
        self.downloaded_video_ids = set()


class DownloadUrlIdentityTests(unittest.TestCase):
    video_id = 'ABCD0123456'
    short = f'https://youtu.be/{video_id}'
    live = f'https://www.youtube.com/live/{video_id}?si=ref'
    vimeo = f'https://vimeo.com/123?ref={short}'

    def setUp(self):
        self.app = UrlHistory()

    def test_queue_keeps_vimeo_and_deduplicates_youtube_live(self):
        unique, duplicates = self.app.deduplicate_urls_by_identity([self.short, self.vimeo, self.live])
        self.assertEqual(unique, [self.short, self.vimeo])
        self.assertEqual(duplicates, [self.live])

    def test_history_tracks_platforms_separately_and_keeps_legacy_hashes(self):
        self.app.remember_downloaded_url_in_memory(self.vimeo)
        self.assertTrue(self.app.is_url_downloaded(self.vimeo))
        self.assertFalse(self.app.is_url_downloaded(self.short))
        legacy = f'https://www.youtube.com/watch?feature=share&v={self.video_id}'
        self.app.downloaded_url_hashes.add(hashlib.md5(legacy.encode()).hexdigest())
        self.assertTrue(self.app.is_url_downloaded(legacy))
        self.app.remember_downloaded_url_in_memory(self.live)
        self.assertTrue(self.app.is_url_downloaded(self.short))

    def test_history_rebuilds_derived_fields_only_when_original_is_present(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.app.downloaded_sessions_dir = directory
        self.app.history_dir = directory / 'human-history'
        self.app.retention_cutoff = lambda: None
        self.app.is_recent_log_file = lambda *_: True
        watch = f'https://www.youtube.com/watch?v={self.video_id}'
        bad_hash = hashlib.md5(watch.encode()).hexdigest()
        legacy = 'https://youtu.be/LegaCy12345'
        legacy_hash = hashlib.md5(legacy.encode()).hexdigest()
        content = f'{bad_hash}\t{self.video_id}\t{watch}\t{self.vimeo}\n{legacy_hash}\n'
        log = directory / 'downloaded_log_fixture.txt'
        log.write_text(content, encoding='utf-8')

        self.app.load_download_log()

        self.assertTrue(self.app.is_url_downloaded(self.vimeo))
        self.assertFalse(self.app.is_url_downloaded(watch))
        self.assertFalse(self.app.is_url_downloaded(self.short))
        self.assertTrue(self.app.is_url_downloaded(legacy))
        self.assertEqual(log.read_text(encoding='utf-8'), content)

    def test_supported_youtube_variants_keep_exact_id_case(self):
        for url in (
            self.live,
            self.short + '?si=share',
            f'https://M.YouTube.COM./watch?feature=share&v={self.video_id}#t=15',
            *(f'https://www.youtube.com/{path}/{self.video_id}' for path in ('embed', 'shorts', 'v')),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.app.url_identity(url), f'youtube:{self.video_id}')
        self.assertNotEqual(self.app.url_identity(self.short), self.app.url_identity(self.short.lower()))

    def test_non_youtube_hosts_and_invalid_or_embedded_ids_are_not_youtube(self):
        for url in (
            self.vimeo,
            f'https://example.com/youtube.com/shorts/{self.video_id}',
            f'https://youtube.com@vimeo.com/live/{self.video_id}',
            self.short + 'A',
            self.short + '/extra',
            f'https://youtube.com/watch?v={self.video_id}!',
            f'https://youtube.com/watch?ref={self.short}',
            f'https://youtube.com/watch?v={self.video_id[:-1]}',
        ):
            with self.subTest(url=url):
                self.assertIsNone(self.app.extract_video_id(url))
