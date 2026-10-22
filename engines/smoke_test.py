"""Phase 1 smoke test on one real clip: proves every tool works end to end.

  python3 engines/smoke_test.py raw/clip.mp4

1. preflight (disk, RAM, single render lock)
2. working copy in proxy/ (4K -> 1440p, never upscale, CFR 30, 1s keyframes)
3. faster-whisper word timestamps (small vs large-v3 speed, CPU int8)
4. MediaPipe face detection (2 fps sample)
5. libass caption burn-in render, client style from brand.json
6. proof stills + measured numbers in out/phase1-proof/
"""
import json
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preflight  # noqa: E402
import sysinfo as si  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACE_MODEL = os.path.join(ROOT, "engines", "models", "blaze_face_short_range.tflite")
FACE_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
                  "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite")


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
    words = [{"w": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2),
              "p": round(w.probability, 3)} for s in segments for w in s.words]
    run = time.time() - t
    return words, {"model": model_name, "load_s": round(load, 1), "transcribe_s": round(run, 1),
                   "rtf": round(run / duration, 2), "words": len(words),
                   "mean_word_prob": round(float(np.mean([w["p"] for w in words])), 3) if words else None}


def read_frames(path, w, h, fps):
    p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
                          "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
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
        base_options=BaseOptions(model_asset_path=FACE_MODEL), min_detection_confidence=0.5))
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
    return stats, best


def ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def write_client_ass(words, path, w, h, brand):
    """Client style: plain white, no color coding. Groups words into short lines."""
    style = brand["captions"]["client_ads"]
    font = brand["fonts"]["caption"] or brand["fonts"]["fallback_caption"]
    size = round(h * style["size_pct_of_height"] / 100)
    margin_v = round(h * style["margin_bottom_pct"] / 100)
    lines, cur = [], []
    for wd in words:
        cur.append(wd)
        if len(cur) >= style["max_words_per_line"] or wd["w"].endswith((".", "?", "!", ",")):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    out = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}",
           "WrapStyle: 2", "", "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
           "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
           "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
           f"Style: Client,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,"
           f"100,100,0,0,1,{round(size * 0.08)},{round(size * 0.04)},2,60,60,{margin_v},1",
           "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for i, ln in enumerate(lines):
        start = ln[0]["start"]
        end = lines[i + 1][0]["start"] if i + 1 < len(lines) else ln[-1]["end"] + 0.3
        text = " ".join(x["w"] for x in ln)
        out.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Client,,0,0,0,,{text}")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    return lines, font, size


def record_real_clip_numbers(R):
    """Keep the real-clip speeds next to the synthetic worst case in machine_limits.json."""
    with open(preflight.LIMITS_PATH) as f:
        m = json.load(f)
    m["measured_real_clip"] = {
        "source": R["source"], "working_copy": R["working_copy"],
        "caption_render": {k: R["caption_render"][k] for k in
                           ["fps", "est_seconds_per_output_minute", "ffmpeg_peak_ram_gb"]},
        "whisper": R["whisper"], "python_whisper_peak_ram_gb": R["peak_python_ram_gb"]}
    with open(preflight.LIMITS_PATH, "w") as f:
        json.dump(m, f, indent=2)


def frame_at(path, t, dst):
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", path,
        "-frames:v", "1", dst])


