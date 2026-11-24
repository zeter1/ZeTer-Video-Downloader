"""Read duration, streams, resolution and bitrate together with ffprobe."""

from collections import OrderedDict
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple
import json
import math
import stat
import subprocess
import threading

from src.core.constants import DOWNLOAD_TIMEOUT_SEC, MAX_URLS_PER_SESSION


_MEDIA_PROBE_CACHE_SIZE = 64
_MEDIA_PROBE_CACHE_LOCK = threading.Lock()


def _file_identity(media_file):
    try:
        info = Path(media_file).stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or not info.st_ino:
        return None
    # A same-volume rename can preserve this identity. A copy or replacement
    # cannot reuse the previous result just because its name/size match.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _real_video_tracks(payload):
    return [stream for stream in payload.get("streams", [])
            if stream.get("codec_type") == "video"
            and not stream.get("disposition", {}).get("attached_pic")]


def _first_video(payload):
    videos = _real_video_tracks(payload)
    return videos[0] if videos else {}


def _dimensions(stream):
    return int(stream.get("width") or 0), int(stream.get("height") or 0)


def _cacheable_metadata(payload):
    try:
        duration = float(payload.get("format", {}).get("duration", 0))
        video = _first_video(payload)
        return 0 < duration < math.inf and bool(payload.get("streams")) and (
            not video or min(_dimensions(video)) > 0
        )
    except (TypeError, ValueError, OverflowError):
        return False


