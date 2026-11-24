"""Tie external tools and their children to the downloader's lifetime on Windows."""

import ctypes
from ctypes import wintypes
import os
import subprocess
import threading


_CREATE_SUSPENDED = 0x00000004
_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_JOB_ACCESS = 0x0100 | 0x0001  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
_THREAD_SNAPSHOT = 0x00000004  # TH32CS_SNAPTHREAD
_THREAD_SUSPEND_RESUME = 0x0002
_NO_MORE_FILES = 18
_INVALID_HANDLE = wintypes.HANDLE(-1).value
_INVALID_SUSPEND_COUNT = wintypes.DWORD(-1).value


class _BasicJobLimits(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _ExtendedJobLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicJobLimits),
        ("io_counters", ctypes.c_ulonglong * 6),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("thread_id", wintypes.DWORD),
        ("process_id", wintypes.DWORD),
        ("base_priority", wintypes.LONG),
        ("delta_priority", wintypes.LONG),
        ("flags", wintypes.DWORD),
    ]


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _bind(name, result_type, *argument_types):
        function = getattr(_kernel32, name)
        function.argtypes = argument_types
        function.restype = result_type
        return function

    _create_job = _bind("CreateJobObjectW", wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
    _set_limits = _bind("SetInformationJobObject", wintypes.BOOL, wintypes.HANDLE,
                        ctypes.c_int, ctypes.POINTER(_ExtendedJobLimits), wintypes.DWORD)
    _assign_process = _bind("AssignProcessToJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
    _open_process = _bind("OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _snapshot_threads = _bind("CreateToolhelp32Snapshot", wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
    _first_thread = _bind("Thread32First", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
    _next_thread = _bind("Thread32Next", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
    _open_thread = _bind("OpenThread", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _resume_thread = _bind("ResumeThread", wintypes.DWORD, wintypes.HANDLE)
    _close_handle = _bind("CloseHandle", wintypes.BOOL, wintypes.HANDLE)


class _KillOnCloseJob:
    def __init__(self):
        self._lock = threading.Lock()
        self._handle = _create_job(None, None)  # Unnamed and not inheritable.
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

        limits = _ExtendedJobLimits()
        limits.basic.limit_flags = _KILL_ON_JOB_CLOSE
        if not _set_limits(self._handle, _JOB_EXTENDED_LIMIT_INFORMATION, limits, ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, pid):
        process = _open_process(_PROCESS_JOB_ACCESS, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _assign_process(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _close_handle(process)

    def close(self):
        with self._lock:
            if self._handle:
                if not _close_handle(self._handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._handle = None


def _resume_suspended_process(pid):
    # Popen closes the primary thread handle. Before the process starts, its
    # initial suspended thread is the only thread belonging to this PID.
    snapshot = _snapshot_threads(_THREAD_SNAPSHOT, 0)
    if snapshot == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ThreadEntry(size=ctypes.sizeof(_ThreadEntry))
        found = _first_thread(snapshot, entry)
        while found:
            if entry.process_id == pid:
                thread = _open_thread(_THREAD_SUSPEND_RESUME, False, entry.thread_id)
                if not thread:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    suspend_count = _resume_thread(thread)
                    if suspend_count == _INVALID_SUSPEND_COUNT:
                        raise ctypes.WinError(ctypes.get_last_error())
                    if suspend_count != 1:
                        raise RuntimeError(f"Unexpected suspend count for process {pid}: {suspend_count}")
                finally:
                    _close_handle(thread)
                return
            entry.size = ctypes.sizeof(_ThreadEntry)
            found = _next_thread(snapshot, entry)
        error_code = ctypes.get_last_error()
        if error_code != _NO_MORE_FILES:
            raise ctypes.WinError(error_code)
        raise ProcessLookupError(f"Suspended thread not found for process {pid}")
    finally:
        _close_handle(snapshot)


def launch_owned_process(command, *, creationflags=0, **kwargs):
    if os.name != "nt":
        return subprocess.Popen(command, creationflags=creationflags, **kwargs)

    job = _KillOnCloseJob()
    process = None
    try:
        process = subprocess.Popen(command, creationflags=creationflags | _CREATE_SUSPENDED, **kwargs)
        job.assign(process.pid)
        process._downloader_job = job
        _resume_suspended_process(process.pid)
        return process
    except BaseException as error:
        try:
            if process is not None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired) as cleanup_error:
                    error.add_note(f"Suspended process cleanup: {cleanup_error}")
                finally:
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        if pipe is not None:
                            pipe.close()
        finally:
            job.close()
        raise


def close_owned_process_job(process):
    job = getattr(process, "_downloader_job", None)
    if isinstance(job, _KillOnCloseJob):
        job.close()
        return True
    return False
