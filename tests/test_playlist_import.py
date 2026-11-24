import json
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.ui.playlist_import import PlaylistImportMixin, _TITLE_REQUEST_LIMIT
from src.ui.video_list import VideoListMixin


class _Scheduler:
    def __init__(self):
        self._next_id = 0
        self.callbacks = {}

    def after(self, delay, callback, *args):
        self._next_id += 1
        self.callbacks[self._next_id] = (callback, args)
        return self._next_id

    def after_cancel(self, callback_id):
        del self.callbacks[callback_id]

    def drain_one(self):
        callback, args = self.callbacks.pop(next(iter(self.callbacks)))
        callback(*args)


class _ControlledExecutor:
    def __init__(self):
        self.requests = []

    def submit(self, callback, *args):
        future = Future()
        future.set_running_or_notify_cancel()
        self.requests.append((future, args))
        return future


class _Playlist(PlaylistImportMixin, VideoListMixin):
    def __init__(self):
        self.video_list = []
        self._studio_playlist_source = self.video_list
        self._playlist_checked = set()
        self._playlist_rows = Mock()
        self._playlist_title_executor = _ControlledExecutor()
        self._studio_playlist_window = _Scheduler()
        self.show_downloaded = Mock()
        self.show_downloaded.get.return_value = True
        self.show_studio_playlist = Mock()
        self.video_list_frame = Mock()
        self.video_stats_label = Mock()
        self.file_logger = Mock()
        self.log = Mock()
        self.add_urls_with_check = Mock(return_value=1)

    def load_videos(self, videos):
        self.finish_html_loading(videos, Mock(), 0)


def _video(index, title="Название", downloaded=False):
    key = f"{index:011d}"
    return {"video_id": key, "url": f"https://www.youtube.com/watch?v={key}",
            "title": title, "is_downloaded": downloaded}


