"""Cross-platform machine facts (macOS + Linux), stdlib only.

Used by check_env.py (one-time report) and preflight.py (before every render).
"""
import os
import platform
import re
import resource
import shutil
import subprocess
import time

GB = 1024 ** 3


def run(cmd):
    """Run a command, return stripped stdout+stderr, or None if it can't run."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def first_line(text):
    return text.splitlines()[0] if text else None


def ram_bytes():
    """(total, available) in bytes."""
    if platform.system() == "Darwin":
        total = int(run(["sysctl", "-n", "hw.memsize"]))
        vm = run(["vm_stat"]) or ""
        page = int(re.search(r"page size of (\d+)", vm).group(1))
        pages = {k: int(v) for k, v in re.findall(r"Pages (free|inactive|speculative|purgeable):\s+(\d+)", vm)}
        return total, sum(pages.values()) * page
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            info[k] = int(v.split()[0]) * 1024
    return info["MemTotal"], info["MemAvailable"]


def disk_free_bytes(path="."):
    return shutil.disk_usage(path).free


def cpu():
    if platform.system() == "Darwin":
        name = run(["sysctl", "-n", "machdep.cpu.brand_string"])
    else:
        name = None
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    break
    return {"name": name, "cores": os.cpu_count(), "arch": platform.machine()}


def gpu():
    """Return a list of human-readable GPU descriptions (empty = none usable)."""
    found = []
    smi = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if smi and "failed" not in smi.lower() and "not found" not in smi.lower():
        found += [f"NVIDIA {l.strip()}" for l in smi.splitlines()]
    if platform.system() == "Darwin":
        sp = run(["system_profiler", "SPDisplaysDataType"]) or ""
        found += [m.strip() for m in re.findall(r"Chipset Model:\s*(.+)", sp)]
    elif os.path.isdir("/dev/dri"):
        found.append("DRM render node present (/dev/dri) - VAAPI may work")
    return found


def run_measured(cmd, **kw):
    """Run a command to completion; return its own peak RSS in bytes.

    Linux: polls /proc/<pid>/status VmHWM, which is per-process and starts fresh
    at exec. RUSAGE_CHILDREN is NOT used there because a forked child inherits
    the parent's high-water mark (so ffmpeg would report Whisper's RAM).
    macOS: falls back to RUSAGE_CHILDREN, an upper bound."""
    p = subprocess.Popen(cmd, **kw)
    peak = 0
    status = f"/proc/{p.pid}/status"
    while p.poll() is None:
        try:
            with open(status) as f:
                for line in f:
                    if line.startswith("VmHWM:"):
                        peak = max(peak, int(line.split()[1]) * 1024)
        except OSError:
            pass
        time.sleep(0.1)
    if p.returncode:
        raise subprocess.CalledProcessError(p.returncode, cmd)
    return peak or child_peak_rss_bytes()


def child_peak_rss_bytes():
    """Peak RSS of the largest finished child process so far (ffmpeg etc)."""
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return peak if platform.system() == "Darwin" else peak * 1024


def self_peak_rss_bytes():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if platform.system() == "Darwin" else peak * 1024
