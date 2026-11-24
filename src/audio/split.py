"""Разбиение MP3 в отдельной рабочей папке с сохранением исходника при сбое."""
from pathlib import Path
import math
import os
import shutil
import subprocess
import tempfile

from src.core.constants import SPLIT_TIMEOUT_SEC
from src.core.errors import CommandCancelledError
from src.core.session_settings import session_setting


_MAX_SIMULTANEOUS_ENCODERS = 16
_WINDOWS_MAX_COMMAND_UNITS = 32767


def _split_command(source, parts, boundaries, quality_args):
    labels = "".join(f"[in{i}]" for i in range(len(parts)))
    filters = [f"[0:a:0]asplit={len(parts)}{labels}"]
    outputs = []
    for i, (part, start, end) in enumerate(zip(parts, boundaries, boundaries[1:])):
        filters.append(f"[in{i}]atrim=start_sample={start}:end_sample={end},asetpts=N/SR/TB[out{i}]")
        outputs.extend(["-map", f"[out{i}]", "-c:a", "libmp3lame", *quality_args, str(part)])
    return ["ffmpeg", "-nostdin", "-n", "-v", "error", "-xerror", "-err_detect", "explode",
            "-protocol_whitelist", "file,pipe", "-i", str(source),
            "-filter_complex", ";".join(filters), "-abort_on", "empty_output_stream", *outputs]


class AudioSplitMixin:
    def split_audio_file(self, temp_file: Path, split_dir: Path,
                         folder_name: str, hours: int, *, audio_files=None) -> None:
        seconds = float(hours) * 3600
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Интервал разбиения должен быть положительным")
        source_duration = self.get_decoded_audio_duration(temp_file)
        sample_rate = self.get_audio_sample_rate(temp_file)
        if source_duration is None or sample_rate <= 0:
            raise RuntimeError("Не удалось декодировать исходный MP3")
        source_samples = round(source_duration * sample_rate)
        interval_samples = round(seconds * sample_rate)
        if interval_samples <= 0:
            raise ValueError("Интервал разбиения короче одного аудиосэмпла")
        # Keep a tail shorter than 0.1 seconds in the preceding part.
        last_cut = source_samples - max(1, round(sample_rate / 10))
        boundaries = [0, *range(interval_samples, last_cut + 1, interval_samples), source_samples]
        split_dir.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix=".split_", dir=split_dir))
        parts = [work_dir / f"part_{i:03d}.mp3" for i in range(len(boundaries) - 1)]
        succeeded = False
        try:
            if self.cancel_flag.is_set():
                raise CommandCancelledError("Разбиение отменено перед сохранением")
            if len(parts) == 1:
                shutil.copyfile(temp_file, parts[0])
            else:
                # Each cut gets its own encoder and gapless metadata. MP3 packets
                # copied across a cut can still depend on the previous part.
                quality = session_setting(self, "audio_quality")
                quality_args = ["-q:a", "0"] if quality == "VBR-0 (лучшее)" else ["-b:a", quality]
                context = {"temp_file": str(temp_file), "split_dir": str(work_dir), "hours": hours}
                offset = 0
                while offset < len(parts):
                    count = min(_MAX_SIMULTANEOUS_ENCODERS, len(parts) - offset)
                    while True:
                        stop = offset + count
                        cmd = _split_command(temp_file, parts[offset:stop], boundaries[offset:stop + 1], quality_args)
                        if os.name != "nt" or (
                            len(subprocess.list2cmdline(cmd).encode("utf-16-le")) // 2 + 1 <= _WINDOWS_MAX_COMMAND_UNITS
                        ):
                            break
                        if count == 1:
                            raise RuntimeError("Пути к MP3 превышают длину команды Windows")
                        count //= 2
                    result = self.run_command(cmd, SPLIT_TIMEOUT_SEC, "split_audio_file", context)
                    if result.returncode != 0 or (result.stderr or "").strip():
                        raise RuntimeError(f"Ошибка FFmpeg при разбиении: {(result.stderr or '')[-500:]}")
                    offset = stop

            for part, start, end in zip(parts, boundaries, boundaries[1:]):
                duration = self.get_decoded_audio_duration(part) if part.is_file() else None
                if duration is None or round(duration * sample_rate) != end - start:
                    raise RuntimeError(f"Часть MP3 не прошла проверку декодирования: {part.name}")
            if self.cancel_flag.is_set():
                raise CommandCancelledError("Разбиение отменено перед сохранением")
            with self.file_lock:
                targets = [self.make_unique_media_path(split_dir / f"{folder_name}_Часть_{i:03d}.mp3")
                           for i in range(1, len(parts) + 1)]
                if audio_files is not None:
                    self.prepare_batch_merge_receipt(audio_files, list(zip(parts, targets)))
                if self.cancel_flag.is_set():
                    raise CommandCancelledError("Разбиение отменено перед сохранением")
                published = []
                try:
                    for part, target in zip(parts, targets):
                        part.rename(target)
                        published.append((part, target))
                except OSError:
                    for part, target in reversed(published):
                        try:
                            target.rename(part)
                        except OSError as rollback_error:
                            self.record_problem(
                                "Не удалось вернуть неполный набор частей в рабочую папку",
                                "ERROR", "split_publication_rollback_failed",
                                {"published_part": str(target), "recovery_dir": str(work_dir)},
                                rollback_error,
                            )
                    raise
            succeeded = True
            self.log(f"✅ Создано {len(parts)} частей", "SUCCESS")
        finally:
            if succeeded:
                shutil.rmtree(work_dir, ignore_errors=True)
            else:
                self.log(f"📁 Исходный MP3 и промежуточные части сохранены: {work_dir}", "WARNING")
