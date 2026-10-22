"""Tkinter update controls. All HTTP and file I/O happens on worker threads."""
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox
import webbrowser

from src.core.app_updates import (
    RELEASES_PAGE, check_for_update, download_verified, installed_build_tag,
    start_verified_replacement,
)


class UpdateControlsMixin:
    def _save_auto_update_preference(self):
        self.settings["auto_check_updates"] = bool(self.auto_check_updates_var.get())
        self.save_settings()

    def _update_worker(self, task, finished):
        """Poll a queue on the Tk thread instead of calling Tk from a worker."""
        if getattr(self, "_updater_busy", False):
            return
        self._updater_busy = True
        results = queue.Queue(maxsize=1)

        def execute():
            try:
                results.put((True, task()))
            except Exception as exc:
                results.put((False, exc))

        threading.Thread(target=execute, daemon=True, name="app-update-worker").start()

        def poll():
            try:
                success, value = results.get_nowait()
            except queue.Empty:
                self.root.after(200, poll)
                return
            self._updater_busy = False
            finished(success, value)

        self.root.after(200, poll)

    def check_app_updates(self, automatic=False):
        if automatic and not self.settings.get("auto_check_updates", True):
            return
        tag = installed_build_tag()
        if not tag:
            if not automatic:
                messagebox.showinfo(
                    "Обновления", "Эта копия не содержит идентификатор официальной сборки. "
                    "Автоматическая замена отключена. Откройте GitHub Releases для установки."
                )
                webbrowser.open(RELEASES_PAGE)
            return

        def finished(success, value):
            if not success:
                self.file_logger.warning("Update check failed: %s", value)
                if not automatic:
                    messagebox.showwarning("Обновления", f"Не удалось проверить обновления: {value}")
                return
            status, release = value
            if status == "available" and release:
                if messagebox.askyesno(
                    "Новая версия доступна",
                    f"Установлена: {tag}\nДоступна: {release['tag']}\n\n"
                    "Скачать проверенный EXE и установить после закрытия программы?"
                ):
                    self._download_app_update(release)
            elif not automatic:
                if status == "current":
                    messagebox.showinfo("Обновления", f"Установлена актуальная сборка {tag}.")
                else:
                    messagebox.showinfo(
                        "Обновления", "Невозможно надёжно сравнить сборки. "
                        "Откройте страницу релизов для ручной установки."
                    )
                    webbrowser.open(RELEASES_PAGE)

        self._update_worker(lambda: check_for_update(tag), finished)

    def _download_app_update(self, release):
        if self.is_downloading:
            messagebox.showwarning(
                "Обновление", "Дождитесь завершения скачивания видео, затем повторите обновление."
            )
            return
        self.log(f"⬇️ Загрузка обновления {release['tag']} и проверка SHA-256...", "INFO")
        folder = self.app_dir / "Обновления" / release["tag"]

        def finished(success, result):
            if not success:
                self.file_logger.warning("Update download failed: %s", result)
                messagebox.showerror("Обновление", f"Не удалось скачать/проверить обновление: {result}")
                return
            if self.is_downloading:
                messagebox.showwarning(
                    "Обновление", "Обновление проверено, но идёт загрузка видео. "
                    "Повторите проверку после завершения задачи."
                )
                return
            if messagebox.askyesno(
                "Установка обновления",
                "EXE скачан, SHA-256 совпал. Закрыть программу и установить "
                f"{release['tag']}? Настройки, логи и скачанные файлы сохранятся."
            ):
                try:
                    start_verified_replacement(result, sys.executable, release["digest"])
                except Exception as exc:
                    self.file_logger.exception("Unable to launch application updater")
                    messagebox.showerror("Обновление", f"Не удалось запустить установку: {exc}")
                    return
                self.on_closing()

        self._update_worker(lambda: download_verified(release, folder), finished)
