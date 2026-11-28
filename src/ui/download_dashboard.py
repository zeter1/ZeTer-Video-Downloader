"""Native queue view. Only the Tk thread renders worker status."""

from collections import deque
from functools import lru_cache
from queue import Empty, Queue
from threading import Thread
from tkinter import messagebox, ttk
from time import time
import re
import tkinter as tk

from src.core.constants import MAX_URLS_PER_SESSION
from src.downloads.queue_state import read_archived_video_entries
from src.downloads.session_view import DOWNLOAD_PHASE
from src.downloads.urls import download_url_issue
from src.ui.queue_rows import QueueRow, QueueRows, open_video_link, web_address
from src.ui.queue_thumbnails import QueueThumbnails


def download_speed(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(B|[KMG]i?B)/s", value)
    if not match:
        return 0.0
    amount, unit = match.groups()
    return float(amount) * {"B": 1, "KB": 1000, "KiB": 1024, "MB": 1000**2,
                            "MiB": 1024**2, "GB": 1000**3, "GiB": 1024**3}[unit]


def queue_file_available(path):
    try:
        return path is not None and path.is_file()
    except OSError:
        return False

STATUS = {
    "waiting": "Ожидает", "starting": "Подготовка", "downloading": "Скачивание",
    "video": "Видео готово", "audio": "Создание MP3", "done": "MP4 и MP3 готовы",
    "skipped": "Уже скачано", "failed": "Не скачалось", "audio_error": "Ошибка MP3",
    "audio_missing": "Нет аудиодорожки",
    "problematic": "Пропущено", "cancelled": "Остановлено", "unfinished": "Не завершено",
    "invalid": "Ссылка не подходит",
}
READY = {"video", "done", "skipped"}
AUDIO_ERRORS = {"audio_error", "audio_missing"}
ERRORS = {"failed", "problematic", "unfinished", "invalid"} | AUDIO_ERRORS
RETRY = {"failed", "problematic", "cancelled", "unfinished"}
ACTIVE = {"starting", "downloading", "audio"}
FILTERS = (
    ("Все", set(STATUS)),
    ("К обработке", {"waiting", "cancelled"}),
    ("В работе", ACTIVE),
    ("Готовые", READY),
    ("Ошибки", ERRORS),
)


class DownloadDashboardMixin:
    def initialize_download_dashboard(self):
        parent = self.studio_queue_parent
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        header = ttk.Frame(parent, padding=(14, 11, 14, 7))
        header.grid(sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Очередь файлов", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self._dashboard_search = tk.StringVar(master=self.root)
        search_bar = ttk.Frame(header)
        search_bar.grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Label(search_bar, text="Поиск", style="Muted.TLabel").pack(side="left", padx=(0, 7))
        self._dashboard_search_entry = search = ttk.Entry(
            search_bar, textvariable=self._dashboard_search, width=23)
        search.pack(side="left", fill="x", expand=True)
        self._dashboard_clear_search = ttk.Button(
            search_bar, text="Сбросить", command=self._clear_queue_search, style="Queue.TButton")
        self._dashboard_clear_search.pack(side="left", padx=(6, 0))
        self._dashboard_clear_search.state(["disabled"])
        search.bind("<Escape>", self._clear_queue_search)
        self.root.bind("<Control-f>", self._focus_queue_search, add="+")
        header.bind("<Configure>", lambda _: self._place_queue_search(header, search_bar, search))

        style = ttk.Style(self.root)
        style.configure("Queue.TButton", padding=(7, 4), font=("Segoe UI", 9))
        style.map("Queue.TButton", background=[("pressed", "#d3e4ca"), ("selected", "#e1efda"),
                                               ("active", "#eef4ea")])
        style.configure("QueuePhase.TLabel", foreground="#365a3a", font=("Segoe UI", 10, "bold"))
        toolbar = ttk.Frame(parent, padding=(12, 0, 12, 8))
        toolbar.grid(row=1, sticky="ew")
        self._dashboard_toolbar = toolbar
        self._dashboard_filter = tk.StringVar(master=self.root, value="Все")
        self._dashboard_tabs = {}
        for label, _ in FILTERS:
            tab = ttk.Button(toolbar, text=label, style="Queue.TButton",
                             command=lambda choice=label: self._select_queue_filter(choice))
            self._dashboard_tabs[label] = tab
        toolbar.bind("<Configure>", lambda _: self._place_queue_filters())

        self._dashboard_phase = tk.StringVar(master=self.root, value="Готово к загрузке")
        phase_bar = ttk.Frame(parent, padding=(14, 0, 14, 7))
        phase_bar.grid(row=2, column=0, sticky="ew")
        phase_bar.columnconfigure(0, weight=1)
        phase_label = ttk.Label(phase_bar, textvariable=self._dashboard_phase,
                                style="QueuePhase.TLabel", width=1)
        phase_label.grid(row=0, column=0, sticky="ew", pady=3)
        phase_label.bind("<Configure>", lambda e: phase_label.configure(wraplength=max(1, e.width - 2)))
        self._dashboard_phase_action = ttk.Button(phase_bar, style="Queue.TButton")
        self._dashboard_phase_action.grid(row=0, column=1, padx=(8, 0))
        self._dashboard_phase_action.grid_remove()

        self._thumbnails = QueueThumbnails()
        self.root.bind("<Destroy>", self._close_dashboard, add="+")
        self._dashboard_table = QueueRows(parent, self._queue_row_actions, self._retry_queue_row_audio,
                                         self._thumbnails.images.get, self._thumbnails.request)
        self._dashboard_table.grid(row=3, column=0, sticky="nsew")
        foot = ttk.Frame(parent, padding=(14, 6))
        foot.grid(row=4, column=0, sticky="ew")
        self._dashboard_hint = tk.StringVar(master=self.root)
        ttk.Label(foot, textvariable=self._dashboard_hint, style="Muted.TLabel").pack(side="left")
        ttk.Button(foot, text="Очистить очередь",
                   command=self._studio_confirm_clear_queue).pack(side="left", padx=(12, 0))
        self._dashboard_rows = {}
        self._dashboard_last = None
        self._dashboard_query = None
        self._dashboard_draft = None
        self._dashboard_identity = lru_cache(maxsize=2048)(self.url_identity)
        self._dashboard_archive_entries = {}
        self._dashboard_session_titles = {}
        self._dashboard_title_results = Queue(maxsize=1)
        archive_dir, identity = self.download_archive_dir, self.url_identity
        results = self._dashboard_title_results

        def load_titles():
            results.put_nowait(read_archived_video_entries(archive_dir, identity))

        Thread(target=load_titles, name="queue-archive-titles", daemon=True).start()
        self._speed_samples = deque(maxlen=52)
        self._speed_session = None
        self._speed_sample_time = 0
        self._dashboard_search.trace_add("write", self.refresh_download_dashboard)
        self._place_queue_filters()

    @staticmethod
    def _place_queue_search(header, search_bar, search):
        narrow = header.winfo_width() < max(440, header.winfo_fpixels("310p"))
        width = 22 if narrow else 25
        if int(search.cget("width")) != width:
            search.configure(width=width)
        search_bar.grid(row=int(narrow), column=0 if narrow else 1,
                        columnspan=2 if narrow else 1, sticky="ew" if narrow else "e",
                        padx=0 if narrow else (8, 0), pady=(5, 0) if narrow else 0)

    def _place_queue_filters(self):
        width = self._dashboard_toolbar.winfo_width()
        limit = width - 24 if width > 1 else float("inf")
        x, row, column = 0, 0, 0
        for tab in self._dashboard_tabs.values():
            size = tab.winfo_reqwidth() + 5
            if x and x + size > limit:
                x, row, column = 0, row + 1, 0
            tab.grid(row=row, column=column, sticky="w", padx=(0, 5), pady=(0, 3))
            x += size
            column += 1

    def _focus_queue_search(self, _event=None):
        search = self._dashboard_search_entry
        if search.winfo_ismapped():
            search.focus_set()
            search.selection_range(0, "end")
            return "break"

    def _clear_queue_search(self, _event=None):
        self._dashboard_search.set("")
        self._focus_queue_search()
        return "break"

    def _select_queue_filter(self, label, *, clear_search=False):
        self._dashboard_filter.set(label)
        if clear_search:
            self._dashboard_search.set("")
        self.refresh_download_dashboard()

    def _jump_to_queue_activity(self):
        url = next((url for url, (status, _) in self._dashboard_rows.items() if status in ACTIVE), None)
        if url is None:
            return
        table = self._dashboard_table
        if not table.reveal(url):
            self._select_queue_filter("В работе", clear_search=True)
            table.reveal(url)
        table.canvas.focus_set()

    def _set_queue_phase(self, phase, active, result):
        if result is not None:
            ready, errors = result
            phase += f"   ·   Готово: {ready}   ·   Ошибки: {errors}"
        if self._dashboard_phase.get() != phase:
            self._dashboard_phase.set(phase)
        button = self._dashboard_phase_action
        mode = "К активным" if active else "Показать ошибки" if result and result[1] else ""
        if button.cget("text") != mode:
            if mode:
                command = (self._jump_to_queue_activity if active else
                           lambda: self._select_queue_filter("Ошибки", clear_search=True))
                button.configure(text=mode, command=command)
                button.grid()
            else:
                button.configure(text="")
                button.grid_remove()

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
            phase, state = "Готово к загрузке", ()
        else:
            phase, state = session.snapshot()

        if working or session is not None:
            current, total = self.completed_files.value(), self.total_files.value()
            self.main_progress["value"] = current
            self.progress_percent.configure(text=f"{int(100 * current / total) if total else 0}%")
            if working and phase == DOWNLOAD_PHASE and total:
                phase += f"   ·   Видео обработано: {current} из {total}"

        if self._dashboard_title_results is not None:
            try:
                self._dashboard_archive_entries = self._dashboard_title_results.get_nowait()
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
            archived = self._dashboard_archive_entries.get(key)
            return (titles.get(key) or self._dashboard_session_titles.get(key)
                    or (archived.title if archived else "Название недоступно"))

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
        selected_filter = self._dashboard_filter.get()
        allowed = dict(FILTERS)[selected_filter]
        filtered_count = sum(status in allowed for status, _ in rows.values())
        visible = tuple((url, label, status, progress.get(url, (None, ""))[0] if status == "downloading" else None)
                        for url, (status, label) in rows.items()
                        if status in allowed and (term in label.casefold() or term in url.casefold()))
        totals = tuple(sum(status in allowed for status, label in rows.values())
                       for _, allowed in FILTERS)
        result = session.result() if session is not None and not working else None
        self._set_queue_phase(phase, working and any(status in ACTIVE for status, _ in rows.values()), result)
        self._dashboard_clear_search.state(["!disabled" if self._dashboard_search.get() else "disabled"])
        self._dashboard_rows = rows
        query = selected_filter, term
        error_videos = frozenset(
            url for url, _, status, _ in visible
            if status in AUDIO_ERRORS and session is not None and session.media_paths(url)[0] is not None
        )
        key = visible, totals, query, working, error_videos
        if key == self._dashboard_last:
            if thumbnails_changed:
                self._dashboard_table.redraw()
            return
        self._dashboard_last = key
        for (label, _), count in zip(FILTERS, totals):
            tab = self._dashboard_tabs[label]
            text = f"{label} {count}"
            if tab.cget("text") != text:
                tab.configure(text=text)
            tab.state(["selected" if label == selected_filter else "!selected"])
        self._place_queue_filters()
        hint = (f"Найдено: {len(visible)} из {filtered_count}" if term else
                f"Файлов: {len(visible)}" if visible else
                "В этом фильтре нет файлов" if rows else "Добавьте ссылки или плейлист")
        self._dashboard_hint.set(hint)
        self._dashboard_table.update_rows(tuple(
            QueueRow(url, title, STATUS[status],
                     ("#8d361d", "#cb704e") if status in ERRORS else
                     ("#315f22", "#75a45f") if status in ACTIVE else ("#536157", ""),
                     max(0, min(100, pct)) if pct is not None else None,
                     self.extract_video_id(url) if status in {"downloading", "video", "audio", "done"}
                     and self.is_youtube_url(url) else None,
                     "Повторить MP3" if status == "audio_error" and url in error_videos
                     and self.can_retry_queue_audio(url) else None,
                     "MP4 сохранён" if url in error_videos else "")
            for url, title, status, pct in visible
        ), reset_scroll=query != self._dashboard_query)
        self._dashboard_query = query

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

    def _queue_row_actions(self, url):
        if url not in self._dashboard_rows:
            return ()
        status, _ = self._dashboard_rows[url]
        session = getattr(self, "_dashboard_session", None)
        actions = []
        if status in AUDIO_ERRORS:
            if status == "audio_error" and self._can_retry_queue_row(session, url):
                actions.append(("Повторить MP3", lambda: self._retry_queue_row_audio(url)))
            actions.append(("Причина ошибки", lambda: self._show_queue_audio_error(url)))
        elif status == "invalid":
            actions.append(("Исправить ссылку", self.show_studio_links))
        elif status in RETRY and not self.is_downloading:
            actions.append(("Вернуть ссылку в редактор", lambda: self._return_dashboard_link(url)))
        archived = self._dashboard_archive_entries.get(self._dashboard_identity(url)) if status == "skipped" else None
        paths = archived.media_paths() if archived is not None else session.media_paths(url) if session else ()
        for index, (kind, path) in enumerate(zip(("MP4", "MP3"), paths)):
            if queue_file_available(path):
                actions.append((f"Открыть папку {kind}", lambda file=path, i=index: self._open_queue_file_folder(
                    archived.media_paths()[i] if archived is not None else file)))
        if web_address(url) is not None:
            actions.append(("Открыть ссылку", lambda: open_video_link(url)))
        actions.append(("Копировать ссылку", lambda: self._copy_queue_url(url)))
        return actions

    def _can_retry_queue_row(self, session, url):
        return session is not None and session.media_paths(url)[0] is not None and self.can_retry_queue_audio(url)

    def _open_queue_file_folder(self, path):
        if queue_file_available(path):
            self.open_folder_path(path.parent, "с файлом очереди")
        else:
            messagebox.showinfo("Очередь файлов", "Файл больше не найден по сохранённому пути.",
                                parent=self.root)

    def _copy_queue_url(self, url):
        self.root.clipboard_clear()
        self.root.clipboard_append(url)

    def _show_queue_audio_error(self, url):
        session = getattr(self, "_dashboard_session", None)
        video = session.media_paths(url)[0] if session is not None else None
        reason = " ".join(session.audio_error(url).split())[:320] if session is not None else ""
        text = reason or "Не удалось создать MP3."
        if queue_file_available(video):
            text += "\n\nMP4 сохранён."
        messagebox.showinfo("Ошибка MP3", text, parent=self.root)

    def _retry_queue_row_audio(self, url):
        if not self.retry_queue_audio(url):
            messagebox.showinfo(
                "Повторить MP3", "Не удалось начать повтор. Проверьте, что исходный MP4 доступен "
                "и другая обработка завершена.", parent=self.root,
            )

    def _return_dashboard_link(self, url):
        if all(self.url_identity(line) != self.url_identity(url) for line in self.get_urls_from_textbox()):
            self.add_urls_with_check([url])
        self.show_studio_links()
