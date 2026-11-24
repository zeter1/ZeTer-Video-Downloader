"""Reuse verified format selection evidence within one download."""

import re

from src.core.constants import VIDEO_MAX_HEIGHT
from src.downloads.youtube_queue_policy import auth_scope


_VERSION_AGE_WARNING = re.compile(r"WARNING: Your yt-dlp version \([^)]+\) is older than \d+ days!")


def selection_scope(strategy):
    return (auth_scope(strategy), strategy.get("ejs_mode", "github"),
            bool(strategy.get("fallback_format")), bool(strategy.get("progressive_format")),
            strategy.get("format_profile", ""))


def proven_lower_video_format(output, width, height):
    """Only trust a safe format ID with plan dimensions matching the actual file.

    The selector puts video first in video+audio. Never filter an audio ID, an
    unknown plan, or another client's catalog on the evidence of this file.
    """
    for line in str(output or "").splitlines():
        if "[DOWNLOAD_PLAN]" not in line:
            continue
        fields = dict(re.findall(r"\b(format_id|width|height)=([^\s]+)", line))
        if fields.get("width") != str(width) or fields.get("height") != str(height):
            continue
        parts = fields.get("format_id", "").split("+")
        if len(parts) <= 2 and all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
            return parts[0]
    return None


def exclude_downloaded_lower_formats(command, format_ids):
    if not format_ids:
        return command
    result = list(command)
    index = result.index("-f") + 1
    filters = "".join(f"[format_id!={format_id}]" for format_id in sorted(format_ids))
    result[index] = re.sub(r"\b(bestvideo\*?|best)(?=\[)",
                           lambda match: match.group(1) + filters, result[index])
    return result


def require_better_video(command, height):
    """Include any higher resolution and the existing cropped Full HD exception."""
    result = list(command)
    index = result.index("-f") + 1
    cap = f"[height<={VIDEO_MAX_HEIGHT}]"
    original = result[index]
    result[index] = original.replace(cap, f"{cap}[height>{height}]")
    if height > 720:
        # At the current cap, width >= 1920 also guarantees landscape orientation.
        # A missing width cannot rule out the cropped Full HD case before download.
        wide = original.replace(cap, f"{cap}[height>720][height<={height}][width>=?1920]")
        result[index] = f"{result[index]}/{wide}"
    return result


def quality_catalog_scope(strategy, default_network_args, ejs_args, reserve_height, excluded_format_ids):
    """Keep extractor, route and selector constraints but ignore transfer options."""
    transfer_options = {"--http-chunk-size", "--concurrent-fragments", "--downloader", "--downloader-args"}
    route = []
    options = iter([*strategy.get("network_args", default_network_args), *strategy["args"]])
    for option in options:
        if option in transfer_options:
            next(options, None)
        else:
            route.append(option)
    key = (tuple(route), tuple(ejs_args), reserve_height, frozenset(excluded_format_ids or ()))
    if strategy.get("progressive_format"):
        profile = "progressive"
    elif strategy.get("fallback_format") or str(strategy.get("format_profile", "")).strip().lower() == "any":
        profile = "any"
    else:
        profile = "direct"
    return key, profile


def quality_catalog_miss(result):
    """Return whether selection alone failed and whether the miss can be reused."""
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    landmarks = getattr(result, "diagnostic_landmarks", ()) or ()
    if "[DOWNLOAD_PLAN]" in output or any("[DOWNLOAD_PLAN]" in line for line in landmarks):
        return False, False
    lines = [line.strip() for line in output.splitlines()]
    errors = [line for line in lines if line.startswith("ERROR:")]
    missing = len(errors) == 1 and errors[0].endswith(
        "Requested format is not available. Use --list-formats for a list of available formats"
    )
    # Extractor warnings may indicate missing formats. The CLI's age notice is independent.
    hidden_formats_possible = any(line.startswith("WARNING:") and not _VERSION_AGE_WARNING.fullmatch(line)
                                  for line in lines)
    return missing, missing and not hidden_formats_possible
