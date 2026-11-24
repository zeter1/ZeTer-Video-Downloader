"""Закрытие окна и ожидание фоновых потоков.

Автоматически выделено из прежнего модуля ui_components.py без изменения тел методов.
"""

from tkinter import messagebox
import time


class WindowLifecycleMixin:
    def on_closing(self) -> None:
        if self._closing_download_window:
            return
        was_active = self.is_downloading
        if was_active and not messagebox.askyesno(
            "Закрытие программы",
            "Скачивание или конвертация ещё идут. Нажмите «Нет», чтобы дождаться завершения.\n\n"
            "Приостановить обработку и выйти? При следующем запуске программа продолжит очередь.",
        ):
            return
        if not self.save_queue_draft() and not messagebox.askyesno(
            "Очередь скачивания",
            "Не удалось сохранить последние изменения списка. Всё равно закрыть программу?",
        ):
            return

        self._closing_download_window = True
        if self._resume_callback is not None:
            self.root.after_cancel(self._resume_callback)
            self._resume_callback = None
        if was_active:
            self.cancel_flag.set()
            self.log("🛑 Приостанавливаю скачивание перед закрытием...", "WARNING")
        self.save_download_links_session(
            "закрытие программы во время загрузки" if was_active else "закрытие программы"
        )
        self.save_current_settings()
        self._close_after_download(was_active, time.monotonic() + 30)

    def _close_after_download(self, was_active, deadline):
        worker = self._download_manager_thread
        if worker is not None and worker.is_alive() and time.monotonic() < deadline:
            self.root.after(100, self._close_after_download, was_active, deadline)
            return
        self.finalize_problem_diagnostics(
            "finished", "application_closed", {"download_was_active": was_active},
        )
        self.root.destroy()
