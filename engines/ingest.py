"""Phase 2 ingest: one raw clip -> everything the editor needs, in work/<stem>/.

  python3 engines/ingest.py raw/clip.mp4

  words.json       every word, start/end seconds, Whisper confidence
  transcript.txt   one line per sentence with timestamps
  numbers.json     rule 4 ledger: every number spoken, when, how sure
  pauses.json      gaps between words (cut candidates), lead-in and tail silence
  loudness.json    integrated LUFS, loudness range, true peak
  faces.json       face box every 0.5 s (feeds reframing)
  ingest.json      summary + flags for Andy
Proof still: out/phase2-proof/<stem>_timeline.png
"""
import json
import os
import re
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import media  # noqa: E402
import numbers_guard  # noqa: E402
import preflight  # noqa: E402

ROOT = media.ROOT
PAUSE_MIN = 0.30        # seconds of silence between words worth listing as a cut point
LOW_WORD_P = 0.50       # words Whisper was unsure of
CLIP_PEAK_DBTP = -0.5   # true peak above this = clipping risk (objective, not a style target)


def working_copy(src, info, L, stem):
    dst = os.path.join(ROOT, "proxy", f"{stem}_work.mp4")
    if os.path.exists(dst) and os.path.getmtime(dst) > os.path.getmtime(src):
        media.verify_working_copy(dst, info, L)  # raises if it is not right
        got = media.probe(dst)
        return dst, {"path": os.path.relpath(dst, ROOT), "w": got["w"], "h": got["h"], "reused": True}
    with preflight.render_slot(src):
        t = time.time()
        w, h, scale, peak = media.make_working_copy(src, dst, info, L)
    return dst, {"path": os.path.relpath(dst, ROOT), "w": w, "h": h, "scale": round(scale, 3),
                 "seconds": round(time.time() - t, 1), "reused": False}


def sentences(words):
    out, cur = [], []
    for wd in words:
        cur.append(wd)
        if wd["w"].endswith((".", "?", "!")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"start": s[0]["start"], "end": s[-1]["end"], "text": " ".join(x["w"] for x in s)} for s in out]


def pauses(words, duration):
    gaps = [{"after_word": i, "after": words[i]["w"], "start": words[i]["end"],
             "end": words[i + 1]["start"], "seconds": round(words[i + 1]["start"] - words[i]["end"], 2)}
            for i in range(len(words) - 1) if words[i + 1]["start"] - words[i]["end"] >= PAUSE_MIN]
    return {"min_pause_s": PAUSE_MIN,
            "lead_in_s": round(words[0]["start"], 2) if words else round(duration, 2),
            "tail_s": round(duration - words[-1]["end"], 2) if words else 0.0,
            "gaps": gaps}


def loudness(path):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    summary = err[err.rfind("Summary:"):]

    def grab(label):
        m = re.search(label + r":\s+(-?[\d.]+|-inf)", summary)
        return None if not m or m.group(1) == "-inf" else float(m.group(1))
    return {"integrated_lufs": grab("I"), "loudness_range_lu": grab("LRA"), "true_peak_dbtp": grab("Peak")}


