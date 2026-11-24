"""Файлы скачивания, временные каталоги и финализация результата.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import Optional
from pathlib import Path
from typing import Set
import hashlib
import errno
import os
import re
import shutil
import stat
import tempfile

from src.core.constants import SUPPORTED_VIDEO_EXTENSIONS
from src.core.download_temp import (
    RECOVERY_MARKER, RESUME_MARKER, owned_resume_directory, plain_download_path, resume_media_title,
    resume_workspace_key,
)
from src.downloads.mp4_output import ensure_mp4_video


_SUBTITLE_SUFFIX = re.compile(r"\.(?:ru|en)\.(?:vtt|srt|ttml|dfxp|ass|ssa|lrc|json3|srv[123])", re.IGNORECASE)


def matching_download_subtitles(video: Path) -> dict:
    """Find complete sidecars for this exact yt-dlp output stem."""
    subtitles = {}
    stem = video.stem
    for item in video.parent.iterdir():
        suffix = item.name[len(stem):]
        if (item.name.startswith(stem + ".") and _SUBTITLE_SUFFIX.fullmatch(suffix)
                and plain_download_path(item)):
            subtitles[suffix] = item
    return subtitles


def _same_subtitle(source: Path, target: Path) -> bool:
    try:
        info = target.lstat()
        if (not stat.S_ISREG(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or info.st_size != source.stat().st_size):
            return False
        with source.open("rb") as selected, target.open("rb") as existing:
            if not os.path.samestat(info, os.fstat(existing.fileno())):
                return False
            while block := selected.read(65536):
                if block != existing.read(len(block)):
                    return False
            after = target.lstat()
            return (not existing.read(1) and os.path.samestat(info, after)
                    and (info.st_size, info.st_mtime_ns) == (after.st_size, after.st_mtime_ns))
    except OSError:
        return False


def _publish_staged_subtitle(staged: Path, target: Path) -> None:
    if os.name == "nt":
        staged.rename(target)
    else:
        os.link(staged, target)


def publish_download_files(app, source: Path, destination: Path, subtitles: dict, move) -> None:
    """Publish the video last; keep staged copies until the full set succeeds."""
    created = []
    try:
        for suffix, subtitle in subtitles.items():
            target = destination.with_suffix(suffix)
            # Retry after a stop between subtitle and video publication.
            if _same_subtitle(subtitle, target):
                continue
            staged = None
            try:
                # Both staging and publication are inside the same video root.
                with tempfile.NamedTemporaryFile(
                    dir=source.parent, prefix=".zvd-subtitle-", suffix=".tmp", delete=False
                ) as output:
                    staged = Path(output.name)
                    with subtitle.open("rb") as data:
                        shutil.copyfileobj(data, output)
                identity = staged.stat()
                try:
                    _publish_staged_subtitle(staged, target)
                except FileExistsError:
                    if not _same_subtitle(staged, target):
                        raise
                else:
                    created.append((target, identity))
            finally:
                if staged is not None:
                    staged.unlink(missing_ok=True)
        move(str(source), str(destination))
    except BaseException:
        for target, identity in created:
            try:
                if os.path.samestat(identity, target.lstat()):
                    target.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                app.record_problem("Не удалось отменить публикацию субтитров", "WARNING",
                                   "download_subtitle_rollback_failed", {"path": str(target)}, error)
        raise


class DownloadFilesMixin:
    def is_supported_video_file(self, path: Path) -> bool:
        return path.suffix.lower().lstrip(".") in SUPPORTED_VIDEO_EXTENSIONS


    def snapshot_video_files(self, directory: Path) -> Set[Path]:
        if not directory.exists():
            return set()
        files: Set[Path] = set()
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            files.update(directory.glob(f"*.{ext}"))
        return {
            f for f in files
            if f.is_file() and not f.is_symlink()
            and f.resolve().parent == directory.resolve()
            and ".temp." not in f.name
            and ".part" not in f.name
            and not f.name.startswith(".zvd-normalize-")
            and not re.search(r"\.f\d+\.(mp4|webm|m4a)$", f.name, re.IGNORECASE)
        }


    def normalize_downloaded_path(self, path: str) -> str:
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            temp_suffix = f".temp.{ext}"
            if path.endswith(temp_suffix):
                return path[:-len(temp_suffix)] + f".{ext}"
        return path


    def make_unique_media_path(self, desired_path: Path, *, subtitles=None) -> Path:
        def available(path):
            if path.exists() or path.is_symlink():
                return False
            for suffix, subtitle in (subtitles or {}).items():
                target = path.with_suffix(suffix)
                try:
                    target.lstat()
                except FileNotFoundError:
                    continue
                except OSError:
                    return False
                if not _same_subtitle(subtitle, target):
                    return False
            return True

        if available(desired_path):
            return desired_path
        for counter in range(2, 1000):
            candidate = desired_path.with_name(
                f"{desired_path.stem} ({counter}){desired_path.suffix}"
            )
            if available(candidate):
                return candidate
        raise FileExistsError(f"Нет свободного имени для {desired_path.name}")


    def move_video_to_manual_processing(self, video_path: str, reason: str) -> str:
        """Безопасно переносит готовый проблемный видеофайл для ручной обработки."""
        source = Path(video_path)
        try:
            if not source.exists() or not source.is_file():
                return str(source)

            self.manual_processing_dir.mkdir(parents=True, exist_ok=True)
            if source.parent.resolve() == self.manual_processing_dir.resolve():
                return str(source)

            with self.file_lock:
                target = self.make_unique_media_path(self.manual_processing_dir / resume_media_title(source, source.parent))
                shutil.move(str(source), str(target))

            self.log(
                f"📦 Видео перенесено в Обработать вручную: {target.name}",
                "MANUAL_FOLDER_LINK"
            )
            return str(target)
        except Exception as e:
            self.log(
                f"❌ Не удалось перенести видео в «Обработать вручную»: {source.name} — {e}",
                "ERROR"
            )
            self.record_problem(
                "Не удалось перенести проблемный видеофайл для ручной обработки",
                "ERROR", "move_video_to_manual_processing",
                {
                    "source_video": str(source),
                    "manual_processing_dir": str(self.manual_processing_dir),
                    "reason": reason,
                },
                e
            )
            return str(source)


    def remove_video_id_suffix_from_filename(self, path: str,
                                             expected_video_id: Optional[str]) -> str:
        if not path or not expected_video_id:
            return path

        try:
            current_path = Path(path)
            if not current_path.exists() or not self.is_supported_video_file(current_path):
                return path
            # The name binds subtitles and the resume format; strip the ID only
            # when publishing the full set.
            if owned_resume_directory(current_path.parent):
                return path

            id_suffix = f" [{expected_video_id}]"
            if not current_path.stem.endswith(id_suffix):
                return path

            clean_stem = current_path.stem[:-len(id_suffix)].rstrip()
            if not clean_stem:
                return path

            with self.file_lock:
                desired_path = current_path.with_name(f"{clean_stem}{current_path.suffix}")
                target_path = self.make_unique_media_path(desired_path)
                if target_path == current_path:
                    return path
                current_path.rename(target_path)

            self.log(f"📝 Имя файла очищено: {target_path.name}", "INFO")
            return str(target_path)
        except Exception as e:
            self.record_problem(
                "Не удалось убрать YouTube ID из имени готового файла",
                "WARNING", "cleanup_downloaded_filename",
                {
                    "path": path,
                    "expected_video_id": expected_video_id,
                },
                e
            )
            return path


    def make_video_download_temp_dir(self, video_dir: Path, url: str, expected_video_id: Optional[str]) -> Path:
        """Retain a compatible private workspace until this video is published."""
        identity = f'youtube:{expected_video_id}' if expected_video_id and self.is_youtube_url(url) else url.strip()
        profile = getattr(self, 'app_dir', None)
        if profile is not None:
            identity = f'{os.path.normcase(str(profile))}\0{identity}'
        key = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]
        root = video_dir / '_temp_downloads'
        for index in range(32):
            name = f'resume-v1-{key}' if index == 0 else f'resume-v1-{key}-{index}'
            workspace = root / name
            try:
                workspace.lstat()
            except FileNotFoundError:
                return workspace
            if (owned_resume_directory(workspace)
                    and not (workspace / RECOVERY_MARKER).exists()
                    and all(plain_download_path(item) for item in workspace.iterdir())):
                return workspace
        raise FileExistsError('No private workspace is available for this download')

    def open_video_download_workspace(self, video_dir: Path, url: str, expected_video_id: Optional[str]) -> Path:
        root = video_dir / '_temp_downloads'
        self.ensure_download_work_root(root)
        workspace = self.make_video_download_temp_dir(video_dir, url, expected_video_id)
        if not owned_resume_directory(workspace):
            workspace.mkdir()
            with (workspace / RESUME_MARKER).open('xb') as marker:
                marker.write(resume_workspace_key(workspace).encode('ascii'))
        return workspace

    def ensure_download_work_root(self, root: Path) -> None:
        if not plain_download_path(root.parent, directory=True):
            raise OSError('The video output directory is not a regular directory')
        root.mkdir(exist_ok=True)
        if not plain_download_path(root, directory=True):
            raise OSError('The download workspace root is not a regular directory')


    def cleanup_empty_download_temp_roots(self, video_dir: Path) -> int:
        """После остановки workers удалить только два пустых служебных корня.

        rmdir атомарно отказывается удалять непустой каталог: незавершённые
        загрузки, чужие файлы и материалы восстановления остаются целиком.
        """
        removed = 0
        try:
            video_dir = Path(video_dir)
            root_stat = video_dir.lstat()
            reparse = stat.FILE_ATTRIBUTE_REPARSE_POINT
            if (not stat.S_ISDIR(root_stat.st_mode)
                    or getattr(root_stat, 'st_file_attributes', 0) & reparse):
                return 0
            video_root = video_dir.resolve(strict=True)
            for name in ('_temp_downloads', '_quality_candidates'):
                candidate = video_root / name
                try:
                    candidate_stat = candidate.lstat()
                    if (not stat.S_ISDIR(candidate_stat.st_mode)
                            or getattr(candidate_stat, 'st_file_attributes', 0) & reparse
                            or candidate.resolve(strict=True).parent != video_root):
                        continue
                    # Recheck the parent immediately before the empty-only removal.
                    parent_stat = video_root.lstat()
                    if (not stat.S_ISDIR(parent_stat.st_mode)
                            or getattr(parent_stat, 'st_file_attributes', 0) & reparse
                            or (parent_stat.st_dev, parent_stat.st_ino) != (root_stat.st_dev, root_stat.st_ino)):
                        break
                    current_stat = candidate.lstat()
                    if (not stat.S_ISDIR(current_stat.st_mode)
                            or getattr(current_stat, 'st_file_attributes', 0) & reparse
                            or (current_stat.st_dev, current_stat.st_ino) != (candidate_stat.st_dev, candidate_stat.st_ino)):
                        continue
                    candidate.rmdir()
                    removed += 1
                except FileNotFoundError:
                    continue
                except OSError as error:
                    if error.errno in (errno.ENOTEMPTY, errno.EEXIST) or getattr(error, 'winerror', None) == 145:
                        continue
                    self.record_problem(
                        'Не удалось удалить пустую служебную папку', 'WARNING',
                        'cleanup_empty_download_temp_roots', {'directory_name': name}, error,
                    )
        except FileNotFoundError:
            pass
        except OSError as error:
            self.record_problem(
                'Не удалось проверить служебные папки загрузки', 'WARNING',
                'cleanup_empty_download_temp_roots', {}, error,
            )
        return removed


    def cleanup_stale_download_temp_dirs(self, video_dir: Path) -> int:
        """Preserve in-progress and legacy folders between download sessions."""
        return 0


    def finalize_downloaded_video_file(self, temp_video_path: str, final_video_dir: Path,
                                       expected_video_id: Optional[str], *, url=None) -> Optional[str]:
        """Переносит готовое видео из временной папки в основную папку Видео."""
        if not temp_video_path:
            return None
        normalized = None
        try:
            original = Path(temp_video_path)
            if not original.exists() or not original.is_file():
                return None
            # Preserve recovery data before any MP4 normalization. If remux or
            # publication fails, the next session must not delete this job dir.
            marker = original.parent / RECOVERY_MARKER
            marker.touch(exist_ok=True)
            subtitles = {}
            staging_root = original.parent.parent
            if (owned_resume_directory(original.parent)
                    or (staging_root.name == "_quality_candidates"
                        and original.parent.name.startswith("candidate-")
                        and plain_download_path(staging_root, directory=True)
                        and plain_download_path(original.parent, directory=True))):
                subtitles = matching_download_subtitles(original)
            mp4_path = ensure_mp4_video(
                self, str(original), expected_video_id=expected_video_id
            )
            if not mp4_path:
                self.record_problem(
                    "Готовое видео не удалось привести к обязательному MP4",
                    "ERROR", "finalize_video_mp4_required",
                    {
                        "temp_video_path": temp_video_path,
                        "final_video_dir": str(final_video_dir),
                        "expected_video_id": expected_video_id,
                    },
                )
                return None
            src = Path(mp4_path)
            if src != original:
                normalized = src
            final_video_dir.mkdir(parents=True, exist_ok=True)
            clean_name = Path(resume_media_title(original, original.parent)).with_suffix(".mp4").name
            if expected_video_id:
                clean_stem = Path(clean_name).stem
                id_suffix = f" [{expected_video_id}]"
                if clean_stem.endswith(id_suffix):
                    clean_name = f"{clean_stem[:-len(id_suffix)].rstrip()}{src.suffix}"
            with self.file_lock:
                desired = final_video_dir / clean_name
                dst = (self.make_unique_media_path(desired, subtitles=subtitles)
                       if subtitles else self.make_unique_media_path(desired))
                if url is not None and getattr(self, "_batch", None) is not None:
                    self.prepare_media_receipt("video_files", url, src, dst)
                move = shutil.move
                if (os.name == 'nt' and src.parent.parent.parent == final_video_dir
                        and src.parent.parent.name in ('_temp_downloads', '_quality_candidates')):
                    # Same-volume Windows rename fails if another writer claims the destination.
                    move = lambda _source, target: src.rename(target)
                publish_download_files(self, src, dst, subtitles, move)
            if normalized is not None:
                normalized = None
                try:
                    original.unlink()
                except OSError as error:
                    self.record_problem("Не удалось удалить заменённый исходник MP4", "WARNING",
                                        "mp4_output_original_cleanup_failed", {"path": str(original)}, error)
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
            self.log(f"📦 Готовый файл перенесён в Видео: {dst.name}", "INFO")
            return str(dst)
        except Exception as e:
            self.record_problem(
                "Не удалось перенести готовое видео из временной папки",
                "ERROR", "finalize_downloaded_video_file",
                {
                    "temp_video_path": temp_video_path,
                    "final_video_dir": str(final_video_dir),
                    "expected_video_id": expected_video_id,
                },
                e
            )
            return None
        finally:
            if normalized is not None:
                try:
                    normalized.unlink(missing_ok=True)
                except OSError as error:
                    self.record_problem("Не удалось удалить подготовленный MP4", "WARNING",
                                        "mp4_output_cleanup_failed", {"path": str(normalized)}, error)
