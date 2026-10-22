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
import sys
import time

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import media  # noqa: E402
import preflight  # noqa: E402
import sysinfo as si  # noqa: E402
from media import (detect_faces, load_audio_16k, make_working_copy, probe,  # noqa: E402
                   sh, transcribe)

ROOT = media.ROOT

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

    face_stats, best, _ = detect_faces(work, w, h)
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
