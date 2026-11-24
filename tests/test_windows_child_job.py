import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from src.core.process_control import ProcessControlMixin
from src.core.subprocess_runner import SubprocessRunnerMixin


class SyntheticRunner(ProcessControlMixin, SubprocessRunnerMixin):
    def __init__(self):
        self.active_processes = {}
        self.cancel_flag = threading.Event()
        self.process_lock = threading.Lock()
        self.subprocess_flags = subprocess.CREATE_NO_WINDOW
        for name in (
            "_command_to_string", "_set_ytdlp_runtime", "_remove_ytdlp_runtime",
            "_record_active_ytdlp_snapshot", "record_problem",
        ):
            setattr(self, name, Mock())
        self._utf8_subprocess_env = lambda: None


def fixture_command(role, root):
    return [sys.executable, "-B", "-m", "tests.test_windows_child_job", role, str(root)]


def wait_for(path, timeout=15):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(path.name)
        time.sleep(0.02)


@unittest.skipUnless(os.name == "nt", "Windows job objects")
class WindowsChildJobTests(unittest.TestCase):
    def test_hard_kill_stops_download_writer_and_its_helper(self):
        import _winapi

        wait_object_0 = 0
        process_terminate = 0x0001
        synchronize = 0x00100000
        with tempfile.TemporaryDirectory(prefix="Загрузка с пробелами ") as directory:
            root = Path(directory)
            launcher = subprocess.Popen(fixture_command("launcher", root))
            process_handles = []
            try:
                wait_for(root / "pids.ready")
                for pid in (root / "pids.ready").read_text().split():
                    process_handles.append(_winapi.OpenProcess(
                        synchronize | process_terminate, False, int(pid)
                    ))
                wait_for(root / "writer.ready")
                wait_for(root / "helper.ready")

                launcher.kill()
                launcher.wait(timeout=5)
                (root / "continue").touch()
                self.assertEqual(
                    [_winapi.WaitForSingleObject(handle, 2000) for handle in process_handles],
                    [wait_object_0, wait_object_0],
                    "The worker tree is still alive after the owning process was killed",
                )
                for name in ("writer", "helper"):
                    self.assertEqual((root / f"{name}.part").read_bytes(), b"initial")
            finally:
                if launcher.poll() is None:
                    launcher.kill()
                    launcher.wait(timeout=5)
                for handle in process_handles:
                    try:
                        if _winapi.WaitForSingleObject(handle, 0) != wait_object_0:
                            _winapi.TerminateProcess(handle, 1)
                            _winapi.WaitForSingleObject(handle, 5000)
                    finally:
                        _winapi.CloseHandle(handle)


def run_fixture(role, root):
    if role == "launcher":
        SyntheticRunner().run_command_streamed(fixture_command("writer", root), 30, "yt_dlp_download")
        return
    if role == "writer":
        child = subprocess.Popen(fixture_command("helper", root))
        pids = root / "pids.tmp"
        pids.write_text(f"{os.getpid()} {child.pid}")
        pids.replace(root / "pids.ready")

    partial = root / f"{role}.part"
    partial.write_bytes(b"initial")
    (root / f"{role}.ready").touch()
    wait_for(root / "continue")
    with partial.open("ab") as file:
        file.write(b"late write")


if __name__ == "__main__":
    run_fixture(sys.argv[1], Path(sys.argv[2]))
