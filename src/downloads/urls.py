"""Разбор, нормализация, дедупликация и валидация URL.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
import hashlib
from tkinter import messagebox
import re
import tkinter as tk
import urllib.request
import urllib.parse


DOWNLOAD_DOMAINS = ('youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com')
_YOUTUBE_VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')


def download_url_issue(url: str) -> str:
    """An empty result means the link can enter the download queue."""
    url = (url or '').strip()
    if any(char.isspace() or ord(char) < 32 for char in url):
        return 'В ссылке есть пробелы или управляющие символы'
    try:
        address = urllib.parse.urlparse(url)
        host = (address.hostname or '').lower().rstrip('.')
    except ValueError:
        return 'Некорректная ссылка'
    if address.scheme not in ('http', 'https') or not host:
        return 'Нужна ссылка HTTP или HTTPS'
    if not any(host == domain or host.endswith('.' + domain) for domain in DOWNLOAD_DOMAINS):
        return 'Сайт не поддерживается'
    return ''


class UrlQueueMixin:
    def extract_video_id(self, url: str) -> Optional[str]:
        url = (url or '').strip()
        if any(char.isspace() or ord(char) < 32 for char in url):
            return None
        try:
            address = urllib.parse.urlsplit(url)
            host = (address.hostname or '').lower().rstrip('.')
        except ValueError:
            return None
        if address.scheme not in ('http', 'https'):
            return None

        path = address.path.removesuffix('/')
        if host == 'youtu.be':
            candidate = urllib.parse.unquote(path.removeprefix('/'))
        elif host == 'youtube.com' or host.endswith('.youtube.com'):
            if path == '/watch':
                parameters = urllib.parse.parse_qs(address.query, keep_blank_values=True)
                candidate = parameters.get('v', [''])[0]
            else:
                route, _, segment = path.removeprefix('/').partition('/')
                if route not in ('embed', 'v', 'shorts', 'live'):
                    return None
                candidate = urllib.parse.unquote(segment)
        else:
            return None
        return candidate if _YOUTUBE_VIDEO_ID.fullmatch(candidate) else None


    def extract_urls_from_text(self, text: str) -> List[str]:
        """Достаёт URL из человекочитаемых history-файлов и логов."""
        if not text:
            return []
        urls: List[str] = []
        for match in re.findall(r'https?://[^\s<>"\']+', text):
            urls.append(match.rstrip('.,);]'))
        return urls


    def normalize_url_for_history(self, url: str) -> str:
        """
        Канонический ключ истории.
        Для YouTube разные варианты одной ссылки (watch, youtu.be, shorts, лишние
        параметры плейлиста/таймкода) считаются одним и тем же видео.
        """
        url = (url or "").strip()
        video_id = self.extract_video_id(url)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"

        try:
            parsed = urllib.parse.urlparse(url)
            scheme = (parsed.scheme or "https").lower()
            host = (parsed.hostname or "").lower().rstrip(".")
            path = urllib.parse.urlunparse(("", "", parsed.path or "", "", "", ""))

            # Для не-YouTube сохраняем полезные параметры, но убираем мусорные трекеры.
            drop_prefixes = ("utm_",)
            drop_exact = {"fbclid", "gclid", "si", "feature", "pp", "ab_channel"}
            query_pairs = []
            for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
                key_l = key.lower()
                if key_l in drop_exact or any(key_l.startswith(p) for p in drop_prefixes):
                    continue
                query_pairs.append((key, value))
            query = urllib.parse.urlencode(query_pairs, doseq=True)
            return urllib.parse.urlunparse((scheme, host, path, "", query, ""))
        except Exception:
            return url


    def url_identity(self, url: str) -> str:
        video_id = self.extract_video_id(url)
        if video_id:
            return f"youtube:{video_id}"
        return f"url:{self.normalize_url_for_history(url)}"


    def deduplicate_urls_by_identity(self, urls: List[str]) -> Tuple[List[str], List[str]]:
        """Убирает повторы внутри очереди, включая разные YouTube-ссылки на один video_id."""
        unique: List[str] = []
        duplicates: List[str] = []
        seen: Set[str] = set()
        for url in urls:
            clean = (url or "").strip()
            if not clean:
                continue
            key = self.url_identity(clean)
            if key in seen:
                duplicates.append(clean)
                continue
            seen.add(key)
            unique.append(clean)
        return unique, duplicates


    def remember_downloaded_url_in_memory(self, url: str) -> None:
        """Добавляет URL в память истории без записи на диск. Вызывать можно при загрузке логов."""
        if not url:
            return
        normalized = self.normalize_url_for_history(url)
        if normalized:
            self.downloaded_url_hashes.add(hashlib.md5(normalized.encode()).hexdigest())
        raw = url.strip()
        if raw and raw != normalized:
            # Совместимость со старыми session-логами, где hash считался от сырой строки.
            self.downloaded_url_hashes.add(hashlib.md5(raw.encode()).hexdigest())
        video_id = self.extract_video_id(url)
        if video_id:
            self.downloaded_video_ids.add(video_id)


    def add_urls_with_check(self, urls: List[str], *, check_history: bool = True) -> int:
        already_downloaded: List[str] = []
        new_urls: List[str] = []
        duplicate_in_batch: List[str] = []
        # Дедупликация внутри входного списка: одинаковый YouTube video_id = один ролик,
        # даже если ссылки выглядят по-разному.
        seen_in_batch: Set[str] = set()

        for url in urls:
            url = url.strip()
            if not url:
                continue
            identity = self.url_identity(url)
            if identity in seen_in_batch:
                duplicate_in_batch.append(url)
                continue
            seen_in_batch.add(identity)

            if check_history and self.should_skip_downloaded(url):
                already_downloaded.append(url)
            else:
                new_urls.append(url)

        if duplicate_in_batch:
            self.log(f"ℹ️ Убрано {len(duplicate_in_batch)} дублей внутри добавляемого списка", "INFO")

        if already_downloaded:
            msg = f"⚠️ {len(already_downloaded)} видео уже были скачаны и НЕ добавлены в очередь:\n\n"
            for u in already_downloaded[:8]:
                msg += f"• {u[:90]}\n"
            if len(already_downloaded) > 8:
                msg += f"... и ещё {len(already_downloaded) - 8} видео\n"
            msg += "\nДля повторной загрузки снимите галочку «Пропускать скачанные видео»."
            messagebox.showwarning("Видео уже скачаны", msg)
            self.log(f"⚠️ Заблокировано {len(already_downloaded)} уже скачанных URL", "WARNING")

        if new_urls:
            current_text = self.url_text.get("1.0", tk.END).strip()
            if current_text:
                self.url_text.insert(tk.END, "\n" + "\n".join(new_urls))
            else:
                self.url_text.insert(tk.END, "\n".join(new_urls))
            self.log(f"✅ Добавлено {len(new_urls)} новых URL", "SUCCESS")

        return len(new_urls)


    def validate_url(self, url: str) -> bool:
        issue = download_url_issue(url)
        if issue:
            self.log(f'⚠️ {issue}', 'WARNING')
        return not issue
