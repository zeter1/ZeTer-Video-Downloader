"""Durable draft and per-start download queues, independent of diagnostics."""

from datetime import datetime
from pathlib import Path
from tkinter import messagebox
import json
import threading
import uuid

from src.core.constants import MAX_URLS_PER_SESSION
from src.core.download_temp import plain_download_path
from src.core.errors import CommandCancelledError

STARTED_QUEUES_FOLDER = "Очереди запуска"
_RESUMABLE_BATCH_STATUSES = ("active", "paused", "audio_pending")


def read_batch(path):
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    validate_batch(data)
    return data


def read_archived_video_titles(archive_dir, url_identity):
    """Recover display titles from recent saved file names without opening media."""
    titles = {}
    try:
        paths = sorted((archive_dir / STARTED_QUEUES_FOLDER).glob("queue_*.json"), reverse=True)
    except OSError:
        return titles
    for path in paths[:128]:
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            batch = read_batch(path)
            for url, receipt in batch.get("video_files", {}).items():
                title = Path(receipt[0]).stem.strip()
                if title:
                    titles.setdefault(url_identity(url), title)
        except (OSError, ValueError):
            continue
    return titles


def _file_name(name):
    return (isinstance(name, str) and bool(name) and not name.endswith((" ", "."))
            and not any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name))


def _media_receipt(receipt, extension):
    return (
        isinstance(receipt, list) and len(receipt) == 5 and _file_name(receipt[0])
        and Path(receipt[0]).suffix.lower() == extension
        and all(type(number) is int for number in receipt[1:])
    )


