"""Bounded retry policy; capability evidence lives only in the current queue."""

import re
import threading

from src.core.constants import YT_DLP_MAX_FAILED_ATTEMPTS, YT_DLP_FAILED_ATTEMPT_BUDGET_SEC
from src.downloads.youtube_retry import cookie_read_failed, has_auth_error


_STATE_LOCK = threading.Lock()


def auth_scope(strategy):
    """Network/chunk variations do not change authentication capability."""
    args = list(strategy.get("args", []))
    clients = re.findall(r"player_client=([^;\s]+)", " ".join(args))
    cookies = tuple((arg, args[index + 1]) for index, arg in enumerate(args[:-1])
                    if arg in ("--cookies", "--cookies-from-browser"))
    return (tuple(clients) or ("default",), cookies)


def _capabilities(strategy):
    keys = []
    args = list(strategy.get("args", []))
    if "--force-ipv6" in strategy.get("network_args", []):
        keys.append("ipv6_dns")
    if any(arg == "--cookies-from-browser" and args[index + 1].split(":", 1)[0] == "chrome"
           for index, arg in enumerate(args[:-1])):
        keys.append("chrome_cookies")
    if "ios" in auth_scope(strategy)[0]:
        profile = "progressive" if strategy.get("progressive_format") else (
            "any" if strategy.get("fallback_format") else strategy.get("format_profile") or "direct_http")
        keys.append("ios_formats:" + profile)
    return keys


class QueueCapabilities:
    def __init__(self):
        self.lock = threading.Lock()
        self.excluded = {}
        self.ios_failed_videos = {}

    def skip_reason(self, strategy):
        with self.lock:
            return next((self.excluded[key] for key in _capabilities(strategy)
                         if key in self.excluded), None)

    def observe(self, strategy, url, error="", success=False):
        lower = str(error or "").casefold()
        with self.lock:
            for key in _capabilities(strategy):
                if key == "ipv6_dns" and any(marker in lower for marker in (
                        "getaddrinfo failed", "name or service not known", "nodename nor servname")):
                    self.excluded[key] = "ipv6_dns_resolution_failed"
                elif key == "chrome_cookies" and cookie_read_failed(lower) and (
                        "cookie" in lower or "database is locked" in lower):
                    self.excluded[key] = "chrome_cookie_database_unreadable"
                elif key.startswith("ios_formats:"):
                    if success:
                        self.ios_failed_videos.pop(key, None)
                        self.excluded.pop(key, None)
                    elif (not has_auth_error(lower) and
                          ("requested format is not available" in lower or "only images are available" in lower)):
                        videos = self.ios_failed_videos.setdefault(key, set())
                        videos.add(url)
                        # A single video's catalog is not evidence about other videos.
                        if len(videos) >= 3:
                            self.excluded[key] = "ios_format_unavailable_on_three_videos"


def reset_queue_capabilities(app):
    with _STATE_LOCK:
        app.youtube_queue_capabilities = QueueCapabilities()


def queue_capabilities(app):
    with _STATE_LOCK:
        state = getattr(app, "youtube_queue_capabilities", None)
        if state is None:
            state = app.youtube_queue_capabilities = QueueCapabilities()
        return state


class RetryBudget:
    """Only failed work counts: a large, progressing successful file is safe."""
    def __init__(self):
        self.failed_attempts = 0
        self.failed_elapsed_sec = 0.0

    def failed(self, elapsed):
        self.failed_attempts += 1
        self.failed_elapsed_sec += max(0.0, elapsed)

    def exhausted(self):
        return (self.failed_attempts >= YT_DLP_MAX_FAILED_ATTEMPTS or
                self.failed_elapsed_sec >= YT_DLP_FAILED_ATTEMPT_BUDGET_SEC)
