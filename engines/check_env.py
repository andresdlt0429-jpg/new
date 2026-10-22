"""Phase 1 environment check.

Reports tool versions, hardware, and benchmarks a 1440p encode, then writes
the render limits for THIS machine to engines/machine_limits.json.
Re-run it on every machine that will render:  python3 engines/check_env.py
"""
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sysinfo as si  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMITS_PATH = os.path.join(ROOT, "engines", "machine_limits.json")

# 1440p working resolution (vertical 9:16 = 1440x2560). Never edit from 4K.
WORK_W, WORK_H = 1440, 2560
BENCH_SECONDS = 6
# Approximate resident RAM of faster-whisper on CPU with int8 weights.
WHISPER_RAM_GB = {"small": 1.0, "medium": 2.0, "large-v3": 4.8}  # large-v3 measured 4.8 GB peak in Phase 1


def tool_versions():
    v = {}
    v["node"] = si.run(["node", "--version"])
    v["npm"] = si.run(["npm", "--version"])
    v["ffmpeg"] = si.first_line(si.run(["ffmpeg", "-hide_banner", "-version"]))
    v["git"] = si.run(["git", "--version"])
    v["git_lfs"] = si.run(["git", "lfs", "version"])
    v["python"] = platform.python_version()
    for mod, attr in [("faster_whisper", "__version__"), ("PIL", "__version__"),
                      ("mediapipe", "__version__"), ("numpy", "__version__"),
                      ("ctranslate2", "__version__")]:
        try:
            v[mod] = getattr(__import__(mod), attr)
        except Exception as e:  # report, never hide
            v[mod] = f"MISSING ({e.__class__.__name__}: {e})"
    return v


def ffmpeg_features():
    conf = si.run(["ffmpeg", "-hide_banner", "-buildconf"]) or ""
    filters = si.run(["ffmpeg", "-hide_banner", "-filters"]) or ""
    return {
        "libass": "--enable-libass" in conf,
        "ass_filter": " ass " in filters,
        "subtitles_filter": " subtitles " in filters,
        "libx264": "--enable-libx264" in conf,
        "libfreetype": "--enable-libfreetype" in conf,
    }


def usable_hw_encoders():
    """An encoder can be compiled in but unusable (no GPU). Prove it with 1 frame."""
    ok = []
    for enc in ["h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_vaapi"]:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "color=black:s=256x256:d=0.1", "-frames:v", "1",
                            "-c:v", enc, "-f", "null", "-"], capture_output=True)
        if r.returncode == 0:
            ok.append(enc)
    return ok


def bench_encode(threads):
    """Encode BENCH_SECONDS of busy 1440x2560@30 video the way a final render will."""
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "bench.mp4")
        src = (f"testsrc2=s={WORK_W}x{WORK_H}:r=30:d={BENCH_SECONDS},"
               "noise=alls=12:allf=t+u")  # noise keeps it from being trivially compressible
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
               "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
               "-threads", str(threads), out]
        t = time.time()
        peak = si.run_measured(cmd)
        dt = time.time() - t
        return {"threads": threads, "seconds": round(dt, 2), "peak_ram_bytes": peak,
                "fps": round(BENCH_SECONDS * 30 / dt, 1),
                "out_mb": round(os.path.getsize(out) / 1e6, 1)}


def main():
    print("== DLT auto-editor: environment check ==")
    versions = tool_versions()
    feats = ffmpeg_features()
    total, avail = si.ram_bytes()
    disk = si.disk_free_bytes(ROOT)
    cpu = si.cpu()
    gpus = si.gpu()
    hw = usable_hw_encoders()

    print("\n-- Tool versions --")
    for k, v in versions.items():
        print(f"  {k:15s} {v}")
    print("\n-- ffmpeg features --")
    for k, v in feats.items():
        print(f"  {k:15s} {'yes' if v else 'NO'}")
    print("\n-- Hardware --")
    print(f"  CPU            {cpu['name']} ({cpu['cores']} threads, {cpu['arch']})")
    print(f"  RAM            {total / si.GB:.1f} GB total, {avail / si.GB:.1f} GB available")
    print(f"  Disk free      {disk / si.GB:.1f} GB (repo volume)")
    print(f"  GPU            {', '.join(gpus) if gpus else 'none detected'}")
    print(f"  HW encoders    {', '.join(hw) if hw else 'none usable (CPU libx264 only)'}")

    print(f"\n-- Benchmark (synthetic worst case: full-frame grain): {BENCH_SECONDS}s of 1440x2560@30, libx264 medium crf18 --")
    bench = bench_encode(cpu["cores"])
    peak_ffmpeg = bench.pop("peak_ram_bytes")
    print(f"  {bench['fps']} fps ({bench['seconds']}s wall), output {bench['out_mb']} MB, "
          f"peak ffmpeg RAM {peak_ffmpeg / si.GB:.2f} GB")

    # Whisper: biggest model that leaves at least half of total RAM free.
    whisper = "small"
    for name in ["medium", "large-v3"]:
        if WHISPER_RAM_GB[name] <= total / si.GB / 4:
            whisper = name
    sec_per_min = round(60 * 30 / bench["fps"])
    limits = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu, "ram_total_gb": round(total / si.GB, 1), "gpus": gpus,
        "hw_encoders": hw, "versions": versions, "ffmpeg_features": feats,
        "bench_1440p_encode": bench,
        "peak_ffmpeg_ram_gb": round(peak_ffmpeg / si.GB, 2),
        "limits": {
            "max_concurrent_renders": 1,
            "working_max_w": WORK_W, "working_max_h": WORK_H,
            "encoder": hw[0] if hw else "libx264",
            "encode_threads": cpu["cores"],
            "est_render_seconds_per_output_minute": sec_per_min,
            "whisper_model": whisper, "whisper_device": "cpu", "whisper_compute_type": "int8",
            # Preflight gates (preflight.py). RAM: 2x measured ffmpeg peak + whisper, min 2 GB.
            "min_free_ram_gb": round(max(2.0, 2 * peak_ffmpeg / si.GB + WHISPER_RAM_GB[whisper]), 1),
            # Disk: fixed floor, plus 3x the source size checked per render.
            "min_free_disk_gb": 10,
            "disk_multiple_of_source": 3,
        },
    }
    with open(LIMITS_PATH, "w") as f:
        json.dump(limits, f, indent=2)

    L = limits["limits"]
    print("\n-- Render limits for this machine --")
    print(f"  Renders at once        {L['max_concurrent_renders']}")
    print(f"  Working resolution     max {L['working_max_w']}x{L['working_max_h']} (1440p); 4K is downscaled first")
    print(f"  Encoder                {L['encoder']} x{L['encode_threads']} threads")
    print(f"  Render speed           ~{sec_per_min}s per 1 min of 1440p output (worst case; smoke_test.py records real-clip speed)")
    print(f"  Whisper                {whisper} / cpu / int8")
    print(f"  Preflight gates        >= {L['min_free_ram_gb']} GB free RAM, "
          f">= {L['min_free_disk_gb']} GB free disk and >= {L['disk_multiple_of_source']}x source size")
    print(f"\nWrote {os.path.relpath(LIMITS_PATH, ROOT)}")

    missing = [k for k, v in versions.items() if v is None or str(v).startswith("MISSING")]
    if missing or not feats["libass"]:
        print(f"\nFAIL: missing {missing or ''} {'' if feats['libass'] else 'libass'}")
        sys.exit(1)
    print("PASS: all required tools present")


if __name__ == "__main__":
    main()
