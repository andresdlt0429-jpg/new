"""Intake check (Phase 10): a fast look at raw clips before ingest, with reject codes.

  python3 engines/intake.py raw/office raw/sampson raw/broll      folders or files

Rules, codes and thresholds: spec/intake-rules.json (REJECT = do not use, WARN = usable, noted).
Per clip it reads the container (ffprobe), 5 sample frames (brightness, faces, fingerprint) and,
for talking-head clips, the audio loudness and true peak. Nothing is converted or written to raw/.
Report: out/intake/intake.md + intake.json. Exit 1 if any clip is rejected, else 0.
"""
import json
import os
import re
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import media  # noqa: E402

ROOT = media.ROOT
with open(os.path.join(ROOT, "spec", "intake-rules.json")) as f:
    RULES = json.load(f)
CODES = {c["code"]: c for c in RULES["codes"]}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".mts"}


def ffprobe(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                       capture_output=True, text=True)
    return json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else None


def ratio(s):
    try:
        a, b = s.split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, AttributeError):
        return 0.0


def frame(path, t, w, h):
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
                          "-frames:v", "1", "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3) if len(raw) == w * h * 3 else None


def loudness(path):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    s = err[err.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", s)
    tp = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", s)
    f = lambda m: None if not m or m.group(1) == "-inf" else float(m.group(1))  # noqa: E731
    return f(i), f(tp)


_det = None


def faces(rgb):
    global _det
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision
    if _det is None:
        _det = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=media.FACE_MODEL), min_detection_confidence=media.FACE_MIN_SCORE))
    return len(_det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))).detections)


