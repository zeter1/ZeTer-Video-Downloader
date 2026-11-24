"""Очередь, ThreadPoolExecutor и запуск пользовательской сессии скачивания.

Автоматически выделено из прежнего модуля download_workflow.py без изменения тел методов.
"""

from typing import List
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import datetime
from tkinter import messagebox
import threading
import time
import tkinter as tk

from src.core.concurrency import ThreadSafeList
from src.core.constants import DEFAULT_PROXY_EXAMPLE, LOG_CATEGORY_DOWNLOAD_FAILED, MAX_CONCURRENT_DL, MAX_URLS_PER_SESSION
from src.core.errors import CommandCancelledError
from src.core.session_settings import session_setting
from src.core.settings import normalize_audio_quality
from src.downloads.session_view import DownloadSessionView
from src.downloads.youtube_queue_policy import reset_queue_capabilities


class DownloadManagerMixin:
    def download_manager(self, urls: List[str]) -> None:
        self.download_outcome = "running"
        video_dir = None
        summary = {"queued_url_count": len(urls)}
        batch = getattr(self, "_batch", None)
        audio_only = batch is not None and batch["status"] == "audio_pending"
        try:
            video_dir = Path(session_setting(self, "save_path")) / "Видео"
            # Retain validated final MP4s until audio conversion completes.
            self._batch_video_decodes = (video_dir.absolute(), {})
            summary.update(self._run_download_session(urls))
            if (audio_only and self.download_outcome == "completed"
                    and self._unfinished_batch_urls(self._batch)):
                self.download_outcome = "failed"
                self.log("ℹ️ MP3 готовы. Нескачанные ссылки остались в журнале очереди.", "INFO")
            if self.cancel_flag.is_set():
                self.download_outcome = "cancelled"
                summary["cancel_requested"] = True
            elif not self._change_active_batch(
                status="audio_pending" if summary["pending_audio_count"] else self.download_outcome,
                expected_status="audio_pending" if audio_only else "active",
            ):
                self.download_outcome = "failed"
                self.record_problem(
                    "Не удалось записать итог в журнал очереди", "ERROR",
                    "download_batch_finish_write_failed", summary,
                )
                self.log("❌ Не удалось записать итог в журнал очереди. Готовые файлы сохранены.", "ERROR")
        except CommandCancelledError:
            self.download_outcome = "cancelled"
        except Exception as error:
            self.download_outcome = "failed"
            self.record_problem(
                "Сессия скачивания прервана ошибкой", "ERROR", "download_manager_failed",
                {"queued_url_count": len(urls)}, error,
            )
            self.log(f"❌ Ошибка сессии скачивания: {error}", "ERROR")
        finally:
            # _run_download_session has left its executor: all workers stopped.
            self._batch_video_decodes = None
            try:
                self.write_problem_session_summary(self.download_outcome, summary)
            finally:
                try:
                    if video_dir is not None:
                        self.cleanup_empty_download_temp_roots(video_dir)
                finally:
                    self.finish_download()

    def _run_download_session(self, urls: List[str]) -> dict:
        reset_queue_capabilities(self)
        dashboard = getattr(self, "_dashboard_session", None)
        # FIX #14: ThreadSafeList вместо plain list — устраняет гонку без list_lock
        downloaded_videos: ThreadSafeList = ThreadSafeList()
        failed_urls: ThreadSafeList = ThreadSafeList()
        problematic_downloads: ThreadSafeList = ThreadSafeList()
        current_downloaded: List[str] = []
        converted: List[str] = []
        pending_audio_count = 0

        save_dir  = Path(session_setting(self, "save_path"))
        audio_dir = save_dir / "Аудио"
        audio_dir.mkdir(parents=True, exist_ok=True)
        video_dir = save_dir / "Видео"
        video_dir.mkdir(parents=True, exist_ok=True)

        stale_count = self.cleanup_stale_download_temp_files(video_dir)
        stale_temp_dirs = self.cleanup_stale_download_temp_dirs(video_dir)
        if stale_count:
            self.log(f"🧹 Перед новой сессией очищены старые временные хвосты yt-dlp: {stale_count}", "INFO")
        if stale_temp_dirs:
            self.log(f"🧹 Перед новой сессией очищены старые временные папки yt-dlp: {stale_temp_dirs}", "INFO")

        self.log(f"📺 СКАЧИВАНИЕ {len(urls)} ВИДЕО", "INFO")
        self.log(
            f"🚀 Параллельные загрузки: {self.max_concurrent}. "
            "YouTube больше не блокируется одним общим слотом.",
            "INFO"
        )
        self.download_threads.clear()

        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = {}
            for i, url in enumerate(urls):
                if self.cancel_flag.is_set():
                    self.log("🛑 Загрузка отменена пользователем", "WARNING")
                    break
                if self.should_skip_downloaded(url):
                    if dashboard is not None:
                        dashboard.update(url, "skipped")
                    self.log(f"⚠️ Пропущено (уже скачано ранее): {url}", "WARNING")
                    self.update_progress()
                    continue
                future = executor.submit(
                    self.download_single_video_task, url, i,
                    downloaded_videos, failed_urls, problematic_downloads
                )
                futures[future] = url

            for future in as_completed(futures):
                try:
                    future.result()
                except CommandCancelledError:
                    self.cancel_flag.set()
                except Exception as error:
                    failed_url = futures[future]
                    if dashboard is not None:
                        dashboard.update(failed_url, "failed")
                    if failed_url not in failed_urls.copy():
                        failed_urls.append(failed_url)
                    self.record_problem(
                        "Необработанная ошибка задачи скачивания", "ERROR",
                        "download_worker_failed", {"url": failed_url}, error,
                    )
                if self.cancel_flag.is_set():
                    for f in futures:
                        f.cancel()
                    break

        if not self.cancel_flag.is_set():
            existing_videos = {}
            for url, path in self.batch_existing_videos().items():
                if not self.is_url_downloaded(url):
                    valid, reason = self.validate_downloaded_video(path, url)
                    if not valid:
                        failed_urls.append(url)
                        manual_video = self.move_video_to_manual_processing(path, "recovered_video_validation_failed")
                        if dashboard is not None:
                            dashboard.update(url, "failed")
                        self.log(f"❌ Восстановленное видео не прошло проверку: {reason}", "ERROR",
                                 category=LOG_CATEGORY_DOWNLOAD_FAILED)
                        self.record_problem(
                            "Восстановленное видео не прошло проверку и не записано в историю",
                            "ERROR", "recovered_video_validation_failed",
                            {"url": url, "video_path": manual_video, "original_video_path": path, "reason": reason},
                        )
                        continue
                    if not self.save_download_log(url, if_missing=True):
                        raise OSError("Не удалось восстановить запись о готовом видео в истории скачиваний")
                existing_videos[url] = path
            current_downloaded = list(dict.fromkeys([
                *existing_videos.values(), *downloaded_videos.copy(),
            ]))
            if dashboard is not None:
                for url, path in existing_videos.items():
                    dashboard.update(url, "video", path)
                if current_downloaded:
                    dashboard.phase("Создание MP3")
            converted = self.convert_videos_to_audio(current_downloaded, audio_dir)

            if not self.cancel_flag.is_set() and len(converted) < len(current_downloaded):
                pending_audio_count = sum(
                    existing_videos.get(url) == path
                    and not self.existing_batch_audio(Path(path), audio_dir)
                    and self.probe_audio_stream_presence(path) is not False
                    for url, path in self.batch_existing_videos().items()
                )
                if pending_audio_count:
                    if not self._change_active_batch(status="audio_pending"):
                        raise OSError("Не удалось сохранить состояние незавершённой конвертации")
                    self.log(
                        f"⚠️ Для {pending_audio_count} сохранённых видео не создан MP3. "
                        "Повторить конвертацию можно через «Продолжить очередь».",
                        "WARNING",
                    )

            if self.cancel_flag.is_set():
                self.log("🛑 Объединение аудио не запускается: операция отменена", "WARNING")
            elif session_setting(self, "merge_audio"):
                # Важное поведение: объединяем ВСЕ успешно созданные MP3,
                # даже если часть видео не скачалась или часть роликов не
                # сконвертировалась. Раньше программа пропускала merge при
                # неполной конвертации, из-за чего пользователь оставался без
                # общего аудио даже при 70+ успешных файлах.
                if converted:
                    if dashboard is not None:
                        dashboard.phase("Объединение аудио")
                    if len(converted) < len(current_downloaded):
                        self.log(
                            "⚠️ Объединяю только успешно сконвертированные аудио: "
                            f"{len(converted)}/{len(current_downloaded)}. "
                            "Итоговый файл будет неполным относительно всей очереди, "
                            "но все успешные MP3 будут включены.",
                            "WARNING"
                        )
                        self.record_problem(
                            "Объединение аудио запущено по успешным MP3, несмотря на неполную конвертацию",
                            "WARNING", "merge_audio_partial_success",
                            {
                                "downloaded_video_count": len(current_downloaded),
                                "converted_audio_count": len(converted),
                                "missing_audio_count": max(0, len(current_downloaded) - len(converted)),
                                "converted_audio_files": converted,
                            }
                        )
                    else:
                        self.log(
                            f"🔗 ОБЪЕДИНЕНИЕ АУДИО: {len(converted)} успешных MP3",
                            "INFO"
                        )

                    if self.has_batch_merge_result(converted):
                        self.log("✅ Результат объединения восстановлен из журнала очереди", "SUCCESS")
                    else:
                        self.merge_audio_files(
                            converted, str(save_dir),
                            self.sanitize_filename(save_dir.name)
                        )
                else:
                    self.log(
                        "⚠️ Объединение аудио не запускается: нет успешно сконвертированных MP3",
                        "WARNING"
                    )
                    self.record_problem(
                        "Объединение аудио не запущено: список успешных MP3 пуст",
                        "WARNING", "merge_audio_no_converted_files",
                        {
                            "downloaded_video_count": len(current_downloaded),
                            "converted_audio_count": 0,
                        }
                    )

        current_problematic = problematic_downloads.copy()
        if current_problematic:
            problematic_file = self.save_problematic_downloads_session(current_problematic)
            problematic_location = (
                problematic_file.name if problematic_file else "Обработать вручную"
            )
            self.log(
                f"⚠️ Пропущено {len(current_problematic)} проблемно скачиваемых видео. "
                f"Ссылки сохранены для ручной обработки: {problematic_location}",
                "WARNING",
                category=LOG_CATEGORY_DOWNLOAD_FAILED,
            )
            self.log(
                f"📁 Открыть папку Обработать вручную: {self.manual_processing_dir}",
                "MANUAL_FOLDER_LINK",
                category=LOG_CATEGORY_DOWNLOAD_FAILED,
            )

        current_failed = failed_urls.copy()

        if current_failed:
            failed_file = self.save_failed_downloads_session(current_failed)
            failed_location = failed_file.name if failed_file else "Обработать вручную"
            self.log(
                f"⚠️ Не удалось скачать {len(current_failed)} видео. "
                f"Ссылки сохранены для ручной обработки: {failed_location}",
                "WARNING",
                category=LOG_CATEGORY_DOWNLOAD_FAILED,
            )
            self.log(
                f"📁 Открыть папку Обработать вручную: {self.manual_processing_dir}",
                "MANUAL_FOLDER_LINK"
            )

        self.download_outcome = (
            "cancelled" if self.cancel_flag.is_set() else
            "failed" if current_failed or current_problematic or len(converted) < len(current_downloaded)
            else "completed"
        )
        return {
            "downloaded_video_count": len(downloaded_videos),
            "failed_download_count": len(current_failed),
            "problematic_download_count": len(current_problematic),
            "converted_audio_count": len(converted),
            "pending_audio_count": pending_audio_count,
            "cancel_requested": self.cancel_flag.is_set(),
            "elapsed_sec": round(
                time.time() - self.download_start_time, 2
            ) if self.download_start_time else 0,
        }


    def start_download(self, *, resume_urls=None) -> None:
        if self.is_downloading or getattr(self, "_closing_download_window", False):
            return
        if not self.save_queue_draft():
            messagebox.showerror("Очередь скачивания", "Не удалось сохранить список ссылок перед началом загрузки.")
            return
        missing_deps = [
            tool for tool in self.missing_deps if resume_urls != [] or tool != "yt-dlp"
        ]
        if missing_deps:
            messagebox.showerror(
                "Ошибка",
                "Невозможно начать загрузку!\n\n"
                f"Не установлены: {', '.join(missing_deps)}"
            )
            return

        self.save_current_settings()

        if (resume_urls != [] and hasattr(self, "proxy_enabled")
                and session_setting(self, "proxy_enabled") and not self.get_proxy_url()):
            messagebox.showwarning(
                "Прокси указан неправильно",
                "Галочка «Прокси yt-dlp» включена, но адрес proxy пустой или некорректный.\n\n"
                f"Пример: {DEFAULT_PROXY_EXAMPLE}\n"
                "Можно либо исправить адрес, либо временно выключить галочку."
            )
            return

        all_urls = self.get_urls_from_textbox() if resume_urls is None else resume_urls

        valid_urls: List[str] = []
        invalid_urls: List[str] = []
        for url in all_urls:
            if self.validate_url(url):
                valid_urls.append(url)
            else:
                if url:
                    invalid_urls.append(url)

        # Дедупликация URL в текстовом поле: preserve order + одинаковый YouTube video_id.
        valid_urls, duplicate_urls = self.deduplicate_urls_by_identity(valid_urls)
        if duplicate_urls:
            self.log(f"ℹ️ Убрано {len(duplicate_urls)} дублей внутри очереди", "INFO")

        if invalid_urls:
            self.log(f"⚠️ Пропущено {len(invalid_urls)} невалидных URL", "WARNING")

        already_done, urls = [], []
        for url in valid_urls:
            (already_done if resume_urls is None and self.should_skip_downloaded(url) else urls).append(url)

        if already_done:
            self.log(f"⏭️ Пропущено {len(already_done)} уже скачанных видео", "WARNING")

        if len(urls) > MAX_URLS_PER_SESSION:
            messagebox.showwarning(
                "Слишком много URL",
                f"Максимум {MAX_URLS_PER_SESSION} видео за раз.\n"
                f"Вы добавили {len(urls)}.\nРазделите на несколько очередей."
            )
            return

        if not urls and resume_urls is None:
            messagebox.showwarning(
                "Предупреждение",
                "Нет новых видео для скачивания.\n\nВсе URL уже скачаны или список пуст."
            )
            return

        # Проверяем сам URL в yt-dlp: прямой TCP-тест не учитывает системный
        # прокси/VPN и ничего не доказывает о доступности выбранного сайта.

        started = datetime.now().astimezone()
        resume_paused = resume_urls is not None and self._batch["status"] == "paused"
        try:
            directory = str(Path(session_setting(self, "save_path")).expanduser().resolve())
            if resume_urls is None:
                audio_options = {
                    name: session_setting(self, name)
                    for name in ("audio_quality", "merge_audio", "split_hours")
                }
                self.begin_download_batch(all_urls, urls, directory, started, audio_options=audio_options)
            else:
                directory = self._batch["directory"]
                if resume_paused and not self._change_active_batch(
                    status="active", expected_status="paused", respect_batch_urls=True,
                ):
                    raise OSError("Не удалось возобновить очередь")
        except (OSError, ValueError, RuntimeError):
            messagebox.showerror(
                "Очередь скачивания",
                "Не удалось сохранить очередь запуска. Проверьте доступ к папкам.",
            )
            return
        session_time = started.strftime('%Y-%m-%d %H:%M:%S')
        self.current_session_start = session_time
        self.current_history_session_file = None
        self.current_download_log_session_file = None
        self.current_session_download_count = 0

        self.is_downloading = True
        self._update_resume_button()
        self.set_save_path_controls_enabled(False)
        self.cancel_flag.clear()
        try:
            self.max_concurrent = max(1, min(self.concurrent_var.get(), MAX_CONCURRENT_DL))
        except tk.TclError:
            self.max_concurrent = 2

        if any(self.is_youtube_url(u) for u in urls):
            self.log(
                f"🌐 YouTube: одновременных загрузок — {self.max_concurrent}. "
                "Подключение: " + self.proxy_status_text(),
                "INFO"
            )

        self.total_files.reset(len(urls))
        self.completed_files.reset(0)
        if hasattr(self, "studio_queue_parent"):
            self._dashboard_session = DownloadSessionView(urls, already_done)
        self.download_start_time = time.time()
        with self.youtube_strategy_state_lock:
            self.youtube_primary_auth_failure_count = 0
            self.youtube_primary_success_count = 0
            self.youtube_adaptive_strategy_logged = False

        self.download_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.main_progress_frame.grid()
        self.main_progress['value'] = 0
        self.main_progress['maximum'] = max(1, len(urls))
        self.update_main_progress(0)

        self.log(f"🚀 Начало загрузки {len(urls)} видео", "INFO")
        self.record_problem(
            "Старт сессии скачивания",
            "INFO", "download_session_started",
            {
                "url_count": len(urls),
                "urls": urls[:MAX_URLS_PER_SESSION],
                "quality": "1080p",
                "audio_quality": session_setting(self, "audio_quality"),
                "max_concurrent": self.max_concurrent,
                "merge_audio": session_setting(self, "merge_audio"),
                "download_subtitles": session_setting(self, "download_subtitles"),
                "proxy_enabled": bool(self.get_proxy_url()),
                "proxy_url_masked": self.mask_proxy_url(self.get_proxy_url()),
                "save_path": session_setting(self, "save_path"),
            }
        )
        self.write_problem_session_summary(
            "running",
            {
                "queued_url_count": len(urls),
                "quality": "1080p",
                "max_concurrent": self.max_concurrent,
                "merge_audio": session_setting(self, "merge_audio"),
                "proxy_enabled": bool(self.get_proxy_url()),
            },
        )

        # Snapshot must not assume that every persisted setting has a Tk control.
        # Proxy settings can intentionally exist only in self.settings; the old
        # getattr(...).get() code raised AttributeError before yt-dlp could start.
        try:
            self.download_settings_snapshot = {
                name: session_setting(self, name)
                for name in (
                    "save_path", "audio_quality", "merge_audio", "download_subtitles",
                    "split_hours", "proxy_enabled", "proxy_url",
                )
            }
            self.download_settings_snapshot["save_path"] = directory
            self.download_settings_snapshot["skip_downloaded"] = self.skip_downloaded_enabled()
            if resume_urls is not None:
                self.download_settings_snapshot.update(self._batch.get("audio_options", {}))
                self.download_settings_snapshot["audio_quality"] = normalize_audio_quality(
                    self.download_settings_snapshot["audio_quality"]
                )
                # The recovery list already accounts for history and missing recorded outputs.
                self.download_settings_snapshot["skip_downloaded"] = False
            thread = threading.Thread(
                target=self.download_manager, args=(urls,), daemon=True
            )
            self._download_manager_thread = thread
            thread.start()
        except Exception as error:
            self._download_manager_thread = None
            if resume_paused:
                self._change_active_batch(status="paused")
            # Do not leave the UI stuck in "downloading" state if session
            # preparation or worker startup fails before download_manager runs.
            self.download_outcome = "failed"
            self.record_problem(
                "Не удалось запустить поток сессии скачивания",
                "ERROR",
                "download_thread_start_failed",
                {"queued_url_count": len(urls)},
                error,
            )
            self.log(f"❌ Не удалось запустить скачивание: {error}", "ERROR")
            self.write_problem_session_summary(
                "failed", {"queued_url_count": len(urls), "worker_started": False}
            )
            self._finish_download_ui()
