from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from src.app.paths import initialize_app_paths
from src.diagnostics.migration import DiagnosticsMigrationMixin
from src.ui.folders import FoldersMixin
from src.ui.log_view import LogViewMixin


class App(DiagnosticsMigrationMixin, FoldersMixin, LogViewMixin):
    read_problem_logging_setting_default = Mock(return_value=False)


class DownloadArchivePathsTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="Архив загрузок ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.archive = self.root / "История скачиваний"

    def start(self):
        app = App()
        with patch("src.app.paths.get_program_dir", return_value=self.root):
            initialize_app_paths(app)
        return app

    def write(self, relative, contents):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def test_imports_legacy_directories_and_preserves_colliding_files_on_restart(self):
        legacy_files = {
            "Ссылки на скачивания/links_legacy.txt": b"queued",
            "История ссылок/downloaded_legacy.txt": b"old history",
            "Логи скачивания/Сессии скачанных/downloaded_log_legacy.txt": b"old log",
            "Log_download/Downloaded_Sessions/downloaded_log_english.txt": b"english log",
            "History_Links/downloaded_english.txt": b"english history",
        }
        for relative, contents in legacy_files.items():
            self.write(relative, contents)
        active_history = self.write(
            "История скачиваний/История ссылок/downloaded_legacy.txt", b"active history"
        )
        active_log = self.write(
            "История скачиваний/Логи скачивания/Сессии скачанных/downloaded_log_legacy.txt",
            b"active log",
        )

        initial = self.start()
        self.assertEqual(initial.download_archive_dir, self.archive)
        self.assertEqual(initial.history_dir, self.archive / "История ссылок")
        self.assertEqual(initial.download_links_dir, self.archive / "Ссылки на скачивания")
        self.assertEqual(initial.log_dir, self.archive / "Логи скачивания")
        self.assertEqual(initial.downloaded_sessions_dir, initial.log_dir / "Сессии скачанных")
        self.assertEqual(initial.problem_log_dir, self.root / "Логи проблем")
        self.assertEqual(active_history.read_bytes(), b"active history")
        self.assertEqual(active_log.read_bytes(), b"active log")
        self.assertEqual((initial.download_links_dir / "links_legacy.txt").read_bytes(), b"queued")
        for directory, glob, expected in (
            (initial.history_dir, "downloaded_*.txt", {b"active history", b"old history", b"english history"}),
            (initial.downloaded_sessions_dir, "downloaded_log_*.txt", {b"active log", b"old log", b"english log"}),
        ):
            with self.subTest(directory=directory):
                files = list(directory.glob(glob))
                self.assertEqual(len(files), len(expected))
                self.assertEqual({unit.read_bytes() for unit in files}, expected)
        for relative in legacy_files:
            with self.subTest(relative=relative):
                self.assertFalse((self.root / relative.split("/", 1)[0]).exists())

        state = {path: path.read_bytes() for path in self.archive.rglob("*") if path.is_file()}
        self.start()
        self.assertEqual(
            {path: path.read_bytes() for path in self.archive.rglob("*") if path.is_file()}, state
        )

    def test_locked_legacy_directory_remains_available_until_next_start(self):
        legacy_history = self.write("История ссылок/downloaded_legacy.txt", b"legacy history")
        legacy_log = self.write(
            "Log_download/Downloaded_Sessions/downloaded_log_legacy.txt", b"legacy log"
        )
        rename = Path.rename

        def deny_legacy_folder_rename(path, target):
            if path in {legacy_history.parent, legacy_log.parents[1]}:
                raise PermissionError("synthetic migration lock")
            return rename(path, target)

        with patch.object(Path, "rename", deny_legacy_folder_rename):
            initial = self.start()

        self.assertIn(legacy_history.parent, initial.legacy_history_dirs)
        self.assertIn(legacy_log.parent, initial.legacy_downloaded_sessions_dirs)
        self.assertTrue(any("synthetic migration lock" in note for note in initial.migration_notes))
        self.assertEqual(legacy_history.read_bytes(), b"legacy history")
        self.assertEqual(legacy_log.read_bytes(), b"legacy log")

        reopened = self.start()
        self.assertFalse(legacy_history.exists())
        self.assertFalse(legacy_log.exists())
        self.assertEqual((reopened.history_dir / legacy_history.name).read_bytes(), b"legacy history")
        self.assertEqual(
            (reopened.downloaded_sessions_dir / legacy_log.name).read_bytes(), b"legacy log"
        )

    def test_archive_action_opens_common_directory(self):
        app = self.start()
        app.open_folder_path = Mock()
        widget = Mock()
        tag = "DOWNLOAD_ARCHIVE_FOLDER_LINK"
        app._configure_log_text_widget(widget)
        app._insert_log_message("История скачиваний: текущая очередь", tag, widget)
        widget.insert.assert_any_call("end", "История скачиваний", ("INFO", tag))
        click_handler = next(
            call.args[2] for call in widget.tag_bind.call_args_list
            if call.args[:2] == (tag, "<Button-1>")
        )
        click_handler(None)
        app.open_folder_path.assert_called_once_with(self.archive, "История скачиваний")
