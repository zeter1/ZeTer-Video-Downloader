"""Импорт списка видео из HTML/JSON YouTube и восстановление названий."""

from typing import Dict
from typing import List
from typing import Set
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import Empty, Queue
from tkinter import filedialog
import html as html_module
import json
from tkinter import messagebox
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk

from src.core.constants import FETCH_TITLE_TIMEOUT, MAX_VIDEOS_PER_LIST
from src.ui.video_list import playlist_video_key


_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_INITIAL_DATA = re.compile(r'\b(?:window\["ytInitialData"\]|ytInitialData)\s*=\s*')
_FALLBACK_VIDEO_ID = re.compile(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"')
_MISSING_TITLE = "Без названия"
_TITLE_WORKERS = 3
_TITLE_REQUEST_LIMIT = 60
_TITLE_BUDGET = 45
_TITLE_FAILURE_TTL = 300


@dataclass
class _PlaylistTitleJob:
    videos: list
    pending: deque
    deadline: float
    cancelled: threading.Event = field(default_factory=threading.Event)
    futures: dict[Future, str] = field(default_factory=dict)
    after_id: str | None = None


class PlaylistImportMixin:
    def load_from_html(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Выберите HTML файл плейлиста YouTube",
            filetypes=[("HTML файлы", "*.html *.htm"), ("Все файлы", "*.*")]
        )
        if not file_path:
            return

        self._cancel_playlist_title_lookup()
        win = tk.Toplevel(self.root)
        win.title("Загрузка плейлиста")
        win.geometry("450x180")
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="⚡ Парсинг HTML файла...",
                  font=("Arial", 11, "bold")).pack(pady=20)
        progress = ttk.Progressbar(win, mode='indeterminate', length=350)
        progress.pack(pady=10)
        progress.start(8)
        ttk.Label(win, text="Чтение и обработка файла…").pack(pady=10)

        start_time = time.monotonic()
        result = Queue(maxsize=1)
        cancelled = threading.Event()
        poll_id = None

        def parse_thread():
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    html_content = f.read()
                videos = self.parse_youtube_playlist_html_fast(html_content, cancelled=cancelled)
                if not cancelled.is_set():
                    result.put((videos, ""))
            except Exception as error:
                if not cancelled.is_set():
                    result.put((None, str(error) or type(error).__name__))

        def poll():
            nonlocal poll_id
            poll_id = None
            try:
                videos, error = result.get_nowait()
            except Empty:
                poll_id = win.after(50, poll)
                return
            if videos is None:
                self.handle_html_error(error, win)
                return
            for video in videos:
                video['is_downloaded'] = self.is_url_downloaded(video['url'])
            self.finish_html_loading(videos, win, time.monotonic() - start_time)

        def close(event):
            nonlocal poll_id
            if event.widget is win:
                cancelled.set()
                if poll_id is not None:
                    win.after_cancel(poll_id)
                    poll_id = None

        win.bind("<Destroy>", close, add="+")
        threading.Thread(target=parse_thread, daemon=True).start()
        poll_id = win.after(50, poll)


    def parse_youtube_playlist_html_fast(self, html_content: str, *, cancelled=None) -> List[Dict]:
        decoder = json.JSONDecoder()
        for match in _INITIAL_DATA.finditer(html_content):
            if cancelled is not None and cancelled.is_set():
                return []
            try:
                json_data, _ = decoder.raw_decode(html_content, match.end())
            except (ValueError, RecursionError):
                continue
            videos = self.extract_all_videos_from_json(json_data)
            if videos:
                return videos

        videos: List[Dict] = []
        seen_ids: Set[str] = set()
        previous = 0
        for match in _FALLBACK_VIDEO_ID.finditer(html_content):
            if cancelled is not None and cancelled.is_set():
                return []
            vid = match[1]
            left, previous = previous, match.end()
            if vid in seen_ids:
                continue
            seen_ids.add(vid)
            title = ""
            start = match.start()
            while (start := html_content.rfind("{", left, start)) >= 0:
                try:
                    item, end = decoder.raw_decode(html_content, start)
                except (ValueError, RecursionError):
                    continue
                if end >= match.end() and isinstance(item, dict) and item.get("videoId") == vid:
                    title = self.extract_title_from_renderer(item)
                    break
            videos.append({
                'url': f'https://www.youtube.com/watch?v={vid}',
                'title': title,
                'video_id': vid
            })
            if len(videos) >= MAX_VIDEOS_PER_LIST:
                break

        return videos


    def extract_title_from_renderer(self, renderer: dict) -> str:
        title_text = ""
        try:
            if 'title' in renderer:
                t = renderer['title']
                if 'runs' in t and t['runs']:
                    title_text = t['runs'][0].get('text', '')
                elif 'simpleText' in t:
                    title_text = t['simpleText']
        except Exception:
            pass
        return self.clean_title(title_text)


    def clean_title(self, title: str) -> str:
        if not isinstance(title, str) or not title:
            return ""
        try:
            title = html_module.unescape(title)
        except Exception:
            pass

        title = re.sub(r'\\u([0-9a-fA-F]{4})',
                       lambda m: chr(int(m.group(1), 16)), title)
        for old, new in [('\\n', ' '), ('\\r', ' '), ('\\t', ' '),
                         ('\\"', '"'), ("\'", "'"), ('&quot;', '"'),
                         ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                         ('&#39;', "'"), ('&nbsp;', ' ')]:
            title = title.replace(old, new)
        title = ' '.join(title.split())
        return title[:150]


    def _prepare_playlist_titles(self, videos):
        cache = vars(self).setdefault("_playlist_title_cache", OrderedDict())
        failures = vars(self).setdefault("_playlist_title_failures", OrderedDict())
        now = time.monotonic()
        pending = deque()
        for video in videos:
            if video['title']:
                continue
            key = playlist_video_key(video)
            video["title"] = cache.get(key, _MISSING_TITLE)
            if key in cache:
                cache.move_to_end(key)
            elif failures.get(key, 0) <= now and len(pending) < _TITLE_REQUEST_LIMIT:
                failures.pop(key, None)
                pending.append((key, video["url"]))
        return pending

    def _start_playlist_title_lookup(self, videos, pending):
        self._cancel_playlist_title_lookup()
        if not pending:
            return
        if getattr(self, "_playlist_title_executor", None) is None:
            self._playlist_title_executor = ThreadPoolExecutor(max_workers=_TITLE_WORKERS,
                                                                thread_name_prefix="playlist-title")
        job = _PlaylistTitleJob(videos, pending, time.monotonic() + _TITLE_BUDGET)
        self._playlist_title_job = job
        self._poll_playlist_titles(job)

    def _poll_playlist_titles(self, job):
        if job is not getattr(self, "_playlist_title_job", None) or job.videos is not self.video_list:
            return
        job.after_id = None
        changed = False
        for future, key in tuple(job.futures.items()):
            if not future.done():
                continue
            del job.futures[future]
            try:
                title = future.result()
            except Exception:
                self.file_logger.exception("Не удалось получить название видео плейлиста")
                continue
            if title is None:
                continue
            if title:
                self._remember_playlist_title(self._playlist_title_cache, key, title)
                video = self._playlist_videos_by_key.get(key)
                if video is not None:
                    video["title"] = title
                    changed = True
            else:
                self._remember_playlist_title(self._playlist_title_failures, key,
                                              time.monotonic() + _TITLE_FAILURE_TTL)

        if time.monotonic() >= job.deadline:
            job.pending.clear()
        while job.pending and len(job.futures) < _TITLE_WORKERS:
            key, url = job.pending.popleft()
            if key in self._playlist_videos_by_key:
                future = self._playlist_title_executor.submit(
                    self._fetch_playlist_title, url, job.deadline, job.cancelled)
                job.futures[future] = key
        if changed:
            self._playlist_rows.redraw()
        if job.futures:
            job.after_id = self._studio_playlist_window.after(75, self._poll_playlist_titles, job)
        else:
            self._playlist_title_job = None

    @staticmethod
    def _remember_playlist_title(cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        if len(cache) > MAX_VIDEOS_PER_LIST:
            cache.popitem(last=False)

    def _fetch_playlist_title(self, url, deadline, cancelled):
        remaining = deadline - time.monotonic()
        if cancelled.is_set() or remaining <= 0:
            return None
        try:
            result = subprocess.run(
                ["yt-dlp", "--skip-download", "--encoding", "utf-8", "--get-title",
                 "--no-cookies-from-browser", "--no-warnings", "--quiet", url],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="replace", env=self._utf8_subprocess_env(),
                timeout=min(FETCH_TITLE_TIMEOUT, remaining), creationflags=self.subprocess_flags,
            )
        except subprocess.TimeoutExpired:
            return "" if remaining >= FETCH_TITLE_TIMEOUT else None
        except OSError:
            return ""
        return self.clean_title(result.stdout) if result.returncode == 0 else ""

    def _cancel_playlist_title_lookup(self):
        job = getattr(self, "_playlist_title_job", None)
        if job is None:
            return
        self._playlist_title_job = None
        job.cancelled.set()
        if job.after_id is not None:
            self._studio_playlist_window.after_cancel(job.after_id)
            job.after_id = None
        for future in job.futures:
            future.cancel()

    def _destroy_playlist_titles(self, event):
        if event.widget is self._studio_playlist_window:
            self._cancel_playlist_title_lookup()
            executor = getattr(self, "_playlist_title_executor", None)
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
                self._playlist_title_executor = None


    def extract_all_videos_from_json(self, json_data: dict) -> List[Dict]:
        videos: List[Dict] = []
        seen_ids: Set[str] = set()
        seen_objects: Set[int] = set()
        pending = [json_data]
        while pending and len(videos) < MAX_VIDEOS_PER_LIST:
            obj = pending.pop()
            if not isinstance(obj, (dict, list)):
                continue
            obj_id = id(obj)
            if obj_id in seen_objects:
                continue
            seen_objects.add(obj_id)
            if isinstance(obj, list):
                pending.extend(reversed(obj))
                continue
            for kind in ('playlistVideoRenderer', 'videoRenderer',
                         'gridVideoRenderer', 'reelItemRenderer'):
                renderer = obj.get(kind)
                vid = renderer.get('videoId') if isinstance(renderer, dict) else None
                if isinstance(vid, str) and _VIDEO_ID.fullmatch(vid):
                    if vid not in seen_ids:
                        seen_ids.add(vid)
                        videos.append({
                            'url': f'https://www.youtube.com/watch?v={vid}',
                            'title': self.extract_title_from_renderer(renderer),
                            'video_id': vid,
                        })
                    break
            else:
                pending.extend(reversed(obj.values()))
        return videos


    def finish_html_loading(self, videos: List[Dict], win: tk.Toplevel,
                             parse_time: float) -> None:
        win.destroy()
        if not videos:
            messagebox.showwarning(
                "Предупреждение", "В HTML файле не найдено видео из плейлиста."
            )
            return
        pending = self._prepare_playlist_titles(videos)
        self.video_list = videos
        self.display_video_list()
        self.video_list_frame.grid()
        self._start_playlist_title_lookup(videos, pending)

        downloaded_count = sum(1 for v in videos if v['is_downloaded'])
        new_count = len(videos) - downloaded_count

        self.log(
            f"⚡ Парсинг {parse_time:.2f}с: {new_count} новых, {downloaded_count} скачанных",
            "SUCCESS" if downloaded_count == 0 else "WARNING"
        )


    def handle_html_error(self, error: str, win: tk.Toplevel) -> None:
        win.destroy()
        messagebox.showerror("Ошибка", f"Не удалось загрузить HTML:\n{error[:200]}")
        self.log(f"❌ Ошибка HTML: {error}", "ERROR")
