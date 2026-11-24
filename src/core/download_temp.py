"""Временные файлы загрузки и их безопасная очистка."""

from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from datetime import datetime
import re
import stat

from src.core.constants import SUPPORTED_VIDEO_EXTENSIONS, YT_DLP_RUNTIME_TAIL_LINES


RESUME_MARKER = '.zvd-resume'
RECOVERY_MARKER = '.preserve_download'
RESUME_RESULT_PREFIX = '[ZVD_RESULT] '
_RESUME_DIRECTORY = re.compile(r'resume-v1-([0-9a-f]{32})(?:-[0-9]+)?')
_RESUME_MEDIA_NAME = re.compile(r'ZVD-([0-9a-f]{8}) \[([^]\r\n]+)\] (.+)')


def plain_download_path(path: Path, *, directory: bool = False) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    return (
        expected_type(info.st_mode)
        and not getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        and (directory or info.st_nlink == 1)
    )


def resume_workspace_key(directory: Path) -> Optional[str]:
    match = _RESUME_DIRECTORY.fullmatch(directory.name)
    return match.group(1) if match else None


def owned_resume_directory(directory: Path) -> bool:
    key = resume_workspace_key(directory)
    marker = directory / RESUME_MARKER
    if (not key or directory.parent.name != '_temp_downloads'
            or not plain_download_path(directory.parent.parent, directory=True)
            or not plain_download_path(directory.parent, directory=True)
            or not plain_download_path(directory, directory=True)
            or not plain_download_path(marker)):
        return False
    identity = key.encode('ascii')
    with marker.open('rb') as source:
        return source.read(len(identity) + 1) == identity


def resume_media_title(path: Path, workspace: Path) -> str:
    """Имя без служебного префикса, который хранит выбранный формат yt-dlp."""
    match = _RESUME_MEDIA_NAME.fullmatch(path.name)
    key = resume_workspace_key(workspace)
    if match and key and match.group(1) == key[:8]:
        return match.group(3)
    return path.name


class DownloadTempMixin:
    def _recent_output_tail(self, lines: List[str], max_lines: int = YT_DLP_RUNTIME_TAIL_LINES) -> str:
        return "\n".join(lines[-max_lines:])


    def _snapshot_download_target_files(self, target_dir: Optional[str],
                                        expected_video_id: Optional[str] = None,
                                        limit: int = 40) -> List[Dict]:
        """Снимок файлов в папке загрузки: готовые, .part, .temp и совпадающие с video_id."""
        if not target_dir:
            return []
        try:
            directory = Path(target_dir)
            if not directory.exists() or not directory.is_dir():
                return [{"path": str(directory), "exists": directory.exists(), "is_dir": False}]
            candidates = []
            patterns = ["*.part", "*.ytdl", "*.temp.*"]
            patterns.extend([f"*.{ext}" for ext in SUPPORTED_VIDEO_EXTENSIONS])
            if expected_video_id:
                patterns.append(f"*{expected_video_id}*")
            seen = set()
            for pattern in patterns:
                for file_path in directory.glob(pattern):
                    try:
                        key = str(file_path.resolve())
                    except OSError:
                        key = str(file_path)
                    if key in seen or not file_path.exists():
                        continue
                    seen.add(key)
                    try:
                        stat = file_path.stat()
                        candidates.append({
                            "name": file_path.name,
                            "path": str(file_path),
                            "size_bytes": stat.st_size,
                            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                            "suffix": file_path.suffix,
                        })
                    except OSError as e:
                        candidates.append({"path": str(file_path), "stat_error": str(e)})
            candidates.sort(key=lambda x: x.get("mtime", ""), reverse=True)
            return candidates[:limit]
        except Exception as e:
            return [{"snapshot_error": f"{type(e).__name__}: {e}"}]


    def cleanup_download_temp_files(self, target_dir, expected_video_id: Optional[str] = None,
                                    final_video_path: Optional[str] = None,
                                    reason: str = "") -> List[str]:
        """Удалить подтверждённые артефакты только после публикации этого видео."""
        deleted: List[str] = []
        if reason != 'download_success_temp_dir' or not final_video_path:
            return deleted
        directory = Path(target_dir)
        try:
            published = Path(final_video_path)
            if (not owned_resume_directory(directory)
                    or published.parent != directory.parent.parent
                    or not plain_download_path(published)
                    or (directory / RECOVERY_MARKER).exists()):
                return deleted
            for item in directory.iterdir():
                if resume_media_title(item, directory) == item.name:
                    continue
                if not owned_resume_directory(directory) or not plain_download_path(item):
                    continue
                item.unlink()
                deleted.append(item.name)
            if owned_resume_directory(directory) and set(directory.iterdir()) == {directory / RESUME_MARKER}:
                (directory / RESUME_MARKER).unlink()
                directory.rmdir()
        except OSError as error:
            self.record_problem(
                "Не удалось очистить служебные файлы yt-dlp", "WARNING",
                "cleanup_download_temp_files", {"deleted_count": len(deleted)}, error,
            )
        if deleted:
            self.log(f"🧹 Удалены временные файлы yt-dlp: {len(deleted)}", "INFO")
        return deleted

    def cleanup_stale_download_temp_files(self, target_dir) -> int:
        """При запуске сохраняем частичные файлы и содержимое пользовательской папки."""
        return 0
