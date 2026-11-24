"""Native queue view. Only the Tk thread renders worker status."""

from collections import deque
from functools import lru_cache
from queue import Empty, Queue
from threading import Thread
from tkinter import ttk
from time import time
import re
import tkinter as tk

from src.core.constants import LOG_TAB_ERRORS, MAX_URLS_PER_SESSION
from src.downloads.queue_state import read_archived_video_titles
from src.downloads.urls import download_url_issue
from src.ui.queue_rows import QueueRows, open_video_link
from src.ui.queue_thumbnails import QueueThumbnails


def download_speed(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(B|[KMG]i?B)/s", value)
    if not match:
        return 0.0
    amount, unit = match.groups()
    return float(amount) * {"B": 1, "KB": 1000, "KiB": 1024, "MB": 1000**2,
                            "MiB": 1024**2, "GB": 1000**3, "GiB": 1024**3}[unit]



STATUS = {
    "waiting": "В очереди", "starting": "Подготовка", "downloading": "Загрузка",
    "video": "Видео готово", "audio": "Создание MP3", "done": "MP4 и MP3 готовы",
    "skipped": "Уже скачано", "failed": "Не скачалось", "audio_error": "Ошибка MP3",
    "problematic": "Пропущено", "cancelled": "Остановлено", "unfinished": "Не завершено",
    "invalid": "Ссылка не подходит",
}
READY = {"video", "done", "skipped"}
ERRORS = {"failed", "audio_error", "problematic", "unfinished", "invalid"}
RETRY = {"failed", "problematic", "cancelled", "unfinished"}
FILTERS = (
    ("Все", set(STATUS)),
    ("В работе", {"waiting", "starting", "downloading", "audio", "cancelled"}),
    ("Готовые", READY),
    ("Ошибки", ERRORS),
)


class DownloadDashboardMixin:
    def initialize_download_dashboard(self):
        parent = self.studio_queue_parent
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        header = ttk.Frame(parent, padding=(14, 11, 14, 7))
        header.grid(sticky="ew")
        ttk.Label(header, text="Очередь файлов", style="CardTitle.TLabel").pack(side="left")
        self._dashboard_search = tk.StringVar(master=self.root)
        search = ttk.Entry(header, textvariable=self._dashboard_search, width=15)
        search.pack(side="right")
        ttk.Label(header, text="Поиск", style="Muted.TLabel").pack(side="right", padx=7)
        toolbar = ttk.Frame(parent, padding=(12, 0, 12, 8))
        toolbar.grid(row=1, sticky="ew")
        self._dashboard_filter = tk.StringVar(master=self.root, value="Все")
        self._dashboard_tabs = {}
        for label, _ in FILTERS:
            tab = ttk.Radiobutton(toolbar, text=label, value=label, variable=self._dashboard_filter,
                                  command=self.refresh_download_dashboard)
            tab.pack(side="left", padx=(0, 8))
            self._dashboard_tabs[label] = tab

        parent.rowconfigure(1, weight=0)
        parent.rowconfigure(2, weight=1)
        self._thumbnails = QueueThumbnails()
        self.root.bind("<Destroy>", self._close_dashboard, add="+")
        self._dashboard_table = QueueRows(parent, self._open_dashboard_row,
                                         self._thumbnails.images.get, self._thumbnails.request)
        self._dashboard_table.grid(row=2, column=0, sticky="nsew")
        foot = ttk.Frame(parent, padding=(14, 6))
        foot.grid(row=3, column=0, sticky="ew")
        self._dashboard_hint = tk.StringVar(master=self.root)
        ttk.Label(foot, textvariable=self._dashboard_hint, style="Muted.TLabel").pack(side="left")
        ttk.Button(foot, text="Очистить очередь",
                   command=self._studio_confirm_clear_queue).pack(side="left", padx=(12, 0))
        self._dashboard_rows = {}
        self._dashboard_last = None
        self._dashboard_draft = None
        self._dashboard_identity = lru_cache(maxsize=2048)(self.url_identity)
        self._dashboard_archive_titles = {}
        self._dashboard_session_titles = {}
        self._dashboard_title_results = Queue(maxsize=1)
        archive_dir, identity = self.download_archive_dir, self.url_identity
        results = self._dashboard_title_results

        def load_titles():
            results.put_nowait(read_archived_video_titles(archive_dir, identity))

        Thread(target=load_titles, name="queue-archive-titles", daemon=True).start()
        self._speed_samples = deque(maxlen=52)
        self._speed_session = None
        self._speed_sample_time = 0
        self._dashboard_search.trace_add("write", self.refresh_download_dashboard)

    def _close_dashboard(self, event):
        if event.widget is self.root:
            self._thumbnails.closed.set()

    def _dashboard_draft_rows(self, session):
        content = self.url_text.get("1.0", "end-1c")
        skip_downloaded = self.skip_downloaded_enabled()
        key = (content, skip_downloaded, session)
        if self._dashboard_draft is None or self._dashboard_draft[0] != key:
            entries = []
            seen = set()
            for line in content.splitlines():
                url = line.strip()
                if not url:
                    continue
                issue = download_url_issue(url)
                identity = url if issue else self._dashboard_identity(url)
                if identity in seen:
                    continue
                seen.add(identity)
                status = "invalid" if issue else "waiting"
                if not issue and skip_downloaded and self.is_url_downloaded(url):
                    status = "skipped"
                entries.append((url, identity, status, issue))
                if len(entries) == MAX_URLS_PER_SESSION:
                    break
            self._dashboard_draft = (key, entries)
        return self._dashboard_draft[1]

    def refresh_download_dashboard(self, *_):
        if not hasattr(self, "_dashboard_table") or getattr(self, "_closing_download_window", False):
            return
        session = getattr(self, "_dashboard_session", None)
        working = self.is_downloading
        if session is None:
            phase, state, events = "Готово к загрузке", (), ()
        else:
            phase, state, events = session.snapshot()

        if working or session is not None:
            current, total = self.completed_files.value(), self.total_files.value()
            self.main_progress["value"] = current
            self.progress_percent.configure(text=f"{int(100 * current / total) if total else 0}%")
            phase += f"   ·   Видео обработано: {current} из {total}" if total else ""
        self.studio_session_status.set(phase)
        if events:
            self.studio_recent_text.set("\n".join(f"•  {event}" for event in events))

        if self._dashboard_title_results is not None:
            try:
                self._dashboard_archive_titles = self._dashboard_title_results.get_nowait()
            except Empty:
                pass
            else:
                self._dashboard_title_results = None
        identity_for = self._dashboard_identity
        titles = {identity_for(video["url"]): video["title"] for video in self.video_list}
        self._dashboard_session_titles.update(
            (identity_for(url), name) for url, (_, name) in state if name
        )

        def display_title(key):
            return (titles.get(key) or self._dashboard_session_titles.get(key)
                    or self._dashboard_archive_titles.get(key) or "Название недоступно")

        rows = {url: (status, display_title(identity_for(url))) for url, (status, _) in state}
        if working:
            self._dashboard_draft = None
        else:
            for url, identity, status, issue in self._dashboard_draft_rows(session):
                if url not in rows or rows[url][0] == "skipped":
                    rows[url] = status, issue or display_title(identity)

        now = time()
        with self.ytdlp_runtime_lock:
            progress = {item["url"]: (item.get("display_percent"), item.get("display_speed", ""))
                        for item in self.ytdlp_runtime.values()
                        if item.get("url") and now - item.get("display_at", 0) < 5}
        self._draw_dashboard_speed(session, progress, now, working)
        thumbnails_changed = self._thumbnails.collect(self.root)
        term = self._dashboard_search.get().strip().casefold()
        allowed = dict(FILTERS)[self._dashboard_filter.get()]
        visible = tuple((url, label, status, progress.get(url, (None, ""))[0] if status == "downloading" else None)
                        for url, (status, label) in rows.items()
                        if status in allowed and (term in label.casefold() or term in url.casefold()))
        totals = tuple(sum(status in allowed for status, label in rows.values())
                       for _, allowed in FILTERS)
        key = visible, totals, working
        if key == self._dashboard_last:
            if thumbnails_changed:
                self._dashboard_table.redraw()
            return
        self._dashboard_last = key
        for (label, _), count in zip(FILTERS, totals):
            self._dashboard_tabs[label].configure(text=f"{label} {count}")
        active = sum(status == "downloading" for status, _ in rows.values())
        self._dashboard_hint.set(f"Загружается {active} из {self.max_concurrent}" if working else
                                 f"Файлов: {len(visible)}" if visible else
                                 "Нет файлов по фильтру." if rows else "Добавьте ссылки или плейлист.")
        self._dashboard_rows = rows
        self._dashboard_table.update_rows(tuple(
            (url, title, STATUS[status],
             ("#286343", "#e7f2ec") if status in READY else
             ("#91431f", "#faede5") if status in ERRORS else
             ("#3a611d", "#e9f4d9") if status in {"downloading", "audio"} else ("#515a50", "#f0f1ee"),
             max(0, min(100, pct)) if pct is not None else None,
             self.extract_video_id(url) if status in {"downloading", "video", "audio", "done"}
             and self.is_youtube_url(url) else None,
             "↻" if status in RETRY and not working else "!" if status in {"audio_error", "invalid"} else None)
            for url, title, status, pct in visible
        ))

    def _draw_dashboard_speed(self, session, progress, now, working):
        graph = self.studio_speed_graph
        if session is not self._speed_session:
            self._speed_session = session
            self._speed_samples.clear()
        if now - self._speed_sample_time < 0.8:
            return
        self._speed_sample_time = now
        speed = sum(download_speed(label) for _, label in progress.values()) if working else 0.0
        if speed or (working and self._speed_samples):
            self._speed_samples.append(speed)
        self.studio_speed_text.set(
            f"{speed / 1024**2:.1f} МиБ/с" if speed >= 1024**2 else
            f"{speed / 1024:.0f} КиБ/с" if speed else ""
        )
        graph.delete("speed")
        peak = max(self._speed_samples, default=0)
        if peak:
            width, height = graph.winfo_width(), graph.winfo_height()
            step = max(1, width / self._speed_samples.maxlen)
            for i, sample in enumerate(reversed(self._speed_samples)):
                x = width - (i + 1) * step
                graph.create_rectangle(x, height - sample / peak * (height - 2),
                                       x + max(1, step - 2), height, fill="#a6ce77", outline="",
                                       tags="speed")

    def _open_dashboard_row(self, url):
        if url not in self._dashboard_rows:
            return
        status, _ = self._dashboard_rows[url]
        if status == "audio_error":
            self.show_studio_log(LOG_TAB_ERRORS)
        elif status == "invalid":
            self.show_studio_links()
        elif status in RETRY and not self.is_downloading:
            self._return_dashboard_link(url)
        else:
            open_video_link(url)

    def _return_dashboard_link(self, url):
        if all(self.url_identity(line) != self.url_identity(url) for line in self.get_urls_from_textbox()):
            self.add_urls_with_check([url])
        self.show_studio_links()
