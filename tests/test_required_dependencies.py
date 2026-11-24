import subprocess
import unittest
from unittest.mock import Mock, patch

from src.downloads.dependencies import DependenciesMixin
from src.ui.dependency_dialog import DependencyDialogMixin


class DependencyApp(DependenciesMixin, DependencyDialogMixin):
    subprocess_flags = 0


class RequiredDependenciesTests(unittest.TestCase):
    def test_missing_or_broken_ffprobe_is_required_and_rechecked(self):
        app = DependencyApp()
        probe_result = FileNotFoundError('ffprobe')

        def run(command, **kwargs):
            if command[0] == 'ffprobe':
                if isinstance(probe_result, OSError):
                    raise probe_result
                return subprocess.CompletedProcess(command, probe_result)
            return subprocess.CompletedProcess(command, 0)

        with patch('src.downloads.dependencies.subprocess.run', side_effect=run) as runner:
            for probe_result, expected in ((FileNotFoundError('ffprobe'), ['ffprobe']),
                                           (1, ['ffprobe']), (0, [])):
                with self.subTest(probe_result=probe_result):
                    app.check_dependencies()
                    self.assertEqual(app.missing_deps, expected)
                    self.assertEqual(app.missing_pip_packages, [])
            self.assertIn(['ffprobe', '-version'], [c.args[0] for c in runner.call_args_list])

    def test_missing_ffprobe_shows_install_instructions_and_download_link(self):
        app = DependencyApp()
        app.root = Mock()
        app.log = Mock()
        app.log_retention_status = Mock()
        app.should_write_problem_logs = Mock(return_value=False)
        app.missing_deps = ['ffprobe']
        app.optional_missing_deps = []
        with patch('src.ui.dependency_dialog.ttk') as ttk:
            app.show_dependency_warning()

        self.assertTrue(any('ffprobe.exe' in c.args[0] and 'PATH' in c.args[0]
                            for c in app.log.call_args_list))
        self.assertIn('Сайт FFmpeg', [c.kwargs['text'] for c in ttk.Button.call_args_list])

    def test_recheck_explains_missing_ffprobe_and_recognizes_recovery(self):
        app = DependencyApp()
        app.log = Mock()
        available = False

        def run(command, **kwargs):
            missing = command[0] == 'ffprobe' and not available
            return subprocess.CompletedProcess(command, int(missing))

        with (patch('src.ui.dependency_dialog.subprocess.run', side_effect=run),
              patch('src.ui.dependency_dialog.messagebox') as dialogs):
            app.recheck_dependencies()
            warning = dialogs.showwarning.call_args.args[1]
            self.assertIn('ffprobe.exe', warning)
            self.assertIn('PATH', warning)

            available = True
            app.recheck_dependencies()
            dialogs.showinfo.assert_called_once()
            self.assertEqual(app.missing_deps, [])
