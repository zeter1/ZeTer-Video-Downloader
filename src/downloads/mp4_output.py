"""Guarantee a final MP4 container without needlessly re-encoding video.

The normal yt-dlp path already asks FFmpeg to merge into MP4.  This module is
an additional publication guard for progressive/fallback formats that arrive
as WebM/MKV.  It first performs a lossless stream-copy remux; only if the
container/codecs cannot be copied to MP4 does it fall back to H.264/AAC
re-encoding.

DEPENDS ON app methods: run_command(), record_problem(), validate_video_packets(),
is_mp4_container(), video_tracks_within_height_limit(), validate_normalized_mp4().
"""

from pathlib import Path
from typing import Optional
import os
import tempfile

from src.core.constants import DOWNLOAD_TIMEOUT_SEC
from src.core.errors import CommandCancelledError


def ensure_mp4_video(app, source_path: str, *, expected_video_id: Optional[str] = None) -> Optional[str]:
    """Return the original MP4 or a verified temporary MP4 for publication.

    Existing MP4 files are returned unchanged.  Non-MP4 files are first
    remuxed with ``-c copy`` (fast/no quality loss).  Re-encoding is a rare
    fallback for codec/container combinations that MP4 cannot stream-copy.
    The publisher retains the original until the final video has been moved.
    """
    source = Path(source_path)
    if not source.is_file():
        return None
    if source.suffix.casefold() == ".mp4" and app.is_mp4_container(source):
        if app.video_tracks_within_height_limit(source) and app.validate_video_packets(source):
            return str(source)
        return None
    if not app.validate_video_packets(source):
        return None

    fd, name = tempfile.mkstemp(prefix=".zvd-normalize-", suffix=".mp4", dir=source.parent)
    os.close(fd)
    temp_target = Path(name)

    common_context = {
        "source_path": str(source),
        "target_path": str(temp_target),
        "source_extension": source.suffix.lower(),
        "expected_video_id": expected_video_id,
    }

    base_cmd = [
        "ffmpeg", "-y", "-nostdin", "-v", "error", "-xerror",
        "-i", str(source),
        "-map", "0:V:0", "-map", "0:a?",
    ]
    modes = (
        ("remux", "stream_copy", ["-c", "copy"]),
        ("transcode", "h264_aac", [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        ]),
    )
    ready = False
    try:
        for operation, mode, codec_args in modes:
            command = base_cmd + codec_args + ["-movflags", "+faststart", str(temp_target)]
            severity = "WARNING" if operation == "remux" else "ERROR"
            # Discard any bytes from the previous attempt before allowing a candidate.
            with temp_target.open("wb"):
                pass
            try:
                result = app.run_command(command, DOWNLOAD_TIMEOUT_SEC, f"mp4_output_{operation}", common_context)
            except CommandCancelledError:
                raise
            except Exception as error:
                app.record_problem(
                    "Не удалось запустить обработку MP4", severity,
                    f"mp4_output_{operation}_exception", common_context, error, command=command,
                )
                continue
            if (result.returncode == 0 and not (result.stderr or "").strip()
                    and temp_target.is_file() and app.validate_normalized_mp4(source, temp_target)):
                app.record_problem(
                    "Проверен подготовленный файл MP4", "INFO", f"mp4_output_{operation}_succeeded",
                    {**common_context, "mode": mode}, resolved=True,
                )
                ready = True
                return str(temp_target)
            failure = "remux_failed_fallback_transcode" if operation == "remux" else "transcode_failed"
            app.record_problem(
                "FFmpeg или проверка подготовленного файла MP4 вернули ошибку",
                severity, f"mp4_output_{failure}",
                {**common_context, "returncode": result.returncode},
                command=command, stdout=result.stdout, stderr=result.stderr,
            )
        return None
    finally:
        if not ready:
            try:
                temp_target.unlink(missing_ok=True)
            except OSError as error:
                app.record_problem("Не удалось удалить временный MP4", "WARNING",
                                   "mp4_output_cleanup_failed", common_context, error)
