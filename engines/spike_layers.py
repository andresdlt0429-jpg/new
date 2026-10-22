"""Phase 3 feasibility spike: the 5-layer stack on one clip, measured.

  python3 engines/spike_layers.py raw/clip.mp4

Layer order, bottom to top:
  L1 background         the footage
  L2 behind-head title  big text, drawn BEFORE the speaker so the head covers it
  L3 speaker matte      the speaker cut out of the same footage (MediaPipe)
  L4 front graphics     cards, lower thirds, logos, CTA
  L5 captions           libass, always on top

Not production code: it proves the layer order works in one ffmpeg pass and
measures what the speaker matte costs. Fonts/colors here are labels, not brand.
"""
import json
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import media  # noqa: E402
import preflight  # noqa: E402
import sysinfo as si  # noqa: E402

ROOT = media.ROOT
MODELS = {
    "selfie": os.path.join(ROOT, "engines", "models", "selfie_segmenter.tflite"),
    "multiclass": os.path.join(ROOT, "engines", "models", "selfie_multiclass_256x256.tflite"),
}
EMA = 0.6  # temporal smoothing of the matte: new = EMA*this frame + (1-EMA)*previous


def font(size, bold=True):
    path = subprocess.run(["fc-match", "-f", "%{file}", "Inter:bold" if bold else "Inter"],
                          capture_output=True, text=True).stdout
    return ImageFont.truetype(path, size)


def segmenter(name):
    from mediapipe.tasks.python import BaseOptions, vision
    return vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
        base_options=BaseOptions(model_asset_path=MODELS[name]),
        output_confidence_masks=True, output_category_mask=False))


def person_mask(seg, name, rgb):
    import mediapipe as mp
    res = seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
    masks = res.confidence_masks
    if name == "selfie":
        return masks[0].numpy_view().astype(np.float32).squeeze()
    # multiclass: index 0 = background
    return 1.0 - masks[0].numpy_view().astype(np.float32).squeeze()


def time_models(work, w, h, n=45):
    """ms per frame for each model, fed full-size and half-size frames."""
    frames = [f for _, f in media.read_frames(work, w, h, 30, limit=n)]
    half = [f for _, f in media.read_frames(work, w // 2, h // 2, 30, limit=n)]
    out = {}
    for name in MODELS:
        seg = segmenter(name)
        for label, batch in (("full", frames), ("half", half)):
            person_mask(seg, name, batch[0])  # warm-up
            t = time.time()
            for f in batch:
                person_mask(seg, name, f)
            out[f"{name}_{label}"] = round((time.time() - t) / len(batch) * 1000, 1)
        seg.close()
    return out


def matte_pass(work, w, h, name, dst):
    """Half-size end to end: ffmpeg decodes at half size, segment, temporal smoothing,
    lossless gray FFV1 at half size. The composite scales the matte back up."""
    seg = segmenter(name)
    hw, hh = w // 2, h // 2
    enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                            "-pix_fmt", "gray", "-s", f"{hw}x{hh}", "-r", "30", "-i", "-",
                            "-c:v", "ffv1", dst], stdin=subprocess.PIPE)
    prev, n, t = None, 0, time.time()
    sample_t = None
    for ts, small in media.read_frames(work, hw, hh, 30):
        m = person_mask(seg, name, small)
        m = m if prev is None else EMA * m + (1 - EMA) * prev
        prev = m
        enc.stdin.write((m * 255).astype(np.uint8).tobytes())
        if sample_t is None and ts >= 3.0:
            sample_t = (n, (m * 255).astype(np.uint8))
        n += 1
    enc.stdin.close()
    enc.wait()
    seg.close()
    dt = time.time() - t
    # Proof sample: full-size frame plus the matte scaled up the same way ffmpeg will.
    idx, m_half = sample_t
    rgb = next(f for k, (_, f) in enumerate(media.read_frames(work, w, h, 30, limit=idx + 1)) if k == idx)
    m_full = np.asarray(Image.fromarray(m_half).resize((w, h), Image.BICUBIC))
    return {"frames": n, "seconds": round(dt, 1), "ms_per_frame": round(dt / n * 1000, 1),
            "mb": round(os.path.getsize(dst) / 1e6, 1), "stored_at": f"{hw}x{hh}"}, (rgb, m_full)


