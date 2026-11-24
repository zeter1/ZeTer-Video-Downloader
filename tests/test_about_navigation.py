"""Exercise the in-window navigation without opening a desktop window."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.ui import layout


class AboutNavigationTests(unittest.TestCase):
    def test_about_reuses_the_download_page_and_builds_the_about_page_once(self):
        app = SimpleNamespace(
            root=Mock(), _studio_download_page=Mock(), _studio_about_page=None,
            _studio_about_nav=Mock(),
        )
        page = Mock()
        page.winfo_manager.side_effect = ["", "pack", ""]
        toggle = layout.UILayoutMixin._studio_toggle_about

        with patch.object(layout, "build_about_page", return_value=page, create=True) as build:
            toggle(app)
            build.assert_called_once_with(app.root)
            app._studio_download_page.pack_forget.assert_called_once_with()
            page.pack.assert_called_once_with(fill="both", expand=True)
            app._studio_about_nav.configure.assert_called_with(text="Загрузки")

            toggle(app)
            page.pack_forget.assert_called_once_with()
            app._studio_download_page.pack.assert_called_once_with(fill="both", expand=True)
            app._studio_about_nav.configure.assert_called_with(text="О программе")

            toggle(app)
            build.assert_called_once()
            self.assertEqual(page.pack.call_count, 2)
