"""Сохранение и ограничение истории очередей ссылок.

Автоматически выделено из прежнего модуля download_workflow.py без изменения тел методов.
"""

from typing import List
from typing import Optional
from pathlib import Path
from datetime import datetime
import tkinter as tk

class DownloadLinkSessionsMixin:
    def get_urls_from_textbox(self) -> List[str]:
        """Возвращает все непустые строки из поля ссылок без изменения порядка."""
        try:
            raw_text = self.url_text.get("1.0", tk.END)
        except Exception as e:
            self.record_problem(
                "Не удалось прочитать поле ссылок для сохранения сессии",
                "WARNING", "get_urls_from_textbox", exception=e
            )
            return []

        urls: List[str] = []
        for line in raw_text.splitlines():
            clean = line.strip()
            if clean:
                urls.append(clean)
        return urls


    def save_download_links_session(self, reason: str = "закрытие программы",
                                    *, urls: Optional[List[str]] = None) -> Optional[Path]:
        """
        Сохраняет текущую очередь ссылок в отдельный .txt-файл.
        Это страховка на случай случайного закрытия программы: введённые ссылки
        можно восстановить из папки «Ссылки на скачивания».
        """
        if urls is None:
            urls = self.get_urls_from_textbox()
        if not urls:
            return None

        try:
            session_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            links_file = self.make_unique_session_file(
                self.download_links_dir, "links", session_ts
            )

            header = (
                f"Ссылки на скачивания\nСессия: {session_ts}\n"
                f"Причина сохранения: {reason}\nКоличество строк: {len(urls)}\n"
                + "=" * 80 + "\n\n"
            )
            self._atomic_write_text_file(links_file, header + "\n".join(urls) + "\n")
            self.log(
                f"💾 Ссылки из очереди сохранены: {links_file.name} "
                f"(строк: {len(urls)})",
                "DOWNLOAD_LINKS_FOLDER_LINK"
            )
            return links_file

        except Exception as e:
            self.log(f"❌ Не удалось сохранить ссылки из очереди: {e}", "ERROR")
            self.record_problem(
                "Не удалось сохранить текущие ссылки на скачивания в отдельный файл",
                "ERROR", "save_download_links_session",
                {
                    "reason": reason,
                    "url_count": len(urls),
                    "download_links_dir": str(self.download_links_dir),
                },
                e
            )
            return None
