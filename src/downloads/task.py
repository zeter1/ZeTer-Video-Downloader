"""Одна задача скачивания и её worker-wrapper."""

from pathlib import Path
from time import monotonic
import threading
import traceback

from src.core.concurrency import ThreadSafeList
from src.core.constants import LOG_CATEGORY_DOWNLOAD_FAILED
from src.core.errors import ProblematicDownloadSkipped


_YOUTUBE_DOWNLOAD_START_INTERVAL_SEC = 3.0


class DownloadTaskMixin:
    def _wait_for_download_start(self, url: str) -> bool:
        if not self.is_youtube_url(url):
            return not self.cancel_flag.is_set()

        while not self.cancel_flag.is_set():
            with self.youtube_strategy_state_lock:
                now = monotonic()
                delay = getattr(self, "_next_youtube_download_start", now) - now
                if delay <= 0:
                    self._next_youtube_download_start = now + _YOUTUBE_DOWNLOAD_START_INTERVAL_SEC
                    return True
            if self.cancel_flag.wait(delay):
                break
        return False

    def download_single_video(self, url: str, index: int,
                               downloaded_videos: ThreadSafeList,
                               failed_urls: ThreadSafeList,
                               problematic_downloads: ThreadSafeList) -> None:
        dashboard = getattr(self, "_dashboard_session", None)
        status = "failed"
        ready_path = None
        try:
            if dashboard is not None:
                dashboard.update(url, "starting")
            self.log(f"📥 [{index + 1}/{self.total_files.value()}]: {url}")

            if not self.validate_url(url):
                self.log(
                    f"❌ Неверный формат URL: {url}",
                    "ERROR",
                    category=LOG_CATEGORY_DOWNLOAD_FAILED,
                )
                failed_urls.append(url)
                return

            if self.should_skip_downloaded(url):
                status = "skipped"
                self.log("⏭️ Пропущено (уже скачано)", "WARNING")
                return

            if not self._wait_for_download_start(url):
                return

            if dashboard is not None:
                dashboard.update(url, "downloading")
            result = self.run_yt_dlp_with_download_slot(url)

            if result and self.wait_for_file(result):
                is_valid, reason = self.validate_downloaded_video(result, url)
                if is_valid:
                    self.log(f"✅ Проверка скачанного видео: {reason}", "SUCCESS")
                    self._change_active_batch(url=url)
                    # После успешного файла удаляем .part/.ytdl/.f137/.f140 хвосты этого ролика.
                    self.cleanup_download_temp_files(
                        Path(result).parent,
                        expected_video_id=self.extract_video_id(url),
                        final_video_path=result,
                        reason="download_success_validated"
                    )
                    downloaded_videos.append(result)
                    self.save_download_log(url)
                    status, ready_path = "video", result
                else:
                    failed_urls.append(url)
                    manual_video = self.move_video_to_manual_processing(
                        result, "downloaded_video_validation_failed"
                    )
                    self.log(
                        f"❌ Скачанный файл не прошёл проверку: {url} — {reason}",
                        "ERROR",
                        category=LOG_CATEGORY_DOWNLOAD_FAILED,
                    )
                    self.record_problem(
                        "Скачанный файл не прошёл проверку ffprobe и не записан в историю",
                        "ERROR", "downloaded_video_validation_failed",
                        {
                            "url": url,
                            "video_path": manual_video,
                            "original_video_path": result,
                            "reason": reason,
                        }
                    )
            else:
                if not self.cancel_flag.is_set():
                    failed_urls.append(url)
                    self.log(
                        f"❌ Не удалось скачать: {url}",
                        "ERROR",
                        category=LOG_CATEGORY_DOWNLOAD_FAILED,
                    )
                    self.record_problem(
                        "Видео не скачалось или файл не появился после yt-dlp",
                        "ERROR", "download_single_video_no_result",
                        {
                            "url": url,
                            "result_path": result,
                            "result_returned": bool(result),
                        }
                    )

        except ProblematicDownloadSkipped as e:
            status = "problematic"
            item = e.to_log_item()
            item["index_in_session"] = index + 1
            item["total_in_session"] = self.total_files.value()
            problematic_downloads.append(item)
            self.log(
                f"⏭️ Пропущено проблемно скачиваемое видео: {e.url} — {e.reason}",
                "WARNING",
                category=LOG_CATEGORY_DOWNLOAD_FAILED,
            )
            self.record_problem(
                "Видео пропущено как проблемно скачиваемое и будет записано в отдельный txt",
                "WARNING", "download_single_video_problematic_skip",
                item
            )
        except KeyboardInterrupt:
            self.log("🛑 Загрузка прервана пользователем", "WARNING")
            raise
        except Exception as e:
            self.log(
                f"❌ Ошибка при скачивании {url}: {str(e)}",
                "ERROR",
                category=LOG_CATEGORY_DOWNLOAD_FAILED,
            )
            self.file_logger.error(f"download_single_video: {traceback.format_exc()}")
            self.record_problem(
                "Необработанное исключение worker-а скачивания",
                "ERROR", "download_single_video_exception",
                {
                    "url": url,
                    "index_in_session": index + 1,
                    "total_in_session": self.total_files.value(),
                    "cancelled": self.cancel_flag.is_set(),
                    "exception_stage": "download_single_video",
                },
                exception=e,
            )
            failed_urls.append(url)
        finally:
            if dashboard is not None:
                if self.cancel_flag.is_set() and status == "failed":
                    status = "cancelled"
                dashboard.update(url, status, ready_path)
            if not self.cancel_flag.is_set():
                self.update_progress()


    def download_single_video_task(self, url: str, index: int,
                                    downloaded_videos: ThreadSafeList,
                                    failed_urls: ThreadSafeList,
                                    problematic_downloads: ThreadSafeList) -> None:
        thread = threading.current_thread()
        self.download_threads.append(thread)
        try:
            self.download_single_video(url, index, downloaded_videos, failed_urls, problematic_downloads)
        finally:
            self.download_threads.remove(thread)
