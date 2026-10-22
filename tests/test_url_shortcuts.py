"""Контракт клавиш поля ссылок без запуска Tk и доступа к буферу Windows."""

from types import SimpleNamespace
import unittest

from src.ui.clipboard import ClipboardMixin


class UrlShortcutTests(unittest.TestCase):
    def dispatch(self, keysym, keycode=0, state=4):
        events = []
        widget = SimpleNamespace(event_generate=events.append)
        event = SimpleNamespace(widget=widget, keysym=keysym, keycode=keycode, state=state)
        result = ClipboardMixin()._on_url_clipboard_shortcut(event)
        return result, events

    def test_each_layout_dispatches_exactly_one_standard_action(self):
        cases = (
            ("c", 0, "<<Copy>>"), ("с", 0, "<<Copy>>"),
            ("Cyrillic_es", 0, "<<Copy>>"), ("unknown", 67, "<<Copy>>"),
            ("x", 0, "<<Cut>>"), ("ч", 0, "<<Cut>>"),
            ("Cyrillic_che", 0, "<<Cut>>"), ("unknown", 88, "<<Cut>>"),
            ("a", 0, "<<SelectAll>>"), ("ф", 0, "<<SelectAll>>"),
            ("Cyrillic_ef", 0, "<<SelectAll>>"), ("unknown", 65, "<<SelectAll>>"),
            ("v", 0, "<<Paste>>"), ("м", 0, "<<Paste>>"),
            ("Cyrillic_em", 0, "<<Paste>>"), ("unknown", 86, "<<Paste>>"),
        )
        for keysym, keycode, expected in cases:
            for symbol in (keysym, keysym.upper()):
                with self.subTest(symbol=symbol, keycode=keycode):
                    # Windows Tk maps NumLock to Mod1 (0x8), not Alt.
                    for state in (4, 6, 12, 14):
                        with self.subTest(state=state):
                            self.assertEqual(self.dispatch(symbol, keycode, state),
                                             ("break", [expected]))

    def test_unrelated_keys_and_unsupported_modifiers_pass_through(self):
        for state in (0, 1, 8, 20, 0x20004):
            for symbol, code in (("c", 67), ("x", 88), ("a", 65), ("v", 86)):
                with self.subTest(state=state, symbol=symbol):
                    self.assertEqual(self.dispatch(symbol, code, state), (None, []))
        self.assertEqual(self.dispatch("z", 90), (None, []))


if __name__ == "__main__":
    unittest.main()
