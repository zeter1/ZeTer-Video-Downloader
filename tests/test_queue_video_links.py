"""Queue archive titles and click dispatch without browser or Tk startup."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.downloads.queue_state import STARTED_QUEUES_FOLDER, media_fingerprint, read_archived_video_entries
from src.downloads.urls import UrlQueueMixin
from src.ui.queue_rows import QueueRow, QueueRows, fit_lines, open_video_link, source_label


class QueueVideoLinkTests(unittest.TestCase):
    def test_saved_receipt_supplies_a_title_for_equivalent_youtube_links(self):
        identity = UrlQueueMixin().url_identity
        url = "https://www.youtube.com/watch?v=Dsh4mqo1dVo"
        with TemporaryDirectory() as temporary:
            archive = Path(temporary)
            directory = archive / STARTED_QUEUES_FOLDER
            directory.mkdir()
            video_dir = archive / "Видео"
            video_dir.mkdir()
            target = video_dir / "Название ролика.mp4"
            target.write_bytes(b"sample mp4")
            batch = {
                "version": 1, "directory": str(archive), "status": "completed",
                "entered": [url], "urls": [url], "downloaded": [url],
                "video_files": {url: [target.name, *media_fingerprint(target)]},
            }
            (directory / "queue_2.json").write_text(json.dumps(batch), encoding="utf-8")
            (directory / "queue_3.json").write_text("{", encoding="utf-8")
            batch["video_files"][url][0] = "Старое имя.mp4"
            (directory / "queue_1.json").write_text(json.dumps(batch), encoding="utf-8")
            entries = read_archived_video_entries(archive, identity)
            entry = entries[identity("https://youtu.be/Dsh4mqo1dVo")]
            self.assertEqual(entry.title, "Название ролика")
            self.assertEqual(entry.media_paths(), (target, None))
            target.write_bytes(b"changed")
            self.assertEqual(entry.media_paths(), (None, None))

    def test_row_click_selects_and_uses_distinct_action_hit_areas(self):
        url = "https://www.youtube.com/watch?v=Dsh4mqo1dVo&t=22"
        rows = SimpleNamespace(
            ROW_HEIGHT=62, rows=[QueueRow(url, "Видео", "Ошибка MP3", ("", ""), None, None, "Повторить MP3")],
            canvas=Mock(), selected=None, redraw=Mock(), _selected_menu=Mock(), run_primary=Mock(),
        )
        rows._select_at = lambda event: QueueRows._select_at(rows, event)
        rows.canvas.canvasy.return_value = 42
        boxes = {"primary:0": (320, 30, 419, 52), "menu:0": (426, 30, 452, 52)}
        rows.canvas.bbox.side_effect = boxes.get
        with patch("src.ui.queue_rows.webbrowser.open_new_tab", return_value=True) as open_tab:
            QueueRows._click(rows, SimpleNamespace(x=100, y=42))
            open_tab.assert_not_called()
            self.assertEqual(rows.selected, url)
            rows.run_primary.assert_not_called()
            QueueRows._click(rows, SimpleNamespace(x=343, y=42))
            rows.run_primary.assert_called_once_with(url)
            QueueRows._click(rows, SimpleNamespace(x=432, y=42))
            rows._selected_menu.assert_called_once()
            QueueRows._open_selected_link(rows)
            open_tab.assert_called_once_with(url)

    def test_title_fits_two_lines_with_ellipsis_and_subtitle_is_short(self):
        title = "Длинное название ВидеоБезПробелов"
        lines = fit_lines(title, 12, len, 2)
        self.assertEqual(lines[0], "Длинное")
        self.assertTrue(lines[1].endswith("…"))
        self.assertLessEqual(max(map(len, lines)), 12)
        self.assertEqual(fit_lines("Оченьдлинноеслово", 8, len, 2), ("Оченьдли", "нноесло…"))
        self.assertEqual(source_label("https://youtu.be/ABCDEFGHIJK?feature=share"), "YouTube")

    def test_filtered_updates_preserve_selected_row_and_scroll_anchor(self):
        rows = SimpleNamespace(ROW_HEIGHT=60, canvas=Mock(), selected="c", redraw=Mock())
        make = lambda url: QueueRow(url, url, "Ожидает", ("", ""), None, None, None)
        rows.rows = tuple(map(make, "abcde"))
        rows.canvas.canvasy.return_value = 65
        QueueRows.update_rows(rows, tuple(map(make, "bcde")))
        self.assertEqual(rows.selected, "c")
        rows.canvas.yview_moveto.assert_called_once_with(5 / 240)
        QueueRows.update_rows(rows, tuple(map(make, "de")), reset_scroll=True)
        self.assertIsNone(rows.selected)
        rows.canvas.yview_moveto.assert_called_with(0)

    def test_narrow_row_keeps_visible_status_and_actions_without_preview_placeholder(self):
        font = SimpleNamespace(measure=lambda text: len(text) * 7)
        rows = SimpleNamespace(
            title_font=font, label_font=font, status_font=font, title_height=14, label_height=12,
            ROW_HEIGHT=54, selected=None, canvas=Mock(), thumbnail=Mock(),
            request_preview=Mock(), _action=Mock(), _visible_images=[],
        )
        row = QueueRow("https://youtu.be/ABCDEFGHIJK", "Длинное название видео " * 5,
                       "Скачивание", ("green", "green"), 42, "ABCDEFGHIJK", None)
        compact, status_x, action_x = QueueRows._columns(rows, 380)
        self.assertTrue(compact)
        QueueRows._draw(rows, row, 0, 380, compact, status_x, action_x)
        text = [call.kwargs["text"] for call in rows.canvas.create_text.call_args_list]
        self.assertIn("Скачивание 42%", text)
        self.assertIn("YouTube", text)
        rows._action.assert_called_once_with(346, 27, 372, "⋯", "menu:0")
        rows.request_preview.assert_not_called()

    def test_invalid_or_non_web_urls_never_reach_the_browser(self):
        with patch("src.ui.queue_rows.webbrowser.open_new_tab") as open_tab:
            for url in ("file:///C:/temp/test", "https://", "http://[broken", "https://video.com/\n"):
                with self.subTest(url=url):
                    self.assertFalse(open_video_link(url))
            open_tab.assert_not_called()
