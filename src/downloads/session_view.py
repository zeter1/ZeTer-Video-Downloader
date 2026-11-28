"""In-memory download status shared by workers and the queue view."""

from pathlib import Path
from threading import Lock

DOWNLOAD_PHASE = "Скачивание видео"


class DownloadSessionView:
    def __init__(self, urls, already_downloaded=()):
        self._lock = Lock()
        self._rows = dict.fromkeys(urls, ("waiting", ""))
        self._run_urls = set(self._rows)
        self._rows.update(dict.fromkeys(already_downloaded, ("skipped", "")))
        self._video_urls = {}
        self._video_paths = {}
        self._audio_paths = {}
        self._audio_errors = {}
        self._result = None
        self._phase = DOWNLOAD_PHASE

    def phase(self, label):
        with self._lock:
            self._phase = label

    def update(self, url, status, path=None):
        with self._lock:
            self._run_urls.add(url)
            title = self._rows.get(url, ("", ""))[1]
            if path is not None:
                key = Path(path)
                if self._video_paths.get(url) != key:
                    self._video_urls.pop(self._video_paths.get(url), None)
                    self._audio_paths.pop(url, None)
                    self._audio_errors.pop(url, None)
                self._video_paths[url] = key
                self._video_urls[key] = url
                title = key.stem
            self._rows[url] = (status, title)

    def audio(self, video, status, audio_path=None, *, reason=""):
        with self._lock:
            url = self._video_urls.get(Path(video))
            if url is not None:
                self._rows[url] = status, self._rows[url][1]
                if audio_path is not None:
                    self._audio_paths[url] = Path(audio_path)
                if status == "done":
                    self._audio_errors.pop(url, None)
                elif reason:
                    self._audio_errors[url] = reason

    def audio_error(self, url):
        with self._lock:
            return self._audio_errors.get(url, "")

    def media_paths(self, url):
        with self._lock:
            return self._video_paths.get(url), self._audio_paths.get(url)

    def forget_video(self, video):
        with self._lock:
            url = self._video_urls.pop(Path(video), None)
            if url is not None:
                self._video_paths.pop(url, None)
                self._audio_paths.pop(url, None)

    def for_audio_retry(self, url):
        with self._lock:
            retry = DownloadSessionView([url])
            retry._rows = self._rows.copy()
            retry._video_urls = self._video_urls.copy()
            retry._video_paths = self._video_paths.copy()
            retry._audio_paths = self._audio_paths.copy()
            retry._audio_errors = self._audio_errors.copy()
            retry._rows[url] = "audio", self._rows.get(url, ("", ""))[1]
            retry._audio_paths.pop(url, None)
            retry._phase = "Создание MP3"
            return retry

    def finish(self, outcome):
        labels = {
            "completed": "Обработка завершена",
            "failed": "Завершено с ошибками",
            "cancelled": "Обработка остановлена",
        }
        unfinished = "cancelled" if outcome == "cancelled" else "unfinished"
        with self._lock:
            for url in self._run_urls:
                status, title = self._rows[url]
                if status in {"waiting", "starting", "downloading", "audio"}:
                    self._rows[url] = (unfinished, title)
            statuses = [self._rows[url][0] for url in self._run_urls]
            self._result = (
                sum(status in {"video", "done"} for status in statuses),
                sum(status in {"failed", "audio_error", "audio_missing", "problematic", "unfinished", "invalid"}
                    for status in statuses),
            )
            self._phase = labels.get(outcome, "Сессия завершена")

    def snapshot(self):
        with self._lock:
            return self._phase, tuple(self._rows.items())

    def result(self):
        with self._lock:
            return self._result
