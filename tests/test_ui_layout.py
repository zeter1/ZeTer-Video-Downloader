"""Regression checks for the main-window layout without a Tk display."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.ui import layout


class SidebarLayoutTests(unittest.TestCase):
    def setUp(self):
        self.sidebar = Mock()
        self.sidebar.winfo_height.return_value = 861
        self.content = Mock()
        self.content.winfo_reqheight.return_value = 555
        self.content.winfo_reqwidth.return_value = 245
        self.content.winfo_rooty.return_value = 100
        self.button = Mock()
        self.button.winfo_rooty.return_value = 620
        self.button.winfo_height.return_value = 25
        self.content.winfo_children.return_value = [self.button]
        self.button.winfo_children.return_value = []
        self.bar = Mock()
        self.bar.winfo_reqwidth.return_value = 15
        self.page = Mock()
        self.page.columnconfigure.return_value = 0
        self.owner = Mock(spec=layout.UILayoutMixin)
        with (
            patch.object(layout.ttk, "Frame", side_effect=[self.sidebar, self.content]),
            patch.object(layout.ttk, "Scrollbar", return_value=self.bar) as make_bar,
            patch.object(layout.tk, "Canvas", side_effect=AssertionError("sidebar uses a canvas window")),
        ):
            layout.UILayoutMixin._studio_build_sidebar(self.owner, self.page)
        self.scroll = make_bar.call_args.kwargs["command"]
        self.resize = next(call.args[1] for call in self.sidebar.bind.call_args_list
                           if call.args[0] == "<Configure>")

    def test_both_cards_are_placed_directly_in_the_sidebar_frame(self):
        self.owner._studio_build_events.assert_called_once_with(self.content)
        self.owner._studio_build_settings.assert_called_once_with(self.content)
        self.assertEqual(self.content.place.call_args.kwargs["relwidth"], 1)
        self.assertEqual(self.content.place.call_args.kwargs["y"], 0)
        self.resize(None)
        self.bar.place_forget.assert_called()
        self.bar.set.assert_called_with(0, 1)

    def test_column_leaves_room_for_settings_and_scrollbar(self):
        self.resize(None)
        self.page.columnconfigure.assert_called_with(1, minsize=260)
        self.page.columnconfigure.reset_mock(return_value=True)
        self.page.columnconfigure.return_value = 260
        self.resize(None)
        self.page.columnconfigure.assert_called_once_with(1, "minsize")

    def test_compact_sidebar_scrolls_to_the_end_and_resets_when_enlarged(self):
        self.sidebar.winfo_height.return_value = 432
        self.resize(None)
        self.bar.place.assert_called()
        self.scroll("moveto", "1")
        self.content.place_configure.assert_called_with(y=-123, width=-15)
        self.assertEqual(self.bar.set.call_args.args[1], 1)
        self.sidebar.winfo_height.return_value = 861
        self.resize(None)
        self.content.place_configure.assert_called_with(y=0, width=0)
        self.bar.place_forget.assert_called()
        self.scroll("scroll", "1", "units")
        self.content.place_configure.assert_called_with(y=0, width=0)

    def test_keyboard_focus_reveals_the_bottom_button_in_a_compact_window(self):
        self.sidebar.winfo_height.return_value = 432
        self.resize(None)
        reveal = next(call.args[1] for call in self.button.bind.call_args_list
                      if call.args[0] == "<FocusIn>")
        reveal(SimpleNamespace(widget=self.button))
        self.content.place_configure.assert_called_with(y=-113, width=-15)


if __name__ == "__main__":
    unittest.main()