class PlaylistImportTests(unittest.TestCase):
    def setUp(self):
        self.app = PlaylistImportMixin()
        self.app.log = Mock()
        self.app.file_logger = Mock()
        self.app._utf8_subprocess_env = lambda: {}
        self.app.subprocess_flags = 0

    @staticmethod
    def renderer(index, title):
        return {"playlistVideoRenderer": {
            "videoId": f"{index:011d}",
            "title": {"runs": [{"text": title}]},
        }}

    def test_large_list_preserves_all_unique_entries_in_order(self):
        rows = [self.renderer(0, "Long fixture title")] * 110
        rows += [self.renderer(i, "Long fixture title") for i in range(1, 122)]
        html = f"<script>var ytInitialData = {json.dumps({'contents': rows})};</script>"
        videos = self.app.parse_youtube_playlist_html_fast(html)
        self.assertEqual([v["video_id"] for v in videos], [f"{i:011d}" for i in range(122)])

    def test_fallback_respects_limit_and_deduplicates(self):
        ids = [0, 0, 1, 2, 3]
        html = " ".join(f'{{"videoId":"{i:011d}"}}' for i in ids)
        with patch("src.ui.playlist_import.MAX_VIDEOS_PER_LIST", 3):
            videos = self.app.parse_youtube_playlist_html_fast(html)
        self.assertEqual([v["video_id"] for v in videos], [f"{i:011d}" for i in range(3)])

    def test_fallback_keeps_title_from_its_own_object(self):
        first = '{"videoId":"00000000001","title":{"runs":[{"text":"Intro"}]}}'
        second = '{"title":{"runs":[{"text":"Outro"}]},"videoId":"00000000002"}'
        unrelated = '{"title":{"runs":[{"text":"Название канала"}]}}'
        with patch("src.ui.playlist_import.subprocess.run") as run:
            videos = self.app.parse_youtube_playlist_html_fast(" ".join((first, second, unrelated)))
        self.assertEqual([v["title"] for v in videos], ["Intro", "Outro"])
        run.assert_not_called()

    def test_parse_publishes_urls_and_missing_titles_without_subprocess(self):
        payload = {"contents": [self.renderer(1, "I"), self.renderer(2, "Intro"),
                                self.renderer(3, ""), self.renderer(4, "Без названия")]}
        html = f"<script>var ytInitialData = {json.dumps(payload)};</script>"
        with patch("src.ui.playlist_import.subprocess.run") as run:
            videos = self.app.parse_youtube_playlist_html_fast(html)
        self.assertEqual([v["title"] for v in videos], ["I", "Intro", "", "Без названия"])
        self.assertEqual(videos[2]["url"], "https://www.youtube.com/watch?v=00000000003")
        run.assert_not_called()

    def test_json_title_can_contain_script_terminator_like_text(self):
        payload = {"contents": [self.renderer(1, "Title }; tail")]}
        html = f"<script>window[\"ytInitialData\"] = {json.dumps(payload)};</script>"
        videos = self.app.parse_youtube_playlist_html_fast(html)
        self.assertEqual([v["title"] for v in videos], ["Title }; tail"])

    def test_title_subprocess_is_bounded_and_decodes_first_success(self):
        cancelled = Mock()
        cancelled.is_set.return_value = False
        with patch("src.ui.playlist_import.time.monotonic", return_value=10), \
             patch("src.ui.playlist_import.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout=" A &amp; B\n")) as run:
            self.assertEqual(self.app._fetch_playlist_title("https://youtube.com/watch?v=00000000001",
                                                           14, cancelled), "A & B")
            self.assertEqual(run.call_args.kwargs["timeout"], 4)
            cancelled.is_set.return_value = True
            self.assertIsNone(self.app._fetch_playlist_title("https://youtube.com/watch?v=00000000002",
                                                             14, cancelled))
        run.assert_called_once()


class PlaylistStateTests(unittest.TestCase):
    def setUp(self):
        self.app = _Playlist()

    def test_filter_and_removal_preserve_remaining_checks_by_id(self):
        first, downloaded, third, fourth = _video(1), _video(2, downloaded=True), _video(3), _video(4)
        self.app.load_videos([first, downloaded, third, fourth])
        self.app._set_playlist_checked(first["video_id"], False)
        self.app._set_playlist_checked(downloaded["video_id"], True)
        self.app.show_downloaded.get.return_value = False
        self.app.display_video_list()
        self.app._remove_playlist_video(third["video_id"])
        with patch("src.ui.video_list.messagebox.showinfo"):
            self.app.add_selected_to_queue()
        self.app.add_urls_with_check.assert_called_once_with([fourth["url"]])
        self.app.show_downloaded.get.return_value = True
        self.app.display_video_list()
        self.assertEqual(self.app._selected_playlist_videos(), [downloaded, fourth])

    def test_selection_commands_target_visible_videos_and_remove_only_after_confirmation(self):
        first, downloaded, third = _video(1), _video(2, downloaded=True), _video(3)
        self.app.load_videos([first, downloaded, third])
        self.app.select_all_videos()
        self.assertEqual(self.app._selected_playlist_videos(), [first, downloaded, third])
        self.app.select_only_new()
        self.assertEqual(self.app._selected_playlist_videos(), [first, third])
        self.app.deselect_all_videos()
        self.assertEqual(self.app._selected_playlist_videos(), [])
        self.app.select_all_videos()
        self.app.show_downloaded.get.return_value = False
        self.app.display_video_list()
        with patch("src.ui.video_list.messagebox.askyesno", side_effect=[False, True]) as ask:
            self.app.delete_selected_videos()
            self.assertEqual(self.app.video_list, [first, downloaded, third])
            self.app.delete_selected_videos()
        self.assertEqual(self.app.video_list, [downloaded])
        self.assertTrue(all("2 видео" in call.args[1] for call in ask.call_args_list))
        self.app.show_downloaded.get.return_value = True
        self.app.display_video_list()
        self.assertEqual(self.app._selected_playlist_videos(), [downloaded])

    def test_list_is_available_before_titles_and_discarded_requests_cannot_replace_new_list(self):
        old, removed = _video(1, ""), _video(2, "")
        self.app.load_videos([old, removed])
        self.app.show_studio_playlist.assert_called_once()
        self.assertEqual(self.app._selected_playlist_videos(), [old, removed])
        self.assertEqual(old["title"], "Без названия")
        old_job = self.app._playlist_title_job
        old_future, removed_future = (request[0] for request in self.app._playlist_title_executor.requests)
        self.app._remove_playlist_video(removed["video_id"])
        removed_future.set_result("Удалённый")
        self.app._studio_playlist_window.drain_one()
        self.assertEqual(self.app.video_list, [old])
        self.assertEqual(removed["title"], "Без названия")
        self.app._set_playlist_checked(old["video_id"], False)
        new = _video(1, "")
        self.app.load_videos([new])
        self.assertTrue(old_job.cancelled.is_set())
        self.assertIsNone(old_job.after_id)
        old_future.set_result("Поздний результат")
        self.app._poll_playlist_titles(old_job)
        self.assertEqual(new["title"], "Без названия")
        self.assertEqual(self.app._selected_playlist_videos(), [new])
        new_future = self.app._playlist_title_executor.requests[-1][0]
        new_future.set_result("Актуальное название")
        self.app._set_playlist_checked(new["video_id"], False)
        self.app._studio_playlist_window.drain_one()
        self.assertEqual(new["title"], "Актуальное название")
        self.assertEqual(self.app._selected_playlist_videos(), [])
        self.assertEqual(self.app._studio_playlist_window.callbacks, {})
        self.assertIsNone(self.app._playlist_title_job)

    def test_successful_titles_are_reused_and_failures_expire(self):
        with patch("src.ui.playlist_import.time.monotonic", return_value=100) as clock:
            self.app.load_videos([_video(1, ""), _video(2, ""), _video(3, "Без названия")])
            requests = self.app._playlist_title_executor.requests
            self.assertEqual(len(requests), 2)
            requests[0][0].set_result("Название из кэша")
            requests[1][0].set_result("")
            self.app._studio_playlist_window.drain_one()
            self.app.load_videos([_video(1, ""), _video(2, "")])
            self.assertEqual(len(requests), 2)
            self.assertEqual([video["title"] for video in self.app.video_list],
                             ["Название из кэша", "Без названия"])
            clock.return_value = 401
            self.app.load_videos([_video(1, ""), _video(2, "")])
        self.assertEqual(len(requests), 3)
        self.assertTrue(requests[2][1][0].endswith("00000000002"))

    def test_title_work_is_bounded_per_import_and_stops_at_deadline(self):
        with patch("src.ui.playlist_import.time.monotonic", return_value=100) as clock:
            self.app.load_videos([_video(index, "") for index in range(1000)])
            job = self.app._playlist_title_job
            requests = self.app._playlist_title_executor.requests
            self.assertEqual(len(requests), 3)
            self.assertEqual(len(job.pending) + len(requests), _TITLE_REQUEST_LIMIT)
            clock.return_value = job.deadline
            for future, _ in requests:
                future.set_result(None)
            self.app._studio_playlist_window.drain_one()
        self.assertEqual(len(requests), 3)
        self.assertEqual(self.app._studio_playlist_window.callbacks, {})
        self.assertIsNone(self.app._playlist_title_job)
