"""Объединение аудиофайлов."""

from typing import List
from pathlib import Path
import shutil
import subprocess
import tkinter as tk
import traceback

from src.core.constants import AUDIO_DURATION_TOLERANCE_SEC, MAX_SPLIT_HOURS, MERGE_TIMEOUT_SEC
from src.core.errors import CommandCancelledError
from src.core.session_settings import session_setting
import tempfile
import math


class AudioMergeMixin:
    def merge_audio_files(self, audio_files: List[str], output_path: str,
                           folder_name: str) -> None:
        if not audio_files:
            self.log("❌ Нет файлов для объединения", "ERROR")
            raise RuntimeError("Нет входных файлов для объединения")

        split_dir = Path(output_path) / "Разбивка аудио"
        split_dir.mkdir(parents=True, exist_ok=True)


        valid_files = [f for f in audio_files if Path(f).is_file()]
        if len(valid_files) != len(audio_files):
            raise FileNotFoundError("Часть входных MP3 отсутствует; неполное объединение отменено")
        durations = [self.get_audio_duration(f) for f in valid_files]
        if any(not math.isfinite(d) or d <= 0 for d in durations):
            raise RuntimeError("Не удалось определить длительность входных MP3")
        total_duration = sum(durations)
        work_dir = Path(tempfile.mkdtemp(prefix=".merge_", dir=split_dir))
        temp_out = work_dir / "merged.mp3"
        list_file = work_dir / "filelist.txt"
        succeeded = False

        try:
            with list_file.open('w', encoding='utf-8', newline='\n') as playlist:
                playlist.write("ffconcat version 1.0\n")
                for audio_file in valid_files:
                    path = Path(audio_file).absolute().as_posix()
                    if '\n' in path or '\r' in path:
                        raise ValueError("Перенос строки в пути к MP3 недопустим для FFmpeg")
                    # В ffconcat апостроф экранируется за пределами одинарных кавычек.
                    escaped_path = path.replace("'", r"'\''")
                    playlist.write(f"file '{escaped_path}'\n")

            self.log(
                f"🔄 Объединение {len(valid_files)} файлов "
                f"({self.format_duration(total_duration)})...", "INFO"
            )

            audio_quality_setting = session_setting(self, "audio_quality")
            cmd = ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
                   "-f", "concat", "-safe", "0", "-protocol_whitelist", "file", "-i", str(list_file),
                   "-abort_on", "empty_output_stream", "-c:a", "libmp3lame"]
            if audio_quality_setting == "VBR-0 (лучшее)":
                cmd.extend(["-q:a", "0"])
            else:
                cmd.extend(["-b:a", audio_quality_setting])
            cmd.extend(["-ar", "48000", "-ac", "2", "-y", str(temp_out)])
            try:
                requested_split_hours = session_setting(self, "split_hours")
            except tk.TclError:
                requested_split_hours = MAX_SPLIT_HOURS

            r = self.run_command(
                cmd, MERGE_TIMEOUT_SEC, "merge_audio_files",
                {
                    "output_path": output_path,
                    "folder_name": folder_name,
                    "audio_count": len(valid_files),
                    "split_hours": requested_split_hours,
                }
            )
            if r.returncode != 0 or (r.stderr or "").strip():
                self.log(
                    f"❌ Ошибка FFmpeg: {r.stderr[:500] if r.stderr else 'Неизвестно'}", "ERROR"
                )
                self.record_problem(
                    "FFmpeg не смог объединить аудиофайлы",
                    "ERROR", "merge_audio_files",
                    {
                        "output_path": output_path,
                        "folder_name": folder_name,
                        "audio_count": len(valid_files),
                    },
                    command=cmd, stdout=r.stdout, stderr=r.stderr
                )
                raise RuntimeError("FFmpeg не смог объединить MP3")

            merged_dur = self.get_audio_duration(str(temp_out))
            if (not temp_out.is_file() or not math.isfinite(merged_dur) or merged_dur <= 0
                    or abs(merged_dur - total_duration) >= AUDIO_DURATION_TOLERANCE_SEC):
                raise RuntimeError("Объединённый MP3 не прошёл проверку длительности")
            decoded_duration = self.get_decoded_audio_duration(temp_out)
            if decoded_duration is None or abs(decoded_duration - total_duration) >= AUDIO_DURATION_TOLERANCE_SEC:
                raise RuntimeError("Объединённый MP3 не прошёл проверку декодирования")
            if self.cancel_flag.is_set():
                raise CommandCancelledError("Объединение отменено перед сохранением")
            self.log(f"   После merge: {self.format_duration(merged_dur)}", "INFO")

            if requested_split_hours > 0:
                self.split_audio_file(temp_out, split_dir, folder_name, requested_split_hours,
                                      audio_files=audio_files)
            else:
                with self.file_lock:
                    final = self.make_unique_media_path(split_dir / f"{folder_name}_Объединенное.mp3")
                    self.prepare_batch_merge_receipt(audio_files, [(temp_out, final)])
                    if self.cancel_flag.is_set():
                        raise CommandCancelledError("Объединение отменено перед сохранением")
                    temp_out.rename(final)
                self.log(f"✅ Создан: {final.name}", "SUCCESS")

            if temp_out.exists():
                temp_out.unlink()
            succeeded = True
            self.log("✅ Объединение завершено!", "SUCCESS")

        except subprocess.TimeoutExpired:
            self.log(f"❌ Таймаут объединения (>{MERGE_TIMEOUT_SEC // 3600} час)", "ERROR")
            raise
        except CommandCancelledError:
            self.log("🛑 Объединение остановлено пользователем", "WARNING")
            raise
        except Exception as e:
            self.log(f"❌ Ошибка при объединении: {e}", "ERROR")
            self.file_logger.error(f"merge_audio_files: {traceback.format_exc()}")
            raise
        finally:
            if succeeded:
                shutil.rmtree(work_dir, ignore_errors=True)
            else:
                self.log(f"📁 Материалы незавершённого объединения сохранены: {work_dir}", "WARNING")
