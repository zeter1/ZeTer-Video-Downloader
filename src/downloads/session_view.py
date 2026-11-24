"""In-memory download status shared by workers and the queue view."""

from collections import deque
from pathlib import Path
from threading import Lock


class DownloadSessionView:
    def __init__(self, urls, already_downloaded=()):
        self._lock = Lock()
        self._rows = dict.fromkeys(urls, ("waiting", ""))
        self._rows.update(dict.fromkeys(already_downloaded, ("skipped", "")))
        self._video_urls = {}
        self._phase = "Загрузка видео"
        self._events = deque([self._phase], maxlen=3)

    def phase(self, label):
        with self._lock:
            self._phase = label
            self._events.appendleft(label)

    def update(self, url, status, path=None):
        with self._lock:
            title = self._rows.get(url, ("", ""))[1]
            if path is not None:
                key = Path(path)
                self._video_urls[key] = url
                title = key.stem
            self._rows[url] = (status, title)
            if status in {"video", "done", "failed", "audio_error"}:
                text = {
                    "video": "Видео готово",
                    "done": "MP3 готов",
                    "failed": "Ошибка скачивания",
                    "audio_error": "Ошибка создания MP3",
                }[status]
                self._events.appendleft(text)

    def audio(self, video, status):
        with self._lock:
            url = self._video_urls.get(Path(video))
        if url is not None:
            self.update(url, status)

    def finish(self, outcome):
        labels = {
            "completed": "Обработка завершена",
            "failed": "Завершено с ошибками",
            "cancelled": "Обработка остановлена",
        }
        unfinished = "cancelled" if outcome == "cancelled" else "unfinished"
        with self._lock:
            for url, (status, title) in self._rows.items():
                if status in {"waiting", "starting", "downloading", "audio"}:
                    self._rows[url] = (unfinished, title)
            self._phase = labels.get(outcome, "Сессия завершена")
            self._events.appendleft(self._phase)

    def snapshot(self):
        with self._lock:
            return self._phase, tuple(self._rows.items()), tuple(self._events)