class MediaProbeMixin:
    @staticmethod
    def _media_probe_command(media_file):
        return [
            "ffprobe", "-v", "error", "-show_entries",
            "format=format_name,duration:format_tags=major_brand:"
            "stream=codec_type,width,height,duration,bit_rate,sample_rate:stream_disposition=attached_pic",
            "-of", "json", str(media_file),
        ]

    def _probe_media(self, media_file, *, fresh=False):
        identity = _file_identity(media_file)
        with _MEDIA_PROBE_CACHE_LOCK:
            cache = vars(self).setdefault("_media_probe_cache", OrderedDict())
            if fresh:
                cache.pop(identity, None)
            elif identity in cache:
                cache.move_to_end(identity)
                return cache[identity]

        result = subprocess.run(
            self._media_probe_command(media_file), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
            creationflags=self.subprocess_flags,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}, result
        payload = json.loads(result.stdout)
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("format", {}), dict)
            or not isinstance(payload.get("streams", []), list)
            or any(not isinstance(stream, dict) for stream in payload.get("streams", []))
        ):
            raise ValueError("Неверная структура метаданных ffprobe")
        if identity is not None and identity != _file_identity(media_file):
            raise OSError("Медиафайл изменился во время проверки ffprobe")
        if identity is not None and not result.stderr.strip() and _cacheable_metadata(payload):
            with _MEDIA_PROBE_CACHE_LOCK:
                cache[identity] = (payload, result)
                cache.move_to_end(identity)
                if len(cache) > _MEDIA_PROBE_CACHE_SIZE:
                    cache.popitem(last=False)
        return payload, result

    def validate_video_packets(self, media_file: Path) -> bool:
        """Decode all real video and audio streams to the null muxer."""
        return self._decode_media(media_file, video=True) is not None

    def get_decoded_audio_duration(self, media_file: Path):
        """Return the decoded duration of the first audio track, or None on failure."""
        return self._decode_media(media_file, video=False)

    def _decode_media(self, media_file: Path, *, video: bool):
        identity = _file_identity(media_file)
        key = (identity, video)
        batch = getattr(self, "_batch_video_decodes", None)
        batch_path = None
        with _MEDIA_PROBE_CACHE_LOCK:
            cache = vars(self).setdefault("_media_decode_cache", OrderedDict())
            if video and identity is not None and batch is not None:
                directory, validated = batch
                path = Path(media_file).absolute()
                if path.parent == directory and path.suffix.casefold() == ".mp4":
                    if validated.get(path) == identity:
                        return True
                    batch_path = path
            if key in cache:
                cache.move_to_end(key)
                if batch_path is not None and (batch_path in validated or len(validated) < MAX_URLS_PER_SESSION):
                    validated[batch_path] = identity
                return cache[key]

        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror",
            "-err_detect", "explode", "-protocol_whitelist", "file,pipe",
            "-i", str(media_file),
        ]
        if video:
            command.extend(["-map", "0:V", "-map", "0:a?"])
        else:
            command.extend(["-map", "0:a:0", "-af", "asetpts=N/SR/TB",
                            "-stats_period", str(DOWNLOAD_TIMEOUT_SEC + 1), "-progress", "pipe:1"])
        command.extend(["-abort_on", "empty_output_stream", "-f", "null", "-"])
        operation = "check_video_decode" if video else "check_audio_decode"
        context = {"media_path": str(media_file)}
        try:
            result = self.run_command(command, DOWNLOAD_TIMEOUT_SEC, operation, context)
        except (OSError, subprocess.TimeoutExpired) as error:
            self.record_problem("Не удалось запустить проверку декодирования", "WARNING",
                                f"{operation}_failed", context, error, command=command)
            return None
        if (result.returncode != 0 or (result.stderr or "").strip()
                or identity != _file_identity(media_file)):
            self.record_problem(
                "Медиафайл не удалось полностью декодировать", "ERROR", f"{operation}_failed",
                context, command=command, stderr=result.stderr,
            )
            return None

        decoded = True
        if not video:
            progress = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
            duration_us = progress.get("out_time_us", "")
            if progress.get("progress") != "end" or not duration_us.isdecimal() or int(duration_us) <= 0:
                self.record_problem("FFmpeg не сообщил длительность декодированного аудио", "ERROR",
                                    f"{operation}_failed", context, command=command)
                return None
            decoded = int(duration_us) / 1_000_000

        if identity is not None:
            with _MEDIA_PROBE_CACHE_LOCK:
                cache[key] = decoded
                cache.move_to_end(key)
                if len(cache) > _MEDIA_PROBE_CACHE_SIZE:
                    cache.popitem(last=False)
                if batch_path is not None and (batch_path in validated or len(validated) < MAX_URLS_PER_SESSION):
                    validated[batch_path] = identity
        return decoded

    def probe_normalization_media(self, media_file: Path, *, fresh=False):
        try:
            payload, result = self._probe_media(media_file, fresh=fresh)
            if not result.stderr.strip():
                return payload
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            self.record_problem(
                "Не удалось проверить параметры MP4", "WARNING", "ffprobe_mp4_normalization",
                {"path": str(media_file)}, error,
            )
        return {}

    def get_media_duration(self, media_file: str) -> float:
        try:
            payload, _ = self._probe_media(media_file)
            return float(payload.get("format", {}).get("duration", "N/A")) if payload else 0
        except Exception as e:
            self.record_problem(
                "Не удалось получить длительность медиафайла",
                "WARNING", "ffprobe_duration",
                {"media_file": media_file}, e
            )
            return 0


    def get_audio_duration(self, audio_file: str) -> float:
        return self.get_media_duration(audio_file)

    def get_audio_sample_rate(self, audio_file: Path) -> int:
        payload = self.probe_normalization_media(audio_file)
        audio = next((stream for stream in payload.get("streams", [])
                      if stream.get("codec_type") == "audio"), {})
        try:
            return int(audio.get("sample_rate") or 0)
        except (TypeError, ValueError):
            return 0


    def has_audio_stream(self, media_file: str) -> bool:
        return self.probe_audio_stream_presence(media_file) is True


    def probe_audio_stream_presence(self, media_file: str) -> Optional[bool]:
        """Return None when ffprobe could not confirm presence or absence."""
        try:
            payload, result = self._probe_media(media_file)
            streams = payload.get("streams", [])
            if any(stream.get("codec_type") == "audio" for stream in streams):
                return True
            if (result.returncode == 0 and not (result.stderr or "").strip()
                    and any(stream.get("codec_type") == "video" for stream in streams)):
                return False
        except Exception as e:
            self.record_problem(
                "Не удалось проверить наличие аудиодорожки",
                "WARNING", "ffprobe_audio_stream",
                {"media_file": media_file}, e
            )
        return None


    def has_video_stream(self, media_file: str) -> bool:
        try:
            payload, _ = self._probe_media(media_file)
            return bool(_first_video(payload))
        except Exception as e:
            self.record_problem(
                "Не удалось проверить наличие видеодорожки",
                "WARNING", "ffprobe_video_stream",
                {"media_file": media_file}, e
            )
            return False


    def get_video_dimensions(self, video_file: str) -> Tuple[int, int]:
        """Возвращает (width, height) через устойчивый JSON-вывод ffprobe."""
        cmd = self._media_probe_command(video_file)
        try:
            payload, r = self._probe_media(video_file)
            width, height = _dimensions(_first_video(payload))
            if width > 0 and height > 0:
                return width, height
            self.record_problem(
                "ffprobe не смог надёжно определить разрешение видео",
                "WARNING", "ffprobe_video_dimensions_unknown",
                {
                    "video_file": video_file,
                    "returncode": r.returncode,
                    "stdout": (r.stdout or "")[-1000:],
                    "stderr": (r.stderr or "")[-2000:],
                },
                command=cmd, stdout=r.stdout, stderr=r.stderr,
            )
        except Exception as error:
            self.record_problem(
                "Ошибка чтения разрешения видео через ffprobe",
                "WARNING", "ffprobe_video_dimensions",
                {"video_file": video_file}, error, command=cmd,
            )
        return 0, 0


    def get_video_resolution(self, video_file: str) -> str:
        width, height = self.get_video_dimensions(video_file)
        if width > 0 and height > 0:
            return f"{width}x{height} ({height}p)"
        return "Неизвестно"


    def get_video_bitrate(self, video_file: str) -> str:
        try:
            payload, _ = self._probe_media(video_file)
            bps = int(_first_video(payload)["bit_rate"])
            mbps = bps / 1_000_000
            return f"{mbps:.1f} Mbps" if mbps >= 1 else f"{bps / 1000:.0f} kbps"
        except Exception:
            return "Неизвестно"


    def get_audio_info(self, audio_path: str) -> str:
        try:
            cmd = ["ffprobe", "-v", "quiet",
                   "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate",
                   "-of", "csv=p=0", audio_path]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=10, creationflags=self.subprocess_flags
            )
            return r.stdout.strip() if r.returncode == 0 else "Неизвестно"
        except Exception:
            return "Ошибка"


    def get_total_audio_duration(self, audio_files: List[str]) -> float:
        return sum(self.get_audio_duration(f) for f in audio_files)


    def format_duration(self, seconds: float) -> str:
        # FIX #12: защита от отрицательных значений (ffprobe может вернуть -1)
        seconds = max(0.0, seconds)
        h, remainder = divmod(int(seconds), 3600)
        m, s = divmod(remainder, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


    def sanitize_filename(self, filename: str) -> str:
        for ch in '<>:"/\\|?*':
            filename = filename.replace(ch, '_')
        return filename[:200]