def timeline_png(path, stem, work, info, wc, words, ledger, pz, faces, audio, report):
    W, PAD = 2400, 40
    font = ImageFont.load_default(size=22)
    small = ImageFont.load_default(size=18)
    big = ImageFont.load_default(size=32)
    dur = info["duration"]
    x_of = lambda t: PAD + (W - 2 * PAD) * t / dur  # noqa: E731

    # Thumbnail strip from the working copy.
    n_thumb = 8
    tw = (W - 2 * PAD) // n_thumb
    th = int(tw * wc["h"] / wc["w"])
    thumbs = []
    for i in range(n_thumb):
        p = os.path.join(ROOT, "work", stem, f"_t{i}.png")
        media.sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{dur * (i + 0.5) / n_thumb:.2f}",
                  "-i", work, "-frames:v", "1", "-vf", f"scale={tw}:{th}", p])
        thumbs.append(Image.open(p).convert("RGB"))
        os.remove(p)

    top = 110
    wave_y, wave_h = top + th + 30, 220
    lab_y = wave_y + wave_h + 10
    face_y = lab_y + 4 * 30 + 20
    led_y = face_y + 70
    H = led_y + 60 + 34 * max(1, len(ledger)) + 40
    img = Image.new("RGB", (W, H), (18, 20, 24))
    d = ImageDraw.Draw(img)
    d.text((PAD, 24), f"{stem}  |  {dur:.1f}s  |  working copy {wc['w']}x{wc['h']}  |  "
                      f"{report['whisper']['model']} rtf {report['whisper']['rtf']}  |  {len(words)} words  |  "
                      f"{len(ledger)} spoken numbers", fill=(235, 235, 235), font=big)
    lf = report["loudness"]
    d.text((PAD, 66), f"loudness {lf['integrated_lufs']} LUFS, LRA {lf['loudness_range_lu']} LU, "
                      f"true peak {lf['true_peak_dbtp']} dBTP  |  lead-in {pz['lead_in_s']}s, tail {pz['tail_s']}s, "
                      f"{len(pz['gaps'])} pauses >= {PAUSE_MIN}s  |  faces in {report['faces']['face_rate'] * 100:.0f}% of samples",
           fill=(170, 170, 170), font=font)
    for i, t in enumerate(thumbs):
        img.paste(t, (PAD + i * tw, top))

    # Pauses shaded, then waveform, then number spans highlighted.
    for g in pz["gaps"]:
        d.rectangle([x_of(g["start"]), wave_y, x_of(g["end"]), wave_y + wave_h], fill=(45, 45, 70))
    if pz["lead_in_s"] > 0:
        d.rectangle([x_of(0), wave_y, x_of(pz["lead_in_s"]), wave_y + wave_h], fill=(45, 45, 70))
    for item in ledger:
        d.rectangle([x_of(item["start"]), wave_y, x_of(item["end"]), wave_y + wave_h],
                    fill=(110, 80, 10) if not item["confirm_by_ear"] else (120, 30, 30))
    cols = W - 2 * PAD
    if len(audio):
        chunks = np.array_split(np.abs(audio), cols)
        peaks = np.array([c.max() if len(c) else 0 for c in chunks])
        peaks = peaks / max(peaks.max(), 1e-6)
        mid = wave_y + wave_h / 2
        for x, pk in enumerate(peaks):
            d.line([PAD + x, mid - pk * wave_h / 2, PAD + x, mid + pk * wave_h / 2], fill=(150, 160, 175))
    d.rectangle([PAD, wave_y, W - PAD, wave_y + wave_h], outline=(80, 80, 80))
    for s in range(int(dur) + 1):
        d.line([x_of(s), wave_y + wave_h, x_of(s), wave_y + wave_h + 6], fill=(120, 120, 120))

    # Word labels, greedily packed into 4 rows so they never overlap.
    num_idx = {i for item in ledger for i in item["word_idx"]}
    row_end = [0] * 4
    for i, wd in enumerate(words):
        x = x_of(wd["start"])
        lw = d.textlength(wd["w"], font=small)
        row = next((r for r in range(4) if row_end[r] < x), None)
        if row is None:
            continue
        color = (255, 190, 60) if i in num_idx else (255, 110, 110) if wd["p"] < LOW_WORD_P else (210, 210, 210)
        d.line([x, wave_y + wave_h, x, lab_y + row * 30], fill=(70, 70, 70))
        d.text((x + 2, lab_y + row * 30), wd["w"], fill=color, font=small)
        row_end[row] = x + lw + 8

    # Face presence strip.
    d.text((PAD, face_y - 26), "face present (2 fps samples)", fill=(170, 170, 170), font=small)
    step = 0.5
    hit_t = {round(f["t"], 2) for f in faces}
    t = 0.0
    while t < dur:
        d.rectangle([x_of(t), face_y, x_of(min(dur, t + step)), face_y + 24],
                    fill=(40, 170, 90) if round(t, 2) in hit_t else (50, 50, 50))
        t += step

    # Ledger table.
    d.text((PAD, led_y), "Spoken-numbers ledger (rule 4): only these may appear on screen", fill=(255, 190, 60), font=font)
    if not ledger:
        d.text((PAD, led_y + 40), "(none spoken: NO numbers may appear on screen in this video)",
               fill=(255, 110, 110), font=font)
    for k, item in enumerate(ledger):
        flag = "  CONFIRM BY EAR" if item["confirm_by_ear"] else ""
        d.text((PAD, led_y + 40 + 34 * k),
               f"{item['start']:6.2f}s   \"{item['text']}\"  ->  {numbers_guard.fmt(item)}   p={item['p_min']}{flag}",
               fill=(255, 110, 110) if flag else (230, 230, 230), font=font)
    img.save(path)


