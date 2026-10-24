"""One render at a time (SOP-11a rule 7, NOTES "Running renders"): a machine-wide lock the OS frees when the holder
exits, even if it is killed. Stills and --plan-only never take it."""
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def render_lock(root, wait=True):
    path = Path(root) / "work" / ".render.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    announced = False
    while True:
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if not wait:
                raise RuntimeError("another render holds the lock")
            if not announced:
                print("  another render is running; waiting for the lock (one render at a time)")
                announced = True
            time.sleep(5)
    try:
        _lower_priority()
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _lower_priority():
    """Below-normal priority (SOP-11a rule 7); ffmpeg children inherit it."""
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
        else:
            os.nice(5)
    except Exception:
        pass
