"""Serialize use of the writable download archive across app processes."""

from contextlib import contextmanager
import errno
import os


class ProfileInUseError(Exception):
    pass


@contextmanager
def hold_download_profile(program_dir):
    # Keep the file in place. Removing it would let a process lock a different file.
    with (program_dir / ".download_profile.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ProfileInUseError from error
            raise
        yield
