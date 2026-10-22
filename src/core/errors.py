"""Shared application exceptions."""

from typing import Dict, Optional
import subprocess


class CommandTimeoutError(subprocess.TimeoutExpired):
    """TimeoutExpired with the watchdog cause and its effective threshold."""

    def __init__(self, command, timeout, *, reason, output=None, stderr=None):
        super().__init__(command, timeout, output=output, stderr=stderr)
        self.reason = reason


class CommandCancelledError(Exception):
    """Command was stopped by the user."""


class ProblematicDownloadSkipped(Exception):
    """Video was skipped because the current network path is too slow."""

    def __init__(self, url: str, reason: str, context: Optional[Dict] = None):
        super().__init__(reason)
        self.url = url
        self.reason = reason
        self.context = context or {}

    def to_log_item(self) -> Dict:
        return {"url": self.url, "reason": self.reason, "context": self.context}
