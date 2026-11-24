import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from src.core.settings import SettingsMixin
from src.diagnostics.retention_storage import RetentionStorageMixin


class SettingsFixture(SettingsMixin, RetentionStorageMixin):
    pass


class SettingsPersistenceTests(unittest.TestCase):
    def test_audio_quality_is_preserved_and_unknown_values_fall_back(self):
        with TemporaryDirectory() as directory:
            app = SettingsFixture()
            app.settings_file = Path(directory) / "settings.json"
            for old, expected in (("256k", "256k"), ("192", "192k"), ("VBR-0 (лучшее)", "VBR-0 (лучшее)"),
                                  ("arbitrary", "128k"), ([], "128k")):
                with self.subTest(old=old):
                    app.settings_file.write_text(json.dumps({"audio_quality": old}), encoding="utf-8")
                    app.load_settings()
                    self.assertEqual(app.settings["audio_quality"], expected)

    def test_replace_failure_preserves_previous_settings_and_success_is_readable(self):
        with TemporaryDirectory(prefix='Настройки приложения ') as directory:
            app = SettingsFixture()
            app.settings_file = Path(directory) / 'settings.json'
            previous = {'skip_downloaded': True, 'save_path': str(Path(directory) / 'Видео')}
            app.settings_file.write_text(json.dumps(previous), encoding='utf-8')
            original = app.settings_file.read_bytes()
            app.settings = {**previous, 'skip_downloaded': False}
            app.log = Mock()

            with patch.object(app, '_replace_atomic_temp_file', side_effect=PermissionError('blocked')):
                app.save_settings()
            self.assertEqual(app.settings_file.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [app.settings_file])
            app.log.assert_called_once()

            app.save_settings()
            reader = SettingsFixture()
            reader.settings_file = app.settings_file
            reader.load_settings()
            self.assertFalse(reader.settings['skip_downloaded'])
            self.assertEqual(reader.settings['save_path'], previous['save_path'])