def main(src):
    stem = os.path.splitext(os.path.basename(src))[0]
    out_dir = os.path.join(ROOT, "work", stem)
    proof = os.path.join(ROOT, "out", "phase2-proof")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(proof, exist_ok=True)
    L = preflight.load_limits()
    info = media.probe(src)
    R = {"source": os.path.relpath(src, ROOT), "source_info": info}
    print(f"source: {info}")

    work, wc = working_copy(src, info, L, stem)
    R["working_copy"] = wc
    print(f"working copy: {wc}")

    audio = media.load_audio_16k(work) if info["has_audio"] else np.zeros(0, np.float32)
    if len(audio):
        words, R["whisper"] = media.transcribe(audio, L["whisper_model"], info["duration"])
    else:
        words, R["whisper"] = [], {"model": L["whisper_model"], "rtf": 0, "words": 0}
    print(f"whisper: {R['whisper']}")
    ledger = numbers_guard.spoken_ledger(words)
    pz = pauses(words, info["duration"])
    R["loudness"] = loudness(work) if info["has_audio"] else {}
    face_stats, _, faces = media.detect_faces(work, wc["w"], wc["h"])
    R["faces"] = face_stats
    faces_out = [{"t": round(f["t"], 2), "cx_pct": round((f["x"] + f["w"] / 2) / wc["w"] * 100, 1),
                  "cy_pct": round((f["y"] + f["h"] / 2) / wc["h"] * 100, 1),
                  "h_pct": round(f["h"] / wc["h"] * 100, 1), "score": f["score"]} for f in faces]

    def dump(name, obj):
        with open(os.path.join(out_dir, name), "w") as f:
            json.dump(obj, f, indent=1)
    dump("words.json", words)
    dump("numbers.json", ledger)
    dump("pauses.json", pz)
    dump("loudness.json", R["loudness"])
    dump("faces.json", faces_out)
    with open(os.path.join(out_dir, "transcript.txt"), "w") as f:
        for s in sentences(words):
            f.write(f"[{s['start']:6.2f} - {s['end']:6.2f}] {s['text']}\n")

    flags = []
    if not words:
        flags.append("NO SPEECH: no captions possible, and no numbers may appear on screen")
    low = [f"{w['w']} @ {w['start']}s (p={w['p']})" for w in words if w["p"] < LOW_WORD_P]
    if low:
        flags.append(f"{len(low)} low-confidence words, check by ear: " + "; ".join(low))
    for item in ledger:
        if item["confirm_by_ear"]:
            flags.append(f"number \"{item['text']}\" @ {item['start']}s has p={item['p_min']}: confirm by ear before it goes on screen")
    peak = R["loudness"].get("true_peak_dbtp")
    if peak is not None and peak > CLIP_PEAK_DBTP:
        flags.append(f"true peak {peak} dBTP: clipping risk in source audio")
    if face_stats["frames_with_face"] == 0:
        flags.append("no face detected in any sample (fine for b-roll; matters for talking-head reframing)")
    if max(info["w"], info["h"]) < L["working_max_h"]:
        flags.append(f"source is {info['w']}x{info['h']}, below 1440p: working copy kept native (no upscale)")
    R.update({"words": len(words), "numbers_spoken": len(ledger), "pauses": len(pz["gaps"]),
              "lead_in_s": pz["lead_in_s"], "tail_s": pz["tail_s"], "flags": flags,
              "outputs": os.path.relpath(out_dir, ROOT)})
    dump("ingest.json", R)

    png = os.path.join(proof, f"{stem}_timeline.png")
    timeline_png(png, stem, work, info, wc, words, ledger, pz, faces, audio, R)
    with open(os.path.join(proof, f"report_{stem}.json"), "w") as f:
        json.dump(R, f, indent=2)

    print(f"\ntranscript:\n{open(os.path.join(out_dir, 'transcript.txt')).read()}")
    print(f"spoken numbers ({len(ledger)}):")
    for item in ledger:
        print(f"  {item['start']:6.2f}s  \"{item['text']}\" -> {numbers_guard.fmt(item)}  p={item['p_min']}")
    print(f"pauses >= {PAUSE_MIN}s: {len(pz['gaps'])}, lead-in {pz['lead_in_s']}s, tail {pz['tail_s']}s")
    print(f"loudness: {R['loudness']}")
    print(f"faces: {face_stats}")
    print("flags:" + "".join(f"\n  - {x}" for x in flags) if flags else "flags: none")
    print(f"outputs: {R['outputs']}/   proof: {os.path.relpath(png, ROOT)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(os.path.abspath(sys.argv[1]))
