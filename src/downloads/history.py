"""История уже скачанных URL/video_id и защита от повторов.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import Optional
import hashlib
import urllib.request
import urllib.parse

from src.core.session_settings import session_setting
from src.downloads.urls import download_url_issue


class DownloadHistoryMixin:
    def _download_history_files(self, *, machine_log):
        if machine_log:
            dirs = (self.downloaded_sessions_dir, *getattr(self, "legacy_downloaded_sessions_dirs", ()))
            pattern = "downloaded_log_*.txt"
        else:
            dirs = (self.history_dir, *getattr(self, "legacy_history_dirs", ()))
            pattern = "downloaded_*.txt"
        for directory in dirs:
            yield from sorted(directory.glob(pattern))


    def load_download_log(self) -> None:
        """Read recent history, including files that could not be moved yet."""
        cutoff = self.retention_cutoff()
        for path in self._download_history_files(machine_log=True):
            try:
                if not self.is_recent_log_file(path, cutoff):
                    continue
                with path.open(encoding="utf-8") as file:
                    for line in file:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split("\t", 3)
                        if len(parts) == 4 and not download_url_issue(parts[3]):
                            # Derive keys from the original URL; older versions could
                            # record a YouTube ID from another site's query string.
                            self.remember_downloaded_url_in_memory(parts[3])
                            continue
                        url_hash = parts[0].strip()
                        if url_hash:
                            self.downloaded_url_hashes.add(url_hash)
                        if len(parts) > 1 and parts[1].strip() not in ("", "-"):
                            self.downloaded_video_ids.add(parts[1].strip())
                        if len(parts) > 2:
                            self.remember_downloaded_url_in_memory(parts[2].strip())
            except (OSError, UnicodeError) as error:
                self.file_logger.error("load_download_log (session file): %s", error)

        for path in self._download_history_files(machine_log=False):
            try:
                if not self.is_recent_log_file(path, cutoff):
                    continue
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                numbered_history = bool(lines) and lines[0] == "Скачанные видео"
                for line in lines:
                    number, _, url = line.strip().partition(". ")
                    if (numbered_history and number.isdecimal() and url.lower().startswith(("http://", "https://"))
                            and not any(char.isspace() for char in url)):
                        self.remember_downloaded_url_in_memory(url)
                    else:
                        for legacy_url in self.extract_urls_from_text(line):
                            self.remember_downloaded_url_in_memory(legacy_url)
            except OSError as error:
                self.file_logger.error("load_download_log (history file): %s", error)


    def save_download_log(self, url: str, *, if_missing: bool = False) -> bool:
        """
        Сохранение URL и video_id в сессионные логи.
        Новые записи идут по одному файлу на сессию и хранятся
        DOWNLOAD_HISTORY_RETENTION_DAYS дней.
        Старые общие download_log.txt/video_ids.txt не используются: в них нет дат по строкам.
        """
        normalized_url = self.normalize_url_for_history(url)
        url_hash = hashlib.md5(normalized_url.encode()).hexdigest()
        video_id = self.extract_video_id(url)

        with self.file_lock:
            if if_missing and self.is_url_downloaded(url):
                return True
            history_written = False
            try:
                history_file, session_log_file = self.ensure_download_session_files()
                row = self.current_session_download_count + 1

                with open(history_file, 'a', encoding='utf-8') as f:
                    f.write(f"{row}. {url}\n")
                self.current_session_download_count = row
                history_written = True
                self.remember_downloaded_url_in_memory(url)

                with open(session_log_file, 'a', encoding='utf-8') as f:
                    f.write(f"{url_hash}\t{video_id or '-'}\t{normalized_url}\t{url}\n")
            except Exception as e:
                self.file_logger.error(f"save_download_log (session): {e}")
                self.record_problem(
                    "Не удалось записать скачанное видео в сессионные логи",
                    "ERROR", "save_download_log",
                    {
                        "url": url,
                        "url_hash": url_hash,
                        "normalized_url": normalized_url,
                        "video_id": video_id,
                        "history_file": str(self.current_history_session_file),
                        "session_log_file": str(self.current_download_log_session_file),
                    },
                    e
                )
            return history_written


    def is_url_downloaded(self, url: str) -> bool:
        if not url:
            return False
        normalized_url = self.normalize_url_for_history(url)
        url_hash = hashlib.md5(normalized_url.encode()).hexdigest()
        if url_hash in self.downloaded_url_hashes:
            return True
        # Совместимость со старыми hash, где считали прямо от введённой строки.
        raw_hash = hashlib.md5(url.strip().encode()).hexdigest()
        if raw_hash in self.downloaded_url_hashes:
            return True
        video_id = self.extract_video_id(url)
        if video_id and video_id in self.downloaded_video_ids:
            return True
        return False


    def skip_downloaded_enabled(self) -> bool:
        try:
            return session_setting(self, "skip_downloaded") is not False
        except (AttributeError, KeyError):
            return True


    def should_skip_downloaded(self, url: str) -> bool:
        return self.skip_downloaded_enabled() and self.is_url_downloaded(url)


    def is_youtube_url(self, url: str) -> bool:
        try:
            host = (urllib.parse.urlparse(url.strip()).hostname or "").lower().rstrip(".")
            return host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be"
        except Exception:
            return False


    def run_yt_dlp_with_download_slot(self, url: str) -> Optional[str]:
        # Раньше здесь был глобальный Lock на YouTube: даже если в настройках стояло
        # 2-3 потока, реально качался только 1 ролик, а остальные висели на строке
        # «Жду свободный слот YouTube». Это делало большие очереди очень медленными.
        # Теперь параллельность контролирует только ThreadPoolExecutor через настройку
        # «Потоков», а устойчивость даёт список fallback-стратегий внутри method_yt_dlp.
        return self.method_yt_dlp(url)