def main(src):
    stem = os.path.splitext(os.path.basename(src))[0]
    proof = os.path.join(ROOT, "out", "phase1-proof")
    for d in ["proxy", "work", proof]:
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    with open(os.path.join(ROOT, "brand.json")) as f:
        brand = json.load(f)
    L = preflight.load_limits()
    R = {"source": os.path.relpath(src, ROOT)}

    info = probe(src)
    R["source_info"] = info
    print(f"source: {info}")

    work = os.path.join(ROOT, "proxy", f"{stem}_work.mp4")
    with preflight.render_slot(src) as pf:
        R["preflight_working_copy"] = pf
        t = time.time()
        w, h, scale, peak = make_working_copy(src, work, info, L)
        R["working_copy"] = {"path": os.path.relpath(work, ROOT), "w": w, "h": h,
                             "scale": round(scale, 3), "seconds": round(time.time() - t, 1),
                             "ffmpeg_peak_ram_gb": round(peak / si.GB, 2)}
    print(f"working copy: {R['working_copy']}")

    audio = load_audio_16k(work)
    R["whisper"] = []
    words = None
    for model_name in ["small", L["whisper_model"]]:
        wds, stats = transcribe(audio, model_name, info["duration"])
        R["whisper"].append(stats)
        print(f"whisper: {stats}")
        words = wds  # keep the last (largest) model's words
    with open(os.path.join(ROOT, "work", f"{stem}.words.json"), "w") as f:
        json.dump(words, f, indent=1)
    R["transcript"] = " ".join(x["w"] for x in words)

    face_stats, best = detect_faces(work, w, h)
    R["faces"] = face_stats
    print(f"faces: {face_stats}")

    if not words:
        print("no speech detected: skipping caption render (nothing was said, so nothing goes on screen)")
        with open(os.path.join(proof, f"report_{stem}.json"), "w") as f:
            json.dump(R, f, indent=2)
        return

    ass = os.path.join(ROOT, "work", f"{stem}.client.ass")
    lines, font, size = write_client_ass(words, ass, w, h, brand)
    out = os.path.join(ROOT, "out", f"{stem}_phase1_captions.mp4")
    with preflight.render_slot(work) as pf:
        R["preflight_render"] = pf
        t = time.time()
        # Run inside work/ so the ass filter path needs no escaping.
        render_peak = si.run_measured(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", work,
                        "-vf", f"ass={os.path.basename(ass)}", "-c:v", L["encoder"],
                        "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-threads", str(L["encode_threads"]), "-c:a", "copy", out],
                       cwd=os.path.join(ROOT, "work"))
        dt = time.time() - t
    R["caption_render"] = {"path": os.path.relpath(out, ROOT), "seconds": round(dt, 1),
                           "fps": round(info["duration"] * 30 / dt, 1),
                           "est_seconds_per_output_minute": round(60 * dt / info["duration"]),
                           "ffmpeg_peak_ram_gb": round(render_peak / si.GB, 2),
                           "caption_lines": len(lines), "font": font, "font_px": size,
                           "out_mb": round(os.path.getsize(out) / 1e6, 1)}
    R["peak_python_ram_gb"] = round(si.self_peak_rss_bytes() / si.GB, 2)
    record_real_clip_numbers(R)
    print(f"caption render: {R['caption_render']}")

    # Proof still 1: working-copy frame with best face box.
    if best:
        _, ts, rgb, f = best
        img = Image.fromarray(rgb)
        d = ImageDraw.Draw(img)
        d.rectangle([f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]], outline=(0, 255, 0), width=6)
        d.text((f["x"], f["y"] + f["h"] + 12), f"face {f['score']:.2f} @ {ts:.1f}s", fill=(0, 255, 0),
               font=ImageFont.load_default(size=max(24, h // 50)))
        img.save(os.path.join(proof, "1_face_detect.png"))
    # Proof still 2: caption render frame mid-way through the 2nd caption line.
    ln = lines[min(1, len(lines) - 1)]
    frame_at(out, (ln[0]["start"] + ln[-1]["end"]) / 2, os.path.join(proof, "2_libass_caption.png"))
    # Proof still 3: 6-frame contact sheet of the caption render.
    tiles = []
    for i in range(6):
        p = os.path.join(ROOT, "work", f"_sheet{i}.png")
        frame_at(out, info["duration"] * (i + 0.5) / 6, p)
        tiles.append(Image.open(p).convert("RGB").resize((w // 4, h // 4)))
        os.remove(p)
    sheet = Image.new("RGB", (w // 4 * 3, h // 4 * 2))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, (i % 3 * (w // 4), i // 3 * (h // 4)))
    sheet.save(os.path.join(proof, "3_contact_sheet.png"))

    with open(os.path.join(proof, f"report_{stem}.json"), "w") as f:
        json.dump(R, f, indent=2)
    print(f"\ntranscript: {R['transcript']}")
    print(f"peak RAM: ffmpeg working copy {R['working_copy']['ffmpeg_peak_ram_gb']} GB, "
          f"ffmpeg caption render {R['caption_render']['ffmpeg_peak_ram_gb']} GB, "
          f"python+whisper {R['peak_python_ram_gb']} GB")
    print(f"proof: {os.path.relpath(proof, ROOT)}/")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(os.path.abspath(sys.argv[1]))