def validate_batch(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported download queue")
    for key in ("entered", "urls", "downloaded"):
        if not isinstance(data.get(key), list) or not all(
            isinstance(url, str) and url and "\n" not in url and "\r" not in url
            for url in data[key]
        ):
            raise ValueError("Invalid download queue rows")
    urls = set(data["urls"])
    directory = data.get("directory")
    if (
        not 0 < len(urls) == len(data["urls"]) <= MAX_URLS_PER_SESSION
        or not urls.issubset(data["entered"])
        or not set(data["downloaded"]).issubset(urls)
        or data.get("status") not in (*_RESUMABLE_BATCH_STATUSES, "completed", "failed")
        or type(data.get("resume_disabled", False)) is not bool
        or type(data.get("respect_batch_urls", False)) is not bool
        or not isinstance(directory, str)
        or not Path(directory).is_absolute()
    ):
        raise ValueError("Invalid download queue state")
    for key, extension in (("video_files", ".mp4"), ("audio_files", ".mp3")):
        files = data.get(key, {})
        if not isinstance(files, dict):
            raise ValueError("Invalid media receipts")
        if not all(_media_receipt(receipt, extension) for receipt in files.values()):
            raise ValueError("Invalid media receipt")
    if not set(data.get("video_files", {})).issubset(urls):
        raise ValueError("Unknown video receipt")
    if "merge_receipt" in data:
        merge = data["merge_receipt"]
        if (not isinstance(merge, dict) or not isinstance(merge.get("inputs"), list)
                or not merge["inputs"] or not all(_media_receipt(item, ".mp3") for item in merge["inputs"])
                or not isinstance(merge.get("outputs"), list) or not merge["outputs"]):
            raise ValueError("Invalid merge receipt")
        outputs = set()
        for item in merge["outputs"]:
            if (not isinstance(item, list) or len(item) != 3
                    or not _file_name(item[0]) or not item[0].startswith((".merge_", ".split_"))
                    or not _file_name(item[1]) or Path(item[1]).suffix.lower() != ".mp3"
                    or not _media_receipt(item[2], ".mp3") or item[2][0].casefold() in outputs):
                raise ValueError("Invalid merge receipt output")
            outputs.add(item[2][0].casefold())
    audio_options = data.get("audio_options", {})
    if (
        not isinstance(audio_options, dict)
        or any(name not in ("audio_quality", "merge_audio", "split_hours") for name in audio_options)
        or any(type(audio_options[name]) is not expected for name, expected in (
            ("audio_quality", str), ("merge_audio", bool), ("split_hours", int)
        ) if name in audio_options)
    ):
        raise ValueError("Invalid audio options")


def media_fingerprint(path):
    if not plain_download_path(path):
        raise FileNotFoundError(path)
    info = path.lstat()
    return [info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino]


def restore_media_receipt(directory, receipt):
    path = directory / receipt[0]
    try:
        if media_fingerprint(path) == receipt[1:]:
            return str(path)
    except OSError:
        pass
    return None


class QueueStateMixin:
    def initialize_queue_recovery(self):
        self.draft_queue_path = self.download_archive_dir / "Текущая очередь.txt"
        self.started_queues_dir = self.download_archive_dir / STARTED_QUEUES_FOLDER
        self._draft_on_disk = None
        self._draft_read_failed = False
        self._batch_lock = threading.Lock()
        self._batch_path = None
        self._batch = None
        self._resume_callback = None
        self._closing_download_window = False
        self._download_manager_thread = None

        try:
            text = self.draft_queue_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            self._draft_read_failed = True
            self.log("⚠️ Не удалось прочитать сохранённую очередь. Проверьте файл в истории скачиваний.", "WARNING")
        else:
            self._draft_on_disk = text
            self.url_text.insert("1.0", text)
        self.url_text.edit_modified(False)
        self.url_text.bind("<<Modified>>", self._queue_text_modified, add="+")
        self._resume_callback = self.root.after_idle(self.resume_interrupted_download)

    def _queue_text_modified(self, event):
        if self.url_text.edit_modified():
            self.url_text.edit_modified(False)
            self.save_queue_draft(edited=True)

    def save_queue_draft(self, *, edited=False, text=None):
        if text is None:
            text = self.url_text.get("1.0", "end-1c")
        if text == self._draft_on_disk or not edited and self._draft_read_failed and not text:
            return True
        if not text and self._draft_on_disk is None and not edited:
            return True
        try:
            current = set(text.splitlines())
            removed = [
                line for line in (self._draft_on_disk or "").splitlines()
                if line not in current and line.strip().startswith(("https://", "http://"))
            ]
            if removed:
                archived = self.save_download_links_session(
                    "строки удалены или заменены в поле ссылок", urls=removed
                )
                if archived is None:
                    return False
            self._atomic_write_text_file(self.draft_queue_path, text)
        except (OSError, UnicodeError):
            self.log("⚠️ Не удалось сохранить текущую очередь. Проверьте доступ к папке истории скачиваний.", "WARNING")
            return False
        self._draft_on_disk = text
        self._draft_read_failed = False
        return True

    def clear_queue_draft(self):
        """Keep the links in the archive and stop automatic recovery of the cleared queue."""
        if self._draft_read_failed or not self.save_queue_draft():
            return False
        try:
            latest = self._read_latest_download_batch(strict=True)
        except (OSError, ValueError):
            return False
        previous = None
        if latest is not None and latest[1]["status"] in _RESUMABLE_BATCH_STATUSES:
            self._batch_path, self._batch = latest
            previous = self._batch
            if not self._change_active_batch(
                status="paused", expected_status=previous["status"], resume_disabled=True,
            ):
                return False
        if not self.save_queue_draft(edited=True, text=""):
            if previous is not None and not self._change_active_batch(
                status=previous["status"], expected_status="paused",
                resume_disabled=previous.get("resume_disabled", False),
            ):
                self.log(
                    "⚠️ Очистка не выполнена: список ссылок сохранён, но вернуть прежний статус "
                    "очереди не удалось. Автовозобновление этой очереди может быть отключено.",
                    "WARNING",
                )
            return False
        self.url_text.delete("1.0", "end")
        self._update_resume_button()
        return True

    def begin_download_batch(self, entered, urls, directory, started, *, audio_options=None):
        path = self.started_queues_dir / f"queue_{started:%Y-%m-%d_%H-%M-%S_%f}_{uuid.uuid4().hex}.json"
        data = {
            "version": 1,
            "started": started.isoformat(timespec="seconds"),
            "entered": entered,
            "urls": urls,
            "directory": directory,
            "downloaded": [],
            "video_files": {},
            "audio_files": {},
            "audio_options": audio_options or {},
            "respect_batch_urls": True,
            "status": "active",
        }
        validate_batch(data)
        self._atomic_write_json_file(path, data)
        self._batch_path = path
        self._batch = data

    def _change_active_batch(self, *, url=None, status=None, media=None,
                             expected_status="active", resume_disabled=None,
                             respect_batch_urls=None, merge_receipt=None):
        if getattr(self, "_batch", None) is None:
            return True
        try:
            with self._batch_lock:
                batch = self._batch
                updated = dict(batch)
                if url is not None and url in batch["urls"] and url not in batch["downloaded"]:
                    updated["downloaded"] = [*batch["downloaded"], url]
                if status is not None and batch["status"] == expected_status:
                    updated["status"] = status
                    if respect_batch_urls is not None:
                        updated["respect_batch_urls"] = respect_batch_urls
                if resume_disabled is True:
                    updated["resume_disabled"] = True
                elif resume_disabled is False:
                    updated.pop("resume_disabled", None)
                if media is not None:
                    key, item, receipt = media
                    previous = batch.get(key, {}).get(item)
                    updated[key] = {**batch.get(key, {}), item: receipt}
                    if key == "video_files" and receipt != previous:
                        # The MP3 belongs to the prior MP4, even when the names match.
                        invalidated = {receipt[0].casefold()}
                        if previous is not None:
                            invalidated.add(previous[0].casefold())
                        updated["audio_files"] = {
                            name: audio for name, audio in batch.get("audio_files", {}).items()
                            if name.casefold() not in invalidated
                        }
                if merge_receipt is not None:
                    updated["merge_receipt"] = merge_receipt
                if updated == batch:
                    return True
                validate_batch(updated)
                self._atomic_write_json_file(self._batch_path, updated)
                self._batch = updated
        except (OSError, UnicodeError):
            self.log("⚠️ Не удалось обновить журнал очереди скачивания.", "WARNING")
            return False
        return True

    def prepare_media_receipt(self, category, item, source, destination):
        if getattr(self, "_batch", None) is None:
            return
        receipt = [destination.name, *media_fingerprint(source)]
        if not self._change_active_batch(media=(category, item, receipt)):
            raise OSError("Не удалось сохранить сведения о готовом файле")

    def batch_existing_videos(self, batch=None):
        if batch is None:
            batch = getattr(self, "_batch", None)
        if batch is None:
            return {}
        directory = Path(batch["directory"]) / "Видео"
        files = batch.get("video_files", {})
        return {
            url: path for url in batch["urls"]
            if url in files and (path := restore_media_receipt(directory, files[url]))
        }

    def existing_batch_audio(self, video, audio_dir):
        batch = getattr(self, "_batch", None)
        receipt = batch and batch.get("audio_files", {}).get(video.name)
        return restore_media_receipt(audio_dir, receipt) if receipt else None

    def prepare_batch_merge_receipt(self, audio_files, publications):
        batch = getattr(self, "_batch", None)
        if batch is None:
            return
        directory = Path(batch["directory"])
        audio_dir, split_dir = directory / "Аудио", directory / "Разбивка аудио"
        inputs = []
        for path in map(Path, audio_files):
            if path.parent != audio_dir:
                raise ValueError("Входной MP3 находится вне папки аудио очереди")
            inputs.append([path.name, *media_fingerprint(path)])
        outputs = []
        for staged, final in publications:
            if staged.parent.parent != split_dir or final.parent != split_dir:
                raise ValueError("Результат находится вне папки объединения очереди")
            outputs.append([staged.parent.name, staged.name, [final.name, *media_fingerprint(staged)]])
        if not self._change_active_batch(merge_receipt={"inputs": inputs, "outputs": outputs}):
            raise OSError("Не удалось сохранить сведения об объединении аудио")

    def has_batch_merge_result(self, audio_files):
        batch = getattr(self, "_batch", None)
        merge = batch and batch.get("merge_receipt")
        if not merge:
            return False
        directory = Path(batch["directory"])
        audio_dir, split_dir = directory / "Аудио", directory / "Разбивка аудио"
        if list(audio_files) != [restore_media_receipt(audio_dir, item) for item in merge["inputs"]]:
            return False

        with self.file_lock:
            pending = []
            published = False
            missing = False
            for work_name, staged_name, receipt in merge["outputs"]:
                if restore_media_receipt(split_dir, receipt):
                    published = True
                    continue
                target, work_dir = split_dir / receipt[0], split_dir / work_name
                staged = work_dir / staged_name
                if (not plain_download_path(work_dir, directory=True)
                        or not restore_media_receipt(work_dir, [staged_name, *receipt[1:]])):
                    missing = True
                    continue
                if target.exists() or target.is_symlink():
                    raise FileExistsError(f"Файл мешает восстановлению объединения: {target}")
                pending.append((staged, target, receipt[1:]))
            if missing:
                if published:
                    raise OSError("Часть объединения сохранена, но остальные файлы отсутствуют")
                return False
            for staged, target, fingerprint in pending:
                if self.cancel_flag.is_set():
                    raise CommandCancelledError("Восстановление объединения остановлено")
                if (not plain_download_path(split_dir, directory=True)
                        or split_dir.resolve(strict=True) != split_dir
                        or not plain_download_path(staged.parent, directory=True)
                        or media_fingerprint(staged) != fingerprint):
                    raise OSError("Промежуточные файлы объединения изменились во время восстановления")
                staged.rename(target)
        return True

    def resume_interrupted_download(self):
        self._resume_callback = None
        self._resume_latest_download(automatic=True)

    def resume_paused_download(self):
        self._resume_latest_download(automatic=False)

    def _update_resume_button(self):
        button = getattr(self, "resume_queue_btn", None)
        if button is None:
            return
        batch = self._batch
        if (not self.is_downloading and batch and not batch.get("resume_disabled")
                and batch["status"] in _RESUMABLE_BATCH_STATUSES):
            button.grid()
        else:
            button.grid_remove()

    def _resume_latest_download(self, *, automatic):
        if self._closing_download_window or self.is_downloading:
            return
        latest = self._read_latest_download_batch()
        if latest is None:
            return
        path, batch = latest
        self._batch_path, self._batch = path, batch
        self._update_resume_button()
        if batch.get("resume_disabled") or batch["status"] not in _RESUMABLE_BATCH_STATUSES:
            return
        if automatic and batch["status"] != "active":
            return
        audio_only = batch["status"] == "audio_pending"
        try:
            directory_available = Path(batch["directory"]).is_dir()
        except OSError:
            directory_available = False
        urls = self._unfinished_batch_urls(batch) if directory_available and not audio_only else []
        if directory_available and not urls and not self.batch_existing_videos(batch):
            status = "failed" if audio_only else "completed"
            if audio_only:
                self.log("⚠️ Сохранённые MP4 для продолжения не найдены или изменились.", "WARNING")
            if not self._change_active_batch(status=status, expected_status=batch["status"]):
                self.download_outcome = "failed"
                self.log("❌ Не удалось записать итог восстановления очереди.", "ERROR")
            self._update_resume_button()
            return
        if self._draft_on_disk is None and not self._draft_read_failed:
            self.url_text.insert("1.0", "\n".join(batch["entered"]))
            self.save_queue_draft(edited=True)
        if not directory_available:
            self.log(
                "⚠️ Папка незавершённой загрузки недоступна. Очередь сохранена. "
                "Продолжение возможно, когда папка снова будет доступна.",
                "WARNING",
            )
            return
        self.save_path.set(batch["directory"])
        if audio_only:
            self.log("▶ Продолжаю создание MP3 из сохранённых видео.", "INFO")
        else:
            self.log(f"▶ Возобновляю обработку очереди. Осталось скачать видео: {len(urls)}.", "INFO")
        self.start_download(resume_urls=urls)

    def _read_latest_download_batch(self, *, strict=False):
        try:
            path = max(self.started_queues_dir.glob("queue_*.json"), default=None)
            if path is not None:
                return path, read_batch(path)
        except (OSError, ValueError):
            self.log("⚠️ Очередь запуска недоступна. Проверьте файлы в истории скачиваний.", "WARNING")
            if strict:
                raise
        return None

    def _unfinished_batch_urls(self, batch):
        existing = self.batch_existing_videos(batch).keys()
        missing = batch.get("video_files", {}).keys() - existing
        completed = (set(batch["downloaded"]) - missing) | existing
        respect_batch_urls = batch.get("respect_batch_urls") or batch["status"] == "paused"
        return [
            url for url in batch["urls"]
            if url not in completed and (
                respect_batch_urls or url in missing or not self.should_skip_downloaded(url)
            )
        ]

    def restore_unfinished_links(self):
        if self._closing_download_window:
            return
        latest = self._read_latest_download_batch()
        if latest is None:
            self.log("ℹ️ Сохранённая очередь запуска не найдена.", "INFO")
            return
        _, batch = latest
        identities = {self.url_identity(url) for url in self.get_urls_from_textbox()}
        missing = [
            url for url in self._unfinished_batch_urls(batch)
            if self.url_identity(url) not in identities
        ]
        if missing:
            self.add_urls_with_check(missing, check_history=False)
            self.save_queue_draft(edited=True)
        else:
            self.log("ℹ️ В последней очереди нет новых незавершённых ссылок для восстановления.", "INFO")

    def confirm_queue_pause(self):
        if self._change_active_batch(status="paused"):
            return True
        messagebox.showerror(
            "Очередь скачивания",
            "Текущая загрузка останавливается. Сохранить отмену на диске не удалось. "
            "При следующем запуске программа может снова продолжить эту очередь. "
            "Проверьте доступ к папке истории скачиваний.",
        )
        return False
