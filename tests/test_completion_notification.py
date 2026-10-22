import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.application import VideoDownloader


class Widget:
    """Узкая граница Tk: тесты не создают окна и не запускают приложение."""
    def __init__(self, parent=None, **options):
        self.parent = parent
        self.options = options
        self.children = []
        self.protocols = {}
        self.bindings = {}
        self.attributes_set = {}
        self.destroyed = False
        self.geometry_value = None
        if parent is not None:
            parent.children.append(self)

    def pack(self, **options):
        pass

    def config(self, **options):
        self.options.update(options)

    configure = config

    def grid_remove(self):
        pass

    def grid(self, **options):
        pass

    def withdraw(self):
        pass

    def overrideredirect(self, value):
        self.borderless = value

    def attributes(self, name, value):
        self.attributes_set[name] = value

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def bind(self, name, callback, **options):
        self.bindings[name] = callback

    def update_idletasks(self):
        pass

    def winfo_reqheight(self):
        return 216

    def geometry(self, value):
        self.geometry_value = value

    def deiconify(self):
        pass

    def lift(self):
        pass

    def destroy(self):
        self.destroyed = True


class Scheduler(Widget):
    def __init__(self):
        super().__init__()
        self.callbacks = {}
        self.serial = 0
        self.owner = threading.get_ident()

    def after(self, delay, callback):
        if threading.get_ident() != self.owner:
            raise AssertionError('Tk called outside its owning thread')
        self.serial += 1
        self.callbacks[self.serial] = callback
        return self.serial

    def after_cancel(self, identifier):
        del self.callbacks[identifier]

    def tick(self):
        identifier = min(self.callbacks)
        self.callbacks.pop(identifier)()


class FolderVariable:
    def __init__(self, value):
        self.value = value
        self.callback = None

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        if self.callback is not None:
            self.callback('folder', '', 'write')

    def trace_add(self, mode, callback):
        self.callback = callback
        return 'folder_trace'

    def trace_remove(self, mode, identifier):
        self.callback = None


def descendants(widget):
    for child in widget.children:
        yield child
        yield from descendants(child)


class CompletionDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='уведомление ')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.app = VideoDownloader.__new__(VideoDownloader)
        app = self.app
        app.root = Scheduler()
        app.save_path = FolderVariable(str(self.directory))
        app.initialize_completion_notifications()
        app.download_btn = Widget()
        app.cancel_btn = Widget()
        app.main_progress_frame = Widget()
        app.save_path_entry = Widget(state='normal')
        app.browse_path_btn = Widget(state='normal')
        app.log = Mock()
        app.format_duration = lambda elapsed: f'{elapsed:.0f} с'
        app.is_downloading = True
        app.download_start_time = 0
        app.download_outcome = 'completed'
        app.download_settings_snapshot = {'save_path': str(self.directory)}
        for name in ('Toplevel', 'Frame', 'Label', 'Button'):
            patcher = patch(f'src.ui.completion_notification.tk.{name}', Widget)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('src.ui.completion_notification.ttk.Button', Widget)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('src.ui.completion_notification.desktop_work_area',
                        return_value=(0, 0, 1920, 1040))
        patcher.start()
        self.addCleanup(patcher.stop)

    def notify(self, outcome='completed'):
        self.app.download_outcome = outcome
        self.app.finish_download()
        self.app.root.tick()
        return self.app._completion_windows[-1]

    def test_worker_enqueues_result_without_touching_tk(self):
        app = VideoDownloader.__new__(VideoDownloader)
        app.root = Mock()
        app.root.after.side_effect = AssertionError('Worker must not call Tk')
        app._completion_queue = queue.Queue()
        app._completion_closed = False
        app.download_outcome = 'completed'
        app.download_start_time = 100
        app.download_settings_snapshot = {'save_path': 'G:/Выбранная папка'}
        app.save_path = Mock()
        app.save_path.get.side_effect = AssertionError('Worker must not read Tk')
        errors = []

        def finish():
            try:
                app.finish_download()
            except Exception as error:
                errors.append(error)

        with patch('src.downloads.progress.time.time', return_value=125):
            thread = threading.Thread(target=finish)
            thread.start()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(app._completion_queue.get_nowait(),
                         ('completed', 25))
        app.root.after.assert_not_called()
        app.save_path.get.assert_not_called()

    def test_completion_changes_ui_only_when_main_thread_polls(self):
        self.app.finish_download()
        self.assertTrue(self.app.is_downloading)
        self.assertEqual(self.app._completion_windows, [])
        self.app.root.tick()
        self.assertFalse(self.app.is_downloading)
        self.assertEqual(len(self.app._completion_windows), 1)
        window = self.app._completion_windows[0]
        self.assertTrue(window.attributes_set['-topmost'])
        self.assertEqual(window.geometry_value, '420x216+1484+808')

    def test_notification_persists_and_only_cross_closes_it(self):
        window = self.notify()
        window.protocols['WM_DELETE_WINDOW']()
        window.bindings['<Escape>'](SimpleNamespace())
        for _ in range(20):
            self.app.root.tick()
        self.assertFalse(window.destroyed)
        self.assertEqual(self.app._completion_windows, [window])
        cross = next(w for w in descendants(window) if w.options.get('text') == '×')
        cross.options['command']()
        self.assertTrue(window.destroyed)
        self.assertEqual(self.app._completion_windows, [])

    def test_open_folder_follows_save_location_change_and_keeps_notice(self):
        window = self.notify()
        new_directory = self.directory / 'новая'
        self.app.save_path.set(str(new_directory))
        self.app.download_settings_snapshot = {'save_path': str(self.directory / 'новая')}
        button = next(w for w in descendants(window)
                      if w.options.get('text') == 'Открыть папку')
        with patch('src.ui.folders.platform.system', return_value='Windows'), \
             patch('src.ui.folders.os.startfile', create=True) as startfile:
            button.options['command']()
        startfile.assert_called_once_with(str(new_directory))
        self.assertIn(str(new_directory), [w.options.get('text') for w in descendants(window)])
        self.assertNotIn(str(self.directory), [w.options.get('text') for w in descendants(window)])
        self.assertFalse(window.destroyed)
        self.assertTrue(new_directory.is_dir())
        self.assertFalse((self.directory / 'другая').exists())

    def test_multiple_tasks_keep_previous_notice_and_restack_after_close(self):
        first = self.notify()
        second = self.notify('failed')
        self.assertEqual(self.app._completion_windows, [first, second])
        self.assertFalse(first.destroyed)
        self.assertEqual(first.geometry_value, '420x216+1484+580')
        self.assertEqual(second.geometry_value, '420x216+1484+808')
        self.app._close_completion_notification(second)
        self.assertEqual(first.geometry_value, '420x216+1484+808')
        self.assertFalse(first.destroyed)

    def test_failed_and_cancelled_sessions_are_not_shown_as_success(self):
        for outcome, title, level in (
            ('failed', 'Завершено с ошибками', 'ERROR'),
            ('cancelled', 'Задача отменена', 'WARNING'),
        ):
            with self.subTest(outcome=outcome):
                window = self.notify(outcome)
                texts = [w.options.get('text', '') for w in descendants(window)]
                self.assertIn(title, texts)
                self.assertNotIn('Готово', texts)
                self.assertEqual(self.app.log.call_args.args[1], level)

    def test_root_close_cancels_poller_and_rejects_late_completion(self):
        app = self.app
        app._stop_completion_notifications(SimpleNamespace(widget=Widget()))
        self.assertFalse(app._completion_closed)
        app._stop_completion_notifications(SimpleNamespace(widget=app.root))
        self.assertEqual(app.root.callbacks, {})
        app.finish_download()
        app._poll_download_completion()
        self.assertTrue(app._completion_queue.empty())
        self.assertEqual(app._completion_windows, [])
        self.assertEqual(app.root.callbacks, {})

    def test_direct_start_failure_shows_current_save_location(self):
        app = self.app
        app.save_path.set(str(self.directory / 'другая'))
        app.download_outcome = 'failed'
        app._finish_download_ui()
        window = app._completion_windows[0]
        texts = [w.options.get('text', '') for w in descendants(window)]
        self.assertIn(str(self.directory / 'другая'), texts)
        self.assertNotIn(str(self.directory), texts)

    def prepare_start(self):
        app = self.app
        app.is_downloading = False
        app.missing_deps = []
        app.cancel_flag = threading.Event()
        app.concurrent_var = SimpleNamespace(get=lambda: 1)
        app.settings = dict(save_path=str(self.directory), audio_quality='320k',
                            merge_audio=False, download_subtitles=False, split_hours=0,
                            proxy_enabled=False, proxy_url='')
        app.url_text = Mock()
        app.url_text.get.return_value = 'https://example.invalid/video'
        app.validate_url = lambda url: True
        app.deduplicate_urls_by_identity = lambda urls: (urls, [])
        app.is_url_downloaded = lambda url: False
        app.check_internet_connection = lambda: True
        app.is_youtube_url = lambda url: False
        app.total_files = Mock()
        app.completed_files = Mock()
        app.youtube_strategy_state_lock = threading.Lock()
        app.main_progress = {}
        app.get_proxy_url = lambda: ''
        for name in ('save_current_settings', 'update_main_progress',
                     'record_problem', 'write_problem_session_summary'):
            setattr(app, name, Mock())

    def test_start_locks_folder_until_terminal_event_is_applied(self):
        app = self.app
        for outcome in ('completed', 'failed', 'cancelled'):
            with self.subTest(outcome=outcome):
                self.prepare_start()
                states_at_worker_start = []
                with patch('src.downloads.manager.threading.Thread') as worker:
                    worker.return_value.start.side_effect = lambda: states_at_worker_start.append(
                        (app.save_path_entry.options['state'], app.browse_path_btn.options['state']))
                    app.start_download()
                self.assertEqual(states_at_worker_start, [('disabled', 'disabled')])
                self.assertTrue(app.is_downloading)
                with patch('src.ui.settings_controls.filedialog.askdirectory') as dialog:
                    app.browse_path()
                dialog.assert_not_called()
                app.download_outcome = outcome
                app.finish_download()
                self.assertEqual(app.save_path_entry.options['state'], 'disabled')
                app.root.tick()
                self.assertEqual(app.save_path_entry.options['state'], 'normal')
                self.assertEqual(app.browse_path_btn.options['state'], 'normal')
                self.assertFalse(app.is_downloading)

    def test_worker_start_failure_also_unlocks_folder(self):
        self.prepare_start()
        with patch('src.downloads.manager.threading.Thread') as worker:
            worker.return_value.start.side_effect = RuntimeError('cannot start worker')
            self.app.start_download()
        self.assertFalse(self.app.is_downloading)
        self.assertEqual(self.app.save_path_entry.options['state'], 'normal')
        self.assertEqual(self.app.browse_path_btn.options['state'], 'normal')
        self.assertEqual(len(self.app._completion_windows), 1)

    def test_folder_dialog_result_is_ignored_if_task_started_while_dialog_was_open(self):
        self.app.is_downloading = False
        def choose_folder():
            self.app.is_downloading = True
            return str(self.directory / 'не менять')
        with patch('src.ui.settings_controls.filedialog.askdirectory', side_effect=choose_folder):
            self.app.browse_path()
        self.assertEqual(self.app.save_path.get(), str(self.directory))


if __name__ == '__main__':
    unittest.main()
