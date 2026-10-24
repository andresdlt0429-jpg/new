"""Probing, working copies (SOP-11a/e), audio extraction and the ffmpeg helpers every stage shares."""
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from . import config

SR = 16000


def run(cmd, **kw):
    """Run a command, raising with its stderr when it fails."""
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({r.returncode}):\n{r.stderr[-3000:]}")
    return r


def ff(*args):
    return run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args])


def filter_path(p):
    """A path inside an ffmpeg filter argument (ass=..., fontsdir=...): escape the characters filters parse."""
    s = str(Path(p).resolve()).replace("\\", "/")
    return s.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path]).stdout
    j = json.loads(out)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    info = dict(duration=float(j["format"].get("duration", 0)), size=int(j["format"].get("size", 0)))
    if v:
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if abs(rot) % 180 == 90:
            w, h = h, w
        num, den = (v.get("r_frame_rate") or "30/1").split("/")
        info.update(width=w, height=h, fps=float(num) / float(den or 1), transfer=v.get("color_transfer", ""),
                    pix_fmt=v.get("pix_fmt", ""), video_start=float(v.get("start_time", 0) or 0))
    if a:
        info["audio_start"] = float(a.get("start_time", 0) or 0)
    return info


def is_hdr(info):
    return info.get("transfer") in ("arib-std-b67", "smpte2084")


def working_copy(src, work, start=None, dur=None):
    """SOP-11a §2 / SOP-11e rule 1: clips above 1440 become a 1440-short-side, 30 fps, CRF 14, lanczos working copy.
    1080p and 1440p clips are used as they are. HDR (HLG/PQ) is tone-mapped to BT.709 (SOP-11e rule 5).
    Returns (path, offset): source time t is copy time t - offset."""
    src = Path(src)
    info = probe(src)
    short = min(info["width"], info["height"])
    needs = short > config.WORK_SHORT or info["fps"] > 31 or is_hdr(info)
    if not needs and start is None:
        return src, 0.0
    key = hashlib.sha1(f"{src.resolve()}|{info['size']}|{start}|{dur}".encode()).hexdigest()[:10]
    out = Path(work) / "proxy" / f"{src.stem}.{key}.mov"
    off = float(start or 0.0)
    if out.exists():
        return out, off
    out.parent.mkdir(parents=True, exist_ok=True)
    target = min(short, config.WORK_SHORT)
    if info["width"] <= info["height"]:
        scale = f"scale={target}:-2:flags=lanczos"
    else:
        scale = f"scale=-2:{target}:flags=lanczos"
    chain = []
    if is_hdr(info):
        chain.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
                     "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    chain += [f"{scale}:out_range=tv:out_color_matrix=bt709", f"fps={config.FPS}", "format=yuv420p"]
    rng = []
    if start is not None:
        rng += ["-ss", f"{start:.3f}"]
    if dur is not None:
        rng += ["-t", f"{dur:.3f}"]
    ff(*rng, "-i", src, "-vf", ",".join(chain), "-c:v", "libx264", "-crf", str(config.WORK_CRF), "-preset", "veryfast",
       "-c:a", "pcm_s16le", "-ar", "48000", out)
    return out, off


def audio16k(path, start=None, dur=None):
    """Mono 16 kHz float audio on the file's clock (README "one thing that will bite again": phone audio can start
    ~0.3 s after video; whisper counts from the first audio sample, cuts count from the file clock)."""
    rng = []
    if start is not None:
        rng += ["-ss", f"{start:.3f}"]
    if dur is not None:
        rng += ["-t", f"{dur:.3f}"]
    r = subprocess.run(["ffmpeg", "-v", "error", *rng, "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace")[-2000:])
    a = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0
    if start is None:
        info = probe(path)
        lead = info.get("audio_start", 0.0) - info.get("video_start", 0.0)
        if lead > 0.005:   # on_file_clock(): pad so sample 0 sits at file time 0
            a = np.concatenate([np.zeros(int(lead * SR), np.float32), a])
    return a


def frame(path, t, width=None):
    """One RGB frame as a numpy array."""
    vf = f"scale={width}:-2" if width else "null"
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", str(path), "-frames:v", "1", "-vf", vf,
                        "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    from PIL import Image
    import io
    return np.asarray(Image.open(io.BytesIO(r.stdout)).convert("RGB"))


def loudness(path):
    """(integrated LUFS, true peak dBFS) via ebur128."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    tail = r[r.rindex("Summary"):]
    import re
    i = float(re.search(r"I:\s+(-?[\d.]+)", tail).group(1))
    p = float(re.search(r"Peak:\s+(-?[\d.]+|-inf)", tail).group(1).replace("-inf", "-99"))
    return i, p
