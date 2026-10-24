"""Phase 7 proof: the depth look in small steps, on one light and one dark frame.

  python3 engines/phase7_depth.py <step>      step = 1..5, or all

  1 raw person matte          frame | alpha | cut-out on red, edge metrics
  2 refined matte             hair close-up raw vs refined, edge metrics + flicker
  3 title behind head, plain  white title, measured text contrast
  4 title on the slab         same title on the brand slab, measured contrast
  5 slab motion               frames through the rise and hold
Stills + numbers -> out/phase7-proof/
"""
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import depth  # noqa: E402
import media  # noqa: E402
import spike_layers  # noqa: E402
from graphics_qa import font  # noqa: E402

ROOT = media.ROOT
OUT = os.path.join(ROOT, "out", "phase7-proof")
FRAMES = {  # name: (working copy, source seconds, why)
    "light": ("proxy/IMG_8628_work.mp4", 52.0, "street at sunset, bright sky behind the head"),
    "dark": ("proxy/IMG_8479_work.mp4", 316.5, "office desk, dark background"),
}
TITLE = "THE SYSTEM YOU'RE MISSING"
W, H = 1440, 2560


def load(name, n=1):
    path, t, _ = FRAMES[name]
    full = [f for _, f in media.read_frames(os.path.join(ROOT, path), W, H, 30, limit=n, start=t)]
    half = [f for _, f in media.read_frames(os.path.join(ROOT, path), W // 2, H // 2, 30, limit=n, start=t)]
    return full, half


def mattes(half, refine=True):
    seg = spike_layers.segmenter("multiclass")
    raw, ref, prev_r, prev_f = [], [], None, None
    for f in half:
        m = spike_layers.person_mask(seg, "multiclass", f)
        r = m if prev_r is None else spike_layers.EMA * m + (1 - spike_layers.EMA) * prev_r
        prev_r = r
        raw.append(r)
        if refine:
            # refine each frame, then smooth over time. Measured on the light frame: this order
            # adds ~20% edge flicker vs raw; smoothing first then refining added ~38%.
            q = depth.refine(m, f)
            q = q if prev_f is None else spike_layers.EMA * q + (1 - spike_layers.EMA) * prev_f
            prev_f = q
            ref.append(q)
    seg.close()
    return raw, ref


def up(alpha):
    return np.asarray(Image.fromarray((alpha * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC), np.float32) / 255


def head_y(name, block_h):
    path, t, _ = FRAMES[name]
    clip = os.path.basename(path).replace("_work.mp4", "")
    with open(os.path.join(ROOT, "work", clip, "faces.json")) as f:
        fs = [x for x in json.load(f) if abs(x["t"] - t) <= 1.0]
    fh = np.mean([x["h_pct"] for x in fs]) / 100 * H
    head_top = min(x["cy_pct"] / 100 * H for x in fs) - 0.8 * fh
    return head_top + 0.35 * fh - block_h / 2


def label(img, text, sub=""):
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, img.width, 120], fill=(18, 20, 24))
    d.text((24, 16), text, font=font(44, True), fill=(235, 235, 235))
    d.text((24, 70), sub, font=font(30), fill=(170, 175, 185))
    return img


def grid(rows, title, path, scale=0.25):
    cells = [[c.convert("RGB").resize((round(c.width * scale), round(c.height * scale))) for c in r] for r in rows]
    cw = max(c.width for r in cells for c in r)
    ch = max(c.height for r in cells for c in r)
    out = Image.new("RGB", (cw * max(len(r) for r in cells) + 20 * (max(len(r) for r in cells) + 1),
                            ch * len(cells) + 20 * (len(cells) + 1) + 90), (18, 20, 24))
    ImageDraw.Draw(out).text((20, 22), title, font=font(40, True), fill=(235, 235, 235))
    for i, r in enumerate(cells):
        for j, c in enumerate(r):
            out.paste(c, (20 + j * (cw + 20), 110 + i * (ch + 20)))
    out.save(path)
    return path


def step1():
    rows, res = [], {}
    for name in FRAMES:
        (full,), (half,) = load(name)
        raw, _ = mattes([half], refine=False)
        a = up(raw[0])
        m = depth.edge_metrics(a)
        res[name] = m
        red = Image.new("RGBA", (W, H), (220, 30, 60, 255))
        cut = Image.fromarray(full).convert("RGBA")
        cut.putalpha(Image.fromarray((a * 255).astype(np.uint8)))
        rows.append([label(Image.fromarray(full), f"{name}: frame", FRAMES[name][2]),
                     label(Image.fromarray((a * 255).astype(np.uint8)).convert("RGB"), "raw alpha (multiclass)"),
                     label(Image.alpha_composite(red, cut), "cut-out on red",
                           f"soft edge {m['soft_edge_px']} px, halo {m['halo_pct']}%")])
    return grid(rows, "Step 1: raw person matte, light (top) and dark (bottom)", os.path.join(OUT, "step1_raw_matte.png")), res


def step2():
    rows, res = [], {}
    for name in FRAMES:
        full, half = load(name, 30)
        raw, ref = mattes(half)
        a_raw, a_ref = up(raw[-1]), up(ref[-1])
        # flicker on the same pixels for both: the refined matte's edge band
        res[name] = {"raw": dict(depth.edge_metrics(a_raw), flicker_pct=depth.flicker(raw, ref)),
                     "refined": dict(depth.edge_metrics(a_ref), flicker_pct=depth.flicker(ref, ref))}
        # close-up on the hair: box around the head from the alpha itself
        ys, xs = np.where(a_ref > 0.5)
        top = int(ys.min())
        cx = int(np.median(xs[ys < top + 300]))
        box = (max(0, cx - 360), max(0, top - 120), min(W, cx + 360), min(H, top + 600))
        red = Image.new("RGBA", (W, H), (220, 30, 60, 255))
        cells = []
        for tag, a in (("raw", a_raw), ("refined", a_ref)):
            cut = Image.fromarray(full[-1]).convert("RGBA")
            cut.putalpha(Image.fromarray((a * 255).astype(np.uint8)))
            m = res[name][tag]
            cells.append(label(Image.alpha_composite(red, cut).crop(box).resize((720, 1440)), f"{name}: {tag}",
                               f"edge {m['soft_edge_px']}px  halo {m['halo_pct']}%  flicker {m['flicker_pct']}%"))
        rows.append(cells)
    return grid(rows, "Step 2: matte refinement (guided filter + halo curve), hair close-up",
                os.path.join(OUT, "step2_refined_matte.png"), scale=0.5), res


def title_step(slab, fname, title):
    rows, res = [], {}
    for name in FRAMES:
        (full,), (half,) = load(name)
        _, ref = mattes([half])
        a = up(ref[0])
        L = depth.slab_layout(TITLE, W, "L", slab)
        yc, occ = depth.place_title(TITLE, W, H, a, head_y(name, L["h"]) + L["h"] / 2, "L", slab)
        layer, (x0, y0, bw, bh), L = depth.slab_frame(TITLE, W, H, yc, 1.0, "L", slab)
        comp = depth.composite(full, layer, a)
        txt, _, _ = depth.slab_frame(TITLE, W, H, yc, 1.0, "L", False)
        glyph = np.asarray(txt)[..., 3] > 128
        if slab:  # what sits right behind the letters: the slab (drawn without text) over the frame
            slab_only, _, _ = depth.slab_frame(TITLE, W, H, yc, 1.0, "L", True, draw_text=False)
            behind = np.asarray(Image.alpha_composite(Image.fromarray(full).convert("RGBA"), slab_only).convert("RGB"))
        else:
            behind = full
        c_text = depth.contrast((255, 255, 255), behind[glyph & (a < 0.5)])
        ok = c_text >= depth.D7["min_text_contrast"] and depth.D7["occlusion_min"] <= occ <= depth.D7["occlusion_max"]
        res[name] = {"text_contrast_worst": c_text, "letters_hidden": occ, "pass": ok}
        rows.append([label(comp, f"{name}: {c_text}:1, {occ:.0%} hidden {'PASS' if ok else 'FAIL'}",
                           f"white text on {'slab' if slab else 'background'}; worst-case contrast "
                           f">= {depth.D7['min_text_contrast']}:1, letters hidden {depth.D7['occlusion_min']:.0%}-"
                           f"{depth.D7['occlusion_max']:.0%}")])
    return grid([[r[0] for r in rows]], title, os.path.join(OUT, fname), scale=0.3), res


def step5():
    times = [0.0, 0.08, 0.16, 0.24, 0.35, 1.5]
    rows, res = [], {}
    for name in FRAMES:
        full, half = load(name, int(max(times) * 30) + 1)
        _, ref = mattes(half)
        L = depth.slab_layout(TITLE, W, "L", True)
        ups = [up(a) for a in ref[::3]]
        yc, occ = depth.place_title_over_time(TITLE, W, H, ups, head_y(name, L["h"]) + L["h"] / 2, "L", True)
        res[name] = {"letters_hidden_over_time": occ}
        cells = []
        for t in times:
            k = min(len(full) - 1, round(t * 30))
            layer, _, _ = depth.slab_frame(TITLE, W, H, yc, t, "L", True)
            cells.append(label(depth.composite(full[k], layer, up(ref[k])), f"{name} t={t:.2f}s"))
        rows.append(cells)
    res["curve"] = depth.motion_curve(times)  # (t, slab offset as share of its height, text opacity)
    return grid(rows, "Step 5: slab motion: rises from behind the shoulders, text fades in, slow drift",
                os.path.join(OUT, "step5_slab_motion.png"), scale=0.2), res


def main(which):
    os.makedirs(OUT, exist_ok=True)
    report_p = os.path.join(OUT, "report.json")
    report = json.load(open(report_p)) if os.path.exists(report_p) else {}
    steps = {"1": step1, "2": step2,
             "3": lambda: title_step(False, "step3_title_plain.png", "Step 3: title behind the head, plain white text"),
             "4": lambda: title_step(True, "step4_title_slab.png", "Step 4: title on the brand slab, behind the head"),
             "5": step5}
    for k in (steps if which == "all" else [which]):
        path, res = steps[k]()
        report[f"step{k}"] = res
        print(f"step {k}: {os.path.relpath(path, ROOT)}  {json.dumps(res)}")
    with open(report_p, "w") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
