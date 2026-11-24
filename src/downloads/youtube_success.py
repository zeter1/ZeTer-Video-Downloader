"""Handle a successful yt-dlp process result (returncode == 0).

This owner contains file validation, 1080p/candidate decisions and successful
finalization. The orchestration loop only applies the returned decision.

DEPENDS ON app methods: parse_yt_dlp_output(), validate_downloaded_video(),
move_video_to_manual_processing(), get_video_dimensions(), record_problem(),
cleanup_download_temp_files(), finalize_downloaded_video_file(), _log_video_info().
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import shutil

from src.core.constants import (
    MIN_VIDEO_FILE_SIZE_BYTES, SUPPORTED_VIDEO_EXTENSIONS, VIDEO_MAX_HEIGHT, VIDEO_TARGET_HEIGHT,
)
from src.core.download_temp import (
    RESUME_RESULT_PREFIX, owned_resume_directory, plain_download_path, resume_media_title,
    resume_workspace_key,
)
from src.downloads.files import matching_download_subtitles, publish_download_files
from src.downloads.youtube_candidate_formats import proven_lower_video_format


@dataclass(frozen=True)
class SuccessDecision:
    action: str  # retry | success | fatal
    video_path: Optional[str] = None
    last_error: Optional[str] = None
    candidate_path: Optional[str] = None
    candidate_height: int = -1
    candidate_width: int = -1
    lower_video_format_id: Optional[str] = None
    semantic_failure: bool = False
    verified_candidate_height: Optional[int] = None


def discard_quality_candidate(app, directory: Path, candidate_path: Optional[str]) -> None:
    if not candidate_path:
        return
    candidate = Path(candidate_path)
    try:
        if (candidate.parent != directory or not plain_download_path(directory.parent, directory=True)
                or not plain_download_path(directory, directory=True)):
            return
        if plain_download_path(candidate):
            candidate.unlink()
        for subtitle in matching_download_subtitles(candidate).values():
            subtitle.unlink()
        if not any(directory.iterdir()):
            directory.rmdir()
    except OSError as error:
        app.record_problem("Не удалось очистить резерв качества", "WARNING",
                           "discard_quality_candidate", {}, error)


def find_completed_attempt_output(app, result, url, workspace, video_files_before, expected_video_id):
    if not owned_resume_directory(workspace):
        if resume_workspace_key(workspace):
            return None
        return app.parse_yt_dlp_output(result.stdout, url, workspace, video_files_before, expected_video_id)

    # The current after_move report selects the exact name. Filename heuristics
    # are only appropriate when guessing a result from legacy download logs.
    outputs = {
        Path(line[len(RESUME_RESULT_PREFIX):])
        for line in (result.stdout or '').splitlines()
        if line.startswith(RESUME_RESULT_PREFIX)
    }
    if len(outputs) != 1:
        return None
    reported, = outputs
    if reported.absolute().parent != workspace.absolute():
        return None
    path = workspace / reported.name
    try:
        if (resume_media_title(path, workspace) != path.name
                and app.is_supported_video_file(path) and plain_download_path(path)
                and path.stat().st_size >= MIN_VIDEO_FILE_SIZE_BYTES):
            return str(path)
    except OSError:
        pass
    return None


def handle_successful_attempt(
    app,
    *,
    result,
    url: str,
    attempt: int,
    strategy: Dict,
    command: List[str],
    download_temp_dir: Path,
    video_dir: Path,
    quality_candidate_dir: Path,
    video_files_before,
    expected_video_id: str,
    best_candidate_path: Optional[str],
    best_candidate_height: int,
    best_candidate_width: int,
    attempt_summaries: List[Dict],
) -> SuccessDecision:
    temp_video_path = find_completed_attempt_output(
        app, result, url, download_temp_dir, video_files_before, expected_video_id,
    )
    if not temp_video_path:
        app.record_problem(
            "yt-dlp завершился успешно, но программа не нашла скачанный видеофайл",
            "ERROR",
            "yt_dlp_success_without_file",
            {
                "url": url,
                "attempt": attempt,
                "strategy": strategy["name"],
                "target_dir": str(download_temp_dir),
                "final_video_dir": str(video_dir),
                "expected_video_id": expected_video_id,
                "files_before_count": len(video_files_before),
                "supported_extensions": SUPPORTED_VIDEO_EXTENSIONS,
            },
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return SuccessDecision("retry", semantic_failure=True)

    valid, validation_reason = app.validate_downloaded_video(temp_video_path, url)
    if not valid:
        app.move_video_to_manual_processing(temp_video_path, validation_reason)
        return SuccessDecision("retry", last_error=validation_reason, semantic_failure=True)

    width, height = app.get_video_dimensions(temp_video_path)
    dimension_source = "ffprobe"
    if width <= 0 or height <= 0:
        plan_dimensions = app.parse_download_plan_dimensions(result.stdout)
        if plan_dimensions:
            width, height = plan_dimensions
            dimension_source = "yt_dlp_download_plan"
            app.record_problem(
                "ffprobe не вернул разрешение; использованы проверенные размеры из DOWNLOAD_PLAN yt-dlp",
                "INFO", "yt_dlp_resolution_fallback_to_download_plan",
                {
                    "url": url,
                    "attempt": attempt,
                    "strategy": strategy["name"],
                    "video_path": temp_video_path,
                    "width": width,
                    "height": height,
                },
                resolved=True,
            )

    resolution_text = (
        f"{width}x{height} ({height}p)" if width > 0 and height > 0
        else "не удалось определить"
    )

    if height > VIDEO_MAX_HEIGHT:
        app.log(
            f"❌ Получен файл выше допустимого лимита: {resolution_text}. "
            f"Максимум программы — {VIDEO_MAX_HEIGHT}p; пробую другую стратегию.",
            "ERROR",
        )
        app.record_problem(
            "yt-dlp вернул видео выше жёсткого лимита качества",
            "ERROR",
            "yt_dlp_resolution_above_limit",
            {
                "url": url,
                "attempt": attempt,
                "strategy": strategy["name"],
                "video_path": temp_video_path,
                "width": width,
                "height": height,
                "dimension_source": dimension_source,
                "max_height": VIDEO_MAX_HEIGHT,
            },
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        try:
            Path(temp_video_path).unlink(missing_ok=True)
        except Exception:
            pass
        return SuccessDecision("retry", semantic_failure=True)

    if height <= 0:
        # The selector itself excludes unknown heights and caps selected formats
        # to VIDEO_MAX_HEIGHT.  Do not re-download the same valid file dozens of
        # times just because one ffprobe metadata query failed.
        app.record_problem(
            "Разрешение готового видео не определено, но yt-dlp selector гарантирует лимит качества",
            "WARNING", "yt_dlp_resolution_unknown_accepted",
            {
                "url": url,
                "attempt": attempt,
                "strategy": strategy["name"],
                "video_path": temp_video_path,
                "max_height": VIDEO_MAX_HEIGHT,
            },
            command=command, stdout=result.stdout, stderr=result.stderr,
            resolved=True,
        )
        # Unknown height must not be treated as a low-quality candidate either.
        height = VIDEO_TARGET_HEIGHT

    # A cinema crop such as 1920x1032 is native Full-HD-width material.
    # Require both a landscape frame and more than 720 lines: width alone
    # must not credit a 1920x720 strip or an ordinary 720p/portrait candidate.
    reached_target = height >= VIDEO_TARGET_HEIGHT or (
        width >= 1920 and 720 < height <= VIDEO_MAX_HEIGHT and width > height
    )
    is_progressive = bool(strategy.get("progressive_format"))
    if not reached_target and (not is_progressive or best_candidate_path is not None):
        candidate_score = height if height > 0 else 0
        keep_candidate = best_candidate_path is None or candidate_score > best_candidate_height
        if keep_candidate:
            quality_candidate_dir.mkdir(parents=True, exist_ok=True)
            if not plain_download_path(quality_candidate_dir, directory=True):
                raise OSError('The quality candidate directory is not a regular directory')
            source_candidate = Path(temp_video_path)
            target = quality_candidate_dir / resume_media_title(source_candidate, download_temp_dir)
            subtitles = matching_download_subtitles(source_candidate)
            with app.file_lock:
                if subtitles:
                    target = app.make_unique_media_path(target, subtitles=subtitles)
                elif target.exists():
                    target = app.make_unique_media_path(target)
                publish_download_files(app, source_candidate, target, subtitles, shutil.move)
            discard_quality_candidate(app, quality_candidate_dir, best_candidate_path)
            best_candidate_path = str(target)
            best_candidate_height = candidate_score
            best_candidate_width = width if width > 0 else 0
            app.log(
                f"↗️ Получено {resolution_text}, но цель — {VIDEO_TARGET_HEIGHT}p. "
                "Сохраняю как резерв и продолжаю искать 1080p.",
                "WARNING",
            )
        else:
            try:
                Path(temp_video_path).unlink(missing_ok=True)
            except Exception:
                pass
        return SuccessDecision(
            "retry",
            candidate_path=best_candidate_path,
            candidate_height=best_candidate_height,
            candidate_width=best_candidate_width,
            lower_video_format_id=proven_lower_video_format(result.stdout, width, height),
            verified_candidate_height=height if keep_candidate and dimension_source == "ffprobe" and width > 0 else None,
        )

    video_path = app.finalize_downloaded_video_file(temp_video_path, video_dir, expected_video_id, url=url)
    if not video_path:
        return SuccessDecision("fatal")

    app._log_video_info(video_path)
    if strategy["name"] == "EJS GitHub stable auto quality":
        app._note_youtube_primary_strategy_result(True)
    app.cleanup_download_temp_files(
        download_temp_dir,
        expected_video_id=expected_video_id,
        final_video_path=video_path,
        reason="download_success_temp_dir",
    )
    discard_quality_candidate(app, quality_candidate_dir, best_candidate_path)
    if attempt > 1:
        app.log(f"✅ Успешно через: {strategy['name']}", "SUCCESS")
        app.record_problem(
            "Видео успешно скачано резервной стратегией",
            "INFO",
            "yt_dlp_recovered_after_fallback",
            {
                "url": url,
                "expected_video_id": expected_video_id,
                "successful_attempt": attempt,
                "successful_strategy": strategy["name"],
                "failed_attempt_count": len(attempt_summaries),
                "attempt_summaries": attempt_summaries[-8:],
                "result_path": video_path,
                "width": width,
                "height": height,
            },
            resolved=True,
        )
    return SuccessDecision("success", video_path=video_path)