def title_layer(w, h, path):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(int(w * 0.17))
    for k, line in enumerate(["BEHIND", "HEAD"]):
        tw = d.textlength(line, font=f)
        d.text(((w - tw) / 2, h * 0.075 + k * w * 0.18), line, font=f, fill=(255, 196, 0, 255))
    img.save(path)
    return img


def front_layer(w, h, path):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    box = [w * 0.06, h * 0.56, w * 0.62, h * 0.61]
    d.rounded_rectangle(box, radius=int(h * 0.012), fill=(20, 110, 255, 235))
    d.text((box[0] + w * 0.035, box[1] + h * 0.010), "FRONT GRAPHIC", font=font(int(h * 0.026)),
           fill=(255, 255, 255, 255))
    img.save(path)
    return img


def captions_ass(w, h, dur, path):
    size = round(h * 0.036)
    with open(path, "w") as f:
        f.write("\n".join([
            "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Cap,Inter,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,"
            f"{round(size * 0.08)},{round(size * 0.04)},2,60,60,{round(h * 0.28)},1",
            "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            f"Dialogue: 0,0:00:00.00,0:00:{dur:05.2f},Cap,,0,0,0,,Captions always on top", ""]))


def checker(w, h, cell=48):
    y, x = np.mgrid[0:h, 0:w]
    c = (((x // cell) + (y // cell)) % 2 * 40 + 70).astype(np.uint8)
    return Image.fromarray(np.stack([c, c, c], -1))


def main(src):
    stem = os.path.splitext(os.path.basename(src))[0]
    wd = os.path.join(ROOT, "work", stem)
    proof = os.path.join(ROOT, "out", "phase3-proof")
    os.makedirs(wd, exist_ok=True)
    os.makedirs(proof, exist_ok=True)
    info = media.probe(src)
    L = preflight.load_limits()
    work = src  # the test clip is already a 1440x2560 working copy
    media.verify_working_copy(work, info, L)
    w, h, dur = info["w"], info["h"], info["duration"]
    R = {"source": os.path.relpath(src, ROOT), "w": w, "h": h, "duration": dur}

    R["matte_ms_per_frame"] = time_models(work, w, h)
    print(f"matte speed (ms/frame): {R['matte_ms_per_frame']}")

    mattes, samples = {}, {}
    for name in MODELS:
        dst = os.path.join(wd, f"matte_{name}.mkv")
        mattes[name], samples[name] = matte_pass(work, w, h, name, dst)
        print(f"matte pass {name}: {mattes[name]}")
    R["matte_pass"] = mattes

    title = title_layer(w, h, os.path.join(wd, "L2_title.png"))
    front = front_layer(w, h, os.path.join(wd, "L4_front.png"))
    captions_ass(w, h, dur, os.path.join(wd, "L5_captions.ass"))

    out = os.path.join(ROOT, "out", f"{stem}_layers.mp4")
    graph = (f"[0:v]split[bg][fgsrc];[1:v]format=gray,scale={w}:{h}:flags=bicubic[m];[fgsrc][m]alphamerge[fg];"
             "[bg][2:v]overlay=0:0[l2];[l2][fg]overlay=0:0[l3];[l3][3:v]overlay=0:0[l4];"
             "[l4]ass=L5_captions.ass,format=yuv420p[out]")
    with preflight.render_slot(work) as pf:
        R["preflight"] = pf
        t = time.time()
        peak = si.run_measured(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", work,
                                "-i", "matte_multiclass.mkv", "-loop", "1", "-i", "L2_title.png",
                                "-loop", "1", "-i", "L4_front.png", "-filter_complex", graph,
                                "-map", "[out]", "-map", "0:a?", "-t", f"{dur}", "-c:v", L["encoder"],
                                "-preset", "medium", "-crf", "18", "-threads", str(L["encode_threads"]),
                                "-c:a", "copy", out], cwd=wd)
        dt = time.time() - t
    R["composite_render"] = {"seconds": round(dt, 1), "fps": round(dur * 30 / dt, 1),
                             "est_seconds_per_output_minute": round(60 * dt / dur),
                             "ffmpeg_peak_ram_gb": round(peak / si.GB, 2), "path": os.path.relpath(out, ROOT)}
    print(f"composite render: {R['composite_render']}")

    # Proof 1: the stack building up, one sheet at a time (L5 comes from the real ffmpeg render).
    rgb, m = samples["multiclass"]
    l1 = Image.fromarray(rgb).convert("RGBA")
    cut = Image.fromarray(rgb).convert("RGBA")
    cut.putalpha(Image.fromarray(m))
    l2 = Image.alpha_composite(l1, title)
    l3 = Image.alpha_composite(l2, cut)
    l4 = Image.alpha_composite(l3, front)
    final_png = os.path.join(wd, "_final.png")
    media.sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "3", "-i", out,
              "-frames:v", "1", final_png])
    l5 = Image.open(final_png).convert("RGBA")
    os.remove(final_png)
    sw, sh_ = w // 4, h // 4
    labels = ["L1 background", "+ L2 behind-head title", "+ L3 speaker matte", "+ L4 front graphics",
              "+ L5 captions (real render)"]
    sheet = Image.new("RGB", (sw * 5 + 60, sh_ + 90), (18, 20, 24))
    d = ImageDraw.Draw(sheet)
    for i, im in enumerate([l1, l2, l3, l4, l5]):
        sheet.paste(im.convert("RGB").resize((sw, sh_)), (10 + i * (sw + 10), 70))
        d.text((10 + i * (sw + 10), 20), labels[i], font=font(26), fill=(235, 235, 235))
    sheet.save(os.path.join(proof, "1_layer_buildup.png"))

    # Proof 2: each layer alone on a checkerboard (transparent = checks).
    alone = Image.new("RGB", (sw * 5 + 60, sh_ + 90), (18, 20, 24))
    d = ImageDraw.Draw(alone)
    ck = checker(w, h).convert("RGBA")
    caps_only = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    diff = np.abs(np.asarray(l5.convert("RGB"), np.int16) - np.asarray(l4.convert("RGB"), np.int16)).sum(-1) > 60
    caps_arr = np.asarray(l5).copy()
    caps_arr[..., 3] = np.where(diff, 255, 0)
    caps_only = Image.fromarray(caps_arr)
    for i, (lab, im) in enumerate([("L1 footage", l1), ("L2 title", title), ("L3 cut-out", cut),
                                   ("L4 graphic", front), ("L5 captions", caps_only)]):
        alone.paste(Image.alpha_composite(ck, im).convert("RGB").resize((sw, sh_)), (10 + i * (sw + 10), 70))
        d.text((10 + i * (sw + 10), 20), lab, font=font(26), fill=(235, 235, 235))
    alone.save(os.path.join(proof, "2_layers_alone.png"))

    # Proof 3: matte edge quality, both models, zoomed on the hair.
    y0, y1, x0, x1 = int(h * 0.08), int(h * 0.30), int(w * 0.25), int(w * 0.75)
    tiles = []
    for name in MODELS:
        rgb_n, m_n = samples[name]
        c = Image.fromarray(rgb_n).convert("RGBA")
        c.putalpha(Image.fromarray(m_n))
        red = Image.new("RGBA", (w, h), (220, 30, 60, 255))
        tiles.append((name, Image.alpha_composite(red, c).crop((x0, y0, x1, y1))))
    tw, th = tiles[0][1].size
    edge = Image.new("RGB", (tw * 2 + 30, th + 70), (18, 20, 24))
    d = ImageDraw.Draw(edge)
    for i, (name, t) in enumerate(tiles):
        edge.paste(t.convert("RGB"), (10 + i * (tw + 10), 60))
        d.text((10 + i * (tw + 10), 15), f"{name}: {R['matte_ms_per_frame'][name + '_half']} ms/frame (half-size)",
               font=font(26), fill=(235, 235, 235))
    edge.save(os.path.join(proof, "3_matte_edges.png"))

    with open(os.path.join(proof, f"report_{stem}.json"), "w") as f:
        json.dump(R, f, indent=2)
    print(f"proof: {os.path.relpath(proof, ROOT)}/")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(os.path.abspath(sys.argv[1]))
