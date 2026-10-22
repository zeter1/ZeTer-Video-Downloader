"""Avoid downloading a proven lower-quality video format again in the same scope."""

import re

from src.downloads.youtube_queue_policy import auth_scope


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
