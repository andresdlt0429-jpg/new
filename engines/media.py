"""Shared media helpers: probe, 1440p working copy, audio decode, Whisper, faces.

Used by ingest.py and smoke_test.py so there is one copy of each.
"""
import json
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sysinfo as si  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Full-range, not short-range: on Andy's real walk-and-talk footage (face ~5% of frame
# height) short-range found 3/24 frames, full-range 24/24; close-ups 12/12 either way.
FACE_MODEL = os.path.join(ROOT, "engines", "models", "blaze_face_full_range.tflite")
# Phase 2: a no-person window-cleaning frame scored 0.533; a real face scored 0.97.
FACE_MIN_SCORE = 0.6
FACE_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
                  "blaze_face_full_range/float16/latest/blaze_face_full_range.tflite")


def sh(cmd):
    subprocess.run(cmd, check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", path], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    rot = 0
    for sd in v.get("side_data_list", []):
        rot = int(sd.get("rotation", 0))
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) == 90:
        w, h = h, w
    return {"w": w, "h": h, "fps": v["r_frame_rate"], "codec": v["codec_name"],
            "duration": float(j["format"]["duration"]),
            "has_audio": any(s["codec_type"] == "audio" for s in j["streams"])}


def make_working_copy(src, dst, info, L):
    """Fit inside 1440p (either orientation). Never upscale."""
    max_long, max_short = L["working_max_h"], L["working_max_w"]
    long_edge, short_edge = max(info["w"], info["h"]), min(info["w"], info["h"])
    scale = min(1.0, max_long / long_edge, max_short / short_edge)
    w, h = int(info["w"] * scale) // 2 * 2, int(info["h"] * scale) // 2 * 2
    peak = si.run_measured(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
        "-vf", f"scale={w}:{h}:flags=lanczos,fps=30", "-c:v", "libx264", "-preset", "fast",
        "-crf", "16", "-g", "30", "-pix_fmt", "yuv420p", "-threads", str(L["encode_threads"]),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", dst])
    verify_working_copy(dst, info, L)
    return w, h, scale, peak


def verify_working_copy(dst, info, L):
    """Andy 2026-10-22: "make sure 1440p". Measure the file, don't trust the math."""
    got = probe(dst)
    portrait = info["h"] >= info["w"]
    want = (L["working_max_w"], L["working_max_h"]) if portrait else (L["working_max_h"], L["working_max_w"])
    if min(info["w"], info["h"]) >= L["working_max_w"] and (got["w"], got["h"]) != want:
        raise RuntimeError(f"working copy is {got['w']}x{got['h']}, expected 1440p {want[0]}x{want[1]}")
    if max(got["w"], got["h"]) > L["working_max_h"]:
        raise RuntimeError(f"working copy is {got['w']}x{got['h']}, above 1440p")
    print(f"verified working copy (ffprobe): {got['w']}x{got['h']} @ {got['fps']}")


def load_audio_16k(path):
    """Decode with ffmpeg to 16 kHz mono float32. faster-whisper 1.2.1's own file
    decoder passes av.open(metadata_errors=...), which PyAV 19 removed, so we hand
    it the waveform instead (documented input type: np.ndarray)."""
    pcm = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-ac", "1",
                          "-ar", "16000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(pcm, np.float32)


def transcribe(audio, model_name, duration):
    from faster_whisper import WhisperModel
    t = time.time()
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    load = time.time() - t
    t = time.time()
    segments, info = model.transcribe(audio, word_timestamps=True, vad_filter=True, language="en")
    # float(): Whisper returns numpy scalars, which json cannot write.
    words = [{"w": w.word.strip(), "start": round(float(w.start), 2), "end": round(float(w.end), 2),
              "p": round(float(w.probability), 3)} for s in segments for w in s.words]
    run = time.time() - t
    return words, {"model": model_name, "load_s": round(load, 1), "transcribe_s": round(run, 1),
                   "rtf": round(run / duration, 2), "words": len(words),
                   "mean_word_prob": round(float(np.mean([w["p"] for w in words])), 3) if words else None}


def read_frames(path, w, h, fps, limit=None):
    """Yield (t, rgb) at w x h (ffmpeg scales if the file differs). limit = max frames,
    applied inside ffmpeg so the pipe is never cut mid-write."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
           "-vf", f"fps={fps},scale={w}:{h}:flags=bilinear"]
    if limit:
        cmd += ["-frames:v", str(limit)]
    p = subprocess.Popen(cmd + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    n = w * h * 3
    i = 0
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        yield i / fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        i += 1
    p.wait()


def detect_faces(path, w, h):
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision
    if not os.path.exists(FACE_MODEL):
        os.makedirs(os.path.dirname(FACE_MODEL), exist_ok=True)
        sh(["curl", "-sSfL", "-o", FACE_MODEL, FACE_MODEL_URL])
    det = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=FACE_MODEL), min_detection_confidence=FACE_MIN_SCORE))
    hits, frames, best = [], 0, None
    t = time.time()
    for ts, rgb in read_frames(path, w, h, 2):
        frames += 1
        res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
        if res.detections:
            d = max(res.detections, key=lambda d: d.categories[0].score)
            b = d.bounding_box
            score = d.categories[0].score
            hits.append({"t": ts, "x": b.origin_x, "y": b.origin_y, "w": b.width, "h": b.height,
                         "score": round(score, 3)})
            if best is None or score > best[0]:
                best = (score, ts, rgb.copy(), hits[-1])
    det.close()
    stats = {"frames_sampled": frames, "frames_with_face": len(hits),
             "face_rate": round(len(hits) / frames, 2) if frames else 0,
             "seconds": round(time.time() - t, 1)}
    if hits:
        stats["mean_center_x_pct"] = round(float(np.mean([(f["x"] + f["w"] / 2) / w for f in hits])) * 100, 1)
        stats["mean_center_y_pct"] = round(float(np.mean([(f["y"] + f["h"] / 2) / h for f in hits])) * 100, 1)
        stats["mean_face_height_pct"] = round(float(np.mean([f["h"] / h for f in hits])) * 100, 1)
    return stats, best, hits
