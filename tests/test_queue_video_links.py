"""Queue archive titles and click dispatch without browser or Tk startup."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.downloads.queue_state import STARTED_QUEUES_FOLDER, read_archived_video_titles
from src.downloads.urls import UrlQueueMixin
from src.ui.queue_rows import QueueRows, open_video_link


class QueueVideoLinkTests(unittest.TestCase):
    def test_saved_receipt_supplies_a_title_for_equivalent_youtube_links(self):
        identity = UrlQueueMixin().url_identity
        url = "https://www.youtube.com/watch?v=Dsh4mqo1dVo"
        with TemporaryDirectory() as temporary:
            archive = Path(temporary)
            directory = archive / STARTED_QUEUES_FOLDER
            directory.mkdir()
            batch = {
                "version": 1, "directory": str(archive), "status": "completed",
                "entered": [url], "urls": [url], "downloaded": [url],
                "video_files": {url: ["Название ролика.mp4", 1, 2, 3, 4]},
            }
            (directory / "queue_2.json").write_text(json.dumps(batch), encoding="utf-8")
            (directory / "queue_3.json").write_text("{", encoding="utf-8")
            batch["video_files"][url][0] = "Старое имя.mp4"
            (directory / "queue_1.json").write_text(json.dumps(batch), encoding="utf-8")
            titles = read_archived_video_titles(archive, identity)
        self.assertEqual(titles[identity("https://youtu.be/Dsh4mqo1dVo")], "Название ролика")

    def test_click_on_link_opens_full_url_and_retry_uses_its_own_target(self):
        url = "https://www.youtube.com/watch?v=Dsh4mqo1dVo&t=22"
        rows = SimpleNamespace(
            ROW_HEIGHT=62, rows=[(url, "Видео", "Не скачалось", ("", ""), None, None, "↻")],
            canvas=Mock(), selected=None, redraw=Mock(), _act=Mock(),
        )
        rows._open_selected_link = lambda: QueueRows._open_selected_link(rows)
        rows.canvas.canvasy.return_value = 42
        rows.canvas.bbox.return_value = (94, 34, 250, 50)
        rows.canvas.winfo_width.return_value = 460
        with patch("src.ui.queue_rows.webbrowser.open_new_tab", return_value=True) as open_tab:
            QueueRows._click(rows, SimpleNamespace(x=100, y=42))
            open_tab.assert_called_once_with(url)
            rows._act.assert_not_called()
            QueueRows._click(rows, SimpleNamespace(x=432, y=42))
            open_tab.assert_called_once()
            rows._act.assert_called_once()

    def test_invalid_or_non_web_urls_never_reach_the_browser(self):
        with patch("src.ui.queue_rows.webbrowser.open_new_tab") as open_tab:
            for url in ("file:///C:/temp/test", "https://", "http://[broken", "https://video.com/\n"):
                with self.subTest(url=url):
                    self.assertFalse(open_video_link(url))
            open_tab.assert_not_called()
