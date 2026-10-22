"""Gate that runs before EVERY render: one render at a time, enough disk and RAM.

Usage from code:   with preflight.render_slot(source_path): ...render...
Usage from shell:  python3 engines/preflight.py raw/clip.mp4
"""
import contextlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sysinfo as si  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMITS_PATH = os.path.join(ROOT, "engines", "machine_limits.json")
LOCK_PATH = os.path.join(ROOT, "work", ".render.lock")


class PreflightError(RuntimeError):
    pass


def load_limits():
    if not os.path.exists(LIMITS_PATH):
        raise PreflightError("No engines/machine_limits.json. Run: python3 engines/check_env.py")
    with open(LIMITS_PATH) as f:
        return json.load(f)["limits"]


def check(source_path=None):
    """Return a dict of measured numbers; raise PreflightError if a gate fails."""
    L = load_limits()
    _, avail = si.ram_bytes()
    disk = si.disk_free_bytes(ROOT)
    src = os.path.getsize(source_path) if source_path else 0
    need_disk = max(L["min_free_disk_gb"] * si.GB, L["disk_multiple_of_source"] * src)
    report = {
        "free_ram_gb": round(avail / si.GB, 1), "need_ram_gb": L["min_free_ram_gb"],
        "free_disk_gb": round(disk / si.GB, 1), "need_disk_gb": round(need_disk / si.GB, 1),
        "source_gb": round(src / si.GB, 2),
    }
    problems = []
    if avail < L["min_free_ram_gb"] * si.GB:
        problems.append(f"RAM {report['free_ram_gb']} GB free < {L['min_free_ram_gb']} GB needed")
    if disk < need_disk:
        problems.append(f"disk {report['free_disk_gb']} GB free < {report['need_disk_gb']} GB needed")
    if problems:
        raise PreflightError("; ".join(problems))
    return report


@contextlib.contextmanager
def render_slot(source_path=None):
    """Hold the single render lock for the duration of one render."""
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        with open(LOCK_PATH) as f:
            raise PreflightError(f"another render is running (lock held by pid {f.read().strip()}); "
                                 f"delete {LOCK_PATH} only if that process is gone")
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        report = check(source_path)
        print(f"preflight OK: RAM {report['free_ram_gb']}/{report['need_ram_gb']} GB, "
              f"disk {report['free_disk_gb']}/{report['need_disk_gb']} GB")
        yield report
    finally:
        os.remove(LOCK_PATH)


if __name__ == "__main__":
    try:
        print(json.dumps(check(sys.argv[1] if len(sys.argv) > 1 else None), indent=2))
    except PreflightError as e:
        print(f"PREFLIGHT FAIL: {e}")
        sys.exit(1)