def check_clip(path):
    r = {"file": os.path.relpath(path, ROOT), "codes": []}
    folder = os.path.basename(os.path.dirname(path))
    role = RULES["role_from_folder"].get(folder, "auto")
    j = ffprobe(path)
    v = next((s for s in (j or {}).get("streams", []) if s["codec_type"] == "video"), None)
    if not v:
        r.update(role=role if role != "auto" else "broll", codes=[code("I01", "no video stream")])
        return r
    rot = 0
    for sd in v.get("side_data_list", []):
        rot = int(sd.get("rotation", 0) or 0)
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) == 90:
        w, h = h, w
    dur = float(j["format"].get("duration") or v.get("duration") or 0)
    has_audio = any(s["codec_type"] == "audio" for s in j["streams"])
    avg, nominal = ratio(v.get("avg_frame_rate")), ratio(v.get("r_frame_rate"))
    transfer = v.get("color_transfer") or ""
    r.update(w=w, h=h, duration_s=round(dur, 2), codec=v.get("codec_name"), fps=round(avg, 3),
             has_audio=has_audio, transfer=transfer or None, mb=round(int(j["format"].get("size", 0)) / 1e6, 1))
    # sample frames, small, long side 640
    sc = 640 / max(w, h)
    sw, shh = max(2, int(w * sc) // 2 * 2), max(2, int(h * sc) // 2 * 2)
    n = RULES["sample_frames"]
    ts = [dur * (k + 0.5) / n for k in range(n)]
    frames = [frame(path, t, sw, shh) for t in ts]
    good = [f for f in frames if f is not None]
    if not good:
        r.update(role=role if role != "auto" else "broll", codes=[code("I01", "sample frames fail to decode")])
        return r
    luma = [float((0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]).mean()) for f in good]
    face_share = sum(faces(f) > 0 for f in good) / len(good)
    small = np.asarray(good[0].reshape(shh, sw, 3).mean(axis=2))
    fp = small[:: max(1, shh // 8), :: max(1, sw // 8)][:8, :8]
    r.update(luma_median=round(float(np.median(luma)), 1), face_share=round(face_share, 2),
             fingerprint="".join("1" if x > fp.mean() else "0" for x in fp.ravel()))
    if role == "auto":
        role = "talking_head" if face_share >= 0.5 and has_audio else "broll"
    r["role"] = role
    rules = {c: CODES[c] for c in CODES if role in CODES[c]["roles"]}
    out = []
    if "I02" in rules and not has_audio:
        out.append(code("I02", "no audio stream"))
    if min(w, h) < rules["I03"]["min_short_side"]:
        out.append(code("I03", f"{w}x{h}: short side {min(w, h)} px < {rules['I03']['min_short_side']}"))
    if "I04" in rules and w > h:
        crop_w = round(h * 9 / 16)
        if crop_w < rules["I04"]["min_crop_w"]:
            out.append(code("I04", f"landscape {w}x{h}: 9:16 crop is {crop_w} px wide (< {rules['I04']['min_crop_w']})"))
    if "I05" in rules and w > h:
        out.append(code("I05", f"{w}x{h} is landscape"))
    mn = rules["I06"]["min_s"][role]
    if dur < mn:
        out.append(code("I06", f"{dur:.1f}s < {mn}s"))
    std = rules["I07"]["standard"]
    if not any(abs(avg - s) < 0.02 for s in std) or (nominal and abs(avg - nominal) / nominal > 0.01):
        out.append(code("I07", f"avg {avg:.3f} fps, nominal {nominal:.3f} fps"))
    if transfer in ("smpte2084", "arib-std-b67"):
        out.append(code("I08", f"transfer {transfer} ({'HLG' if transfer == 'arib-std-b67' else 'PQ'})"))
    if role == "talking_head" and has_audio:
        lufs, tp = loudness(path)
        r.update(lufs=lufs, true_peak_dbtp=tp)
        if tp is not None and tp > rules["I09"]["max_true_peak_dbtp"]:
            out.append(code("I09", f"true peak {tp:+.1f} dBTP"))
        q = rules["I10"]
        if lufs is None or lufs < q["reject_lufs"]:
            out.append(code("I10", f"{lufs} LUFS < {q['reject_lufs']} (too quiet to use)", "REJECT"))
        elif lufs < q["warn_lufs"]:
            out.append(code("I10", f"{lufs} LUFS < {q['warn_lufs']} (raised in the edit)"))
    d = rules["I11"]
    lm = float(np.median(luma))
    if lm < d["reject_luma"]:
        out.append(code("I11", f"median brightness {lm:.0f}/255 < {d['reject_luma']}", "REJECT"))
    elif lm < d["warn_luma"]:
        out.append(code("I11", f"median brightness {lm:.0f}/255 < {d['warn_luma']}"))
    if "I12" in rules and face_share < rules["I12"]["min_face_share"]:
        out.append(code("I12", f"face in {face_share:.0%} of sample frames"))
    r["codes"] = out
    return r


def code(c, detail, level=None):
    return {"code": c, "name": CODES[c]["name"], "level": level or CODES[c]["level"], "detail": detail}


def duplicates(rows):
    seen = {}
    for r in rows:
        if "fingerprint" not in r:
            continue
        key = (r["w"], r["h"], round(r["duration_s"] / 0.05))
        for k2, first in seen.items():
            if k2[:2] == key[:2] and abs(k2[2] - key[2]) <= 1 and \
                    sum(a != b for a, b in zip(first["fingerprint"], r["fingerprint"])) <= 4:
                r["codes"].append(code("I13", f"same as {first['file']}"))
                break
        else:
            seen[key] = r


def verdict(r):
    lv = {c["level"] for c in r["codes"]}
    return "REJECT" if "REJECT" in lv else "WARN" if "WARN" in lv else "OK"


def report(rows, secs):
    out = os.path.join(ROOT, "out", "intake")
    os.makedirs(out, exist_ok=True)
    counts = {k: sum(verdict(r) == k for r in rows) for k in ("OK", "WARN", "REJECT")}
    md = ["# Intake check", "", f"{len(rows)} clips · {counts['OK']} OK · {counts['WARN']} WARN · "
          f"{counts['REJECT']} REJECT · {secs:.0f} s · rules: `spec/intake-rules.json`", "",
          "| Clip | Role | Size | Length | fps | Audio | Light | Face | Verdict | Codes |",
          "| :-- | :-- | :-- | --: | --: | :-- | --: | --: | :-- | :-- |"]
    for r in rows:
        aud = (f"{r['lufs']} LUFS / {r['true_peak_dbtp']:+.1f} dBTP" if r.get("lufs") is not None and
               r.get("true_peak_dbtp") is not None else ("yes" if r.get("has_audio") else "none"))
        md.append(f"| `{r['file']}` | {r.get('role', '')} | {r.get('w', '')}x{r.get('h', '')} | "
                  f"{r.get('duration_s', '')} s | {r.get('fps', '')} | {aud} | {r.get('luma_median', '')} | "
                  f"{r.get('face_share', 0):.0%} | **{verdict(r)}** | "
                  + "<br>".join(f"{c['code']} {c['name']}: {c['detail']}" for c in r["codes"]) + " |")
    md += ["", "## Codes", "", "| Code | Name | Level | Roles | Rule |", "| :-- | :-- | :-- | :-- | :-- |"]
    md += [f"| {c['code']} | {c['name']} | {c['level']} | {', '.join(c['roles'])} | {c['rule']} |" for c in RULES["codes"]]
    with open(os.path.join(out, "intake.md"), "w") as f:
        f.write("\n".join(md) + "\n")
    with open(os.path.join(out, "intake.json"), "w") as f:
        json.dump({"counts": counts, "seconds": round(secs, 1),
                   "clips": [dict(r, verdict=verdict(r)) for r in rows]}, f, indent=1)
    return os.path.join(out, "intake.md"), counts


def main(paths):
    files = []
    for p in paths:
        p = os.path.join(ROOT, p) if not os.path.isabs(p) else p
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if os.path.splitext(f)[1].lower() in VIDEO_EXT)
        else:
            files.append(p)
    t = time.time()
    rows = []
    for fpath in files:
        r = check_clip(fpath)
        rows.append(r)
        v = verdict(r)
        print(f"{v:6s} {r['file']:42s} {r.get('role', ''):12s} "
              + "; ".join(f"{c['code']} {c['name']}" for c in r["codes"]))
    duplicates(rows)
    p, counts = report(rows, time.time() - t)
    print(f"\n{counts}  -> {os.path.relpath(p, ROOT)}  ({time.time() - t:.0f} s)")
    sys.exit(1 if counts["REJECT"] else 0)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
