"""Phase 4 render: plan.json -> 1440p master + 1080x1920 delivery in one ffmpeg pass, then QA.

  python3 engines/render.py plans/<ad>/plan.json [--draft]

Refuses an unlocked plan unless --draft (then a DRAFT tag is burned top-left).
Layers: L1 cut footage, L2 behind-head titles, L3 speaker matte (only while a title
is up), L4 graphics, L5 captions. Audio: 12 ms fades at every cut, high-pass, light
denoise, two-pass loudnorm to spec/sop-defaults.json audio_sop_5.
Outputs: out/<ad>/<ad>_<variant>[_DRAFT]_master_1440.mp4, ..._1080x1920.mp4,
         out/<ad>/proof/ stills, out/<ad>/qa.json
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
import align  # noqa: E402
import components  # noqa: E402
import depth  # noqa: E402
import graphics_qa  # noqa: E402
import ingest  # noqa: E402
import media  # noqa: E402
import numbers_guard  # noqa: E402
import preflight  # noqa: E402
import spike_layers  # noqa: E402
import sysinfo as si  # noqa: E402

ROOT = media.ROOT
with open(os.path.join(ROOT, "spec", "sop-defaults.json")) as f:
    SOP = json.load(f)
with open(os.path.join(ROOT, "brand.json")) as f:
    BRAND = json.load(f)
AUD, EXP = SOP["audio_sop_5"], SOP["export_sop_5"]
STOP = set("a an the and or of to in on for your you is are it this that at all just with so if i".split())


def fontfile(family, bold=True):
    return subprocess.run(["fc-match", "-f", "%{file}", f"{family}:{'bold' if bold else 'regular'}"],
                          capture_output=True, text=True).stdout


def hex_rgb(h, a=255):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


# ---------- timeline ----------

def build_timeline(plan, variant, srcs):
    def wanted(p):
        g = p["group"]
        return g in ("shared", "cta") or g == f"hook:{variant.get('hook')}" or g == f"body:{variant.get('body')}"
    segs, t = [], 0.0
    for p in plan["parts"]:
        if not wanted(p):
            continue
        d = p["out"] - p["in"]
        segs.append({"part": p["id"], "src": p["src"], "proxy": srcs[p["src"]]["proxy"], "in": p["in"],
                     "out": p["out"], "t0": round(t, 3), "t1": round(t + d, 3), "beat": p.get("beat")})
        t += d
    return segs


def resolve(anchor, segs, srcs):
    off = anchor.get("offset", 0.0)
    if "word" in anchor:
        key, idx = anchor["word"].split(":")
        w = srcs[key]["words"][int(idx)]
        tw = w["end"] if anchor.get("edge") == "end" else w["start"]
        for s in segs:
            if s["src"] == key and s["in"] - 0.001 <= tw <= s["out"] + 0.001:
                return round(s["t0"] + (tw - s["in"]) + off, 3)
        raise ValueError(f"anchor word {anchor['word']} ({w['w']!r}) is not in any kept part")
    s = next((s for s in segs if s["part"] == anchor["part"]), None)
    if s is None:
        raise ValueError(f"anchor part {anchor['part']} not in this variant")
    return round((s["t1"] if anchor.get("edge") == "end" else s["t0"]) + off, 3)


# ---------- assets ----------

def wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def graphic_png(item, W, H, path):
    g = BRAND["graphics"]
    font = ImageFont.truetype(fontfile(BRAND["fonts"]["headline"] or BRAND["fonts"]["fallback_headline"]),
                              round(H * g["graphic_size_pct_of_height"] / 100))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    b = item["box"]
    bx, bw = W * b["x_pct"] / 100, W * b["w_pct"] / 100
    pad = font.size * 0.55
    lines = wrap(d, item["text"], font, bw - 2 * pad)
    lh = font.size * 1.18
    bh = lh * len(lines) + 2 * pad * 0.8
    by = H * b["y_pct"] / 100 - bh / 2
    tw = max(d.textlength(ln, font=font) for ln in lines)
    x0 = bx + (bw - (tw + 2 * pad)) / 2
    d.rounded_rectangle([x0, by, x0 + tw + 2 * pad, by + bh], radius=font.size * 0.35,
                        fill=hex_rgb(g["box_color"], round(255 * g["box_alpha"])))
    for i, ln in enumerate(lines):
        lw = d.textlength(ln, font=font)
        d.text((bx + (bw - lw) / 2, by + pad * 0.8 + i * lh), ln, font=font, fill=hex_rgb(g["text_color"]))
    x0, y0, x1, y1 = img.getbbox()
    x0, y0 = x0 // 2 * 2, y0 // 2 * 2
    img.crop((x0, y0, x1, y1)).save(path)  # only the box: far less to decode and blend every frame
    return x0, y0


def title_png(item, W, H, path):
    g = BRAND["graphics"]
    font = ImageFont.truetype(fontfile(BRAND["fonts"]["headline"] or BRAND["fonts"]["fallback_headline"]),
                              components.tier_px(item.get("tier", "L"), W))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lines = wrap(d, item["text"], font, W * 0.92)
    lh = font.size * 1.02
    y0 = H * item.get("y_pct", 25) / 100 - lh * len(lines) / 2
    item["font_px"] = font.size
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    for i, ln in enumerate(lines):
        lw = d.textlength(ln, font=font)
        x, y = (W - lw) / 2, y0 + i * lh
        ds.text((x + font.size * 0.04, y + font.size * 0.05), ln, font=font,
                fill=(0, 0, 0, round(255 * g["title_shadow_alpha"])))
        d.text((x, y), ln, font=font, fill=hex_rgb(g["title_color"]))
    Image.alpha_composite(shadow, img).save(path)


def title_block(text, W, tier="L"):
    """(font_px, lines, block_height_px) for a behind-head title at master width W."""
    font = ImageFont.truetype(fontfile(BRAND["fonts"]["headline"] or BRAND["fonts"]["fallback_headline"]),
                              components.tier_px(tier, W))
    d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    lines = wrap(d, text, font, W * 0.92)
    return font.size, lines, font.size * 1.02 * len(lines)


def end_card(W, H, cards):
    """Logo card from brand.json logo: the logo on its own background, centred."""
    lg = BRAND.get("logo")
    if not lg or not os.path.exists(os.path.join(ROOT, lg["path"])):
        return None
    logo = Image.open(os.path.join(ROOT, lg["path"])).convert("RGB")
    card = Image.new("RGB", (W, H), hex_rgb(lg.get("background", "#FFFFFF"))[:3])
    w = round(W * 0.82)
    logo = logo.resize((w, round(logo.height * w / logo.width)), Image.LANCZOS)
    card.paste(logo, ((W - logo.width) // 2, round(H * 0.45 - logo.height / 2)))
    p = os.path.join(cards, "end_card.png")
    card.save(p)
    return {"png": p, "s": float(lg.get("end_card_s", 2.5))}


def slab_title_clip(t, ts, te, samples, W, H, path):
    """Phase 7 depth title: slab placed so the person hides 4-15% of its letters over the whole
    range (measured on the matte samples, half size), then rendered with its rise + drift."""
    hw, hh = W // 2, H // 2
    y_guess = H * t.get("y_pct", 25) / 100 / 2
    yc_half, occ = depth.place_title_over_time(t["text"], hw, hh, samples, y_guess, t["tier"], True)
    yc = yc_half * 2
    dur = te - ts
    frames, boxes = [], []
    for n in range(max(1, round(dur * EXP["fps"]))):
        img, box, L = depth.slab_frame(t["text"], W, H, yc, n / EXP["fps"], t["tier"], True)
        frames.append(img)
        boxes.append(box)
    x0 = int(min(b[0] for b in boxes)) // 2 * 2
    y0 = max(0, int(min(b[1] for b in boxes))) // 2 * 2
    x1 = min(W, int(max(b[0] + b[2] for b in boxes)) + 2)
    y1 = min(H, int(max(b[1] + b[3] for b in boxes)) + 2)
    components.write_clip([f.crop((x0, y0, x1, y1)) for f in frames], path)
    halo = depth.edge_metrics(samples[len(samples) // 2])["halo_pct"]
    flick = depth.flicker(samples)
    c = depth.contrast(components.WHITE, np.array([[components.PRIMARY[:3]]], np.uint8))
    return {"path": path, "x": x0, "y": y0, "font_px": L["font"].size,
            "depth": {"letters_hidden": occ, "text_contrast": c, "matte_halo_pct": halo, "matte_flicker_pct": flick}}


def matte_for(title, ts, te, segs, W, H, path):
    """Speaker matte (multiclass, half size, smoothed) for the title's time range only."""
    s = next(x for x in segs if x["t0"] - 0.001 <= ts < x["t1"])
    start = s["in"] + (ts - s["t0"])
    hw, hh = W // 2, H // 2
    seg = spike_layers.segmenter("multiclass")
    enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt",
                            "gray", "-s", f"{hw}x{hh}", "-r", "30", "-i", "-", "-c:v", "ffv1", path],
                           stdin=subprocess.PIPE)
    prev, n, t, samples = None, 0, time.time(), []
    for _, small in media.read_frames(os.path.join(ROOT, s["proxy"]), hw, hh, 30, start=start, dur=te - ts):
        m = depth.refine(spike_layers.person_mask(seg, "multiclass", small), small)  # Phase 7: no halo
        m = m if prev is None else spike_layers.EMA * m + (1 - spike_layers.EMA) * prev
        prev = m
        enc.stdin.write((m * 255).astype(np.uint8).tobytes())
        if n % 3 == 0:
            samples.append(m.astype(np.float32))
        n += 1
    enc.stdin.close()
    enc.wait()
    seg.close()
    return {"frames": n, "seconds": round(time.time() - t, 1)}, samples


def face_boxes(segs, srcs, ts, te, W, H):
    """Face boxes (px, master frame) seen in the source footage behind output time [ts, te]."""
    out = []
    for s in segs:
        a, b = max(ts, s["t0"]), min(te, s["t1"])
        if b <= a:
            continue
        p = os.path.join(ROOT, srcs[s["src"]]["dir"], "faces.json")
        if not os.path.exists(p):
            continue
        with open(p) as f:
            for fc in json.load(f):
                t_out = s["t0"] + fc["t"] - s["in"]
                if a - 0.25 <= t_out <= b + 0.25:
                    fh = fc["h_pct"] / 100 * H
                    cx, cy = fc["cx_pct"] / 100 * W, fc["cy_pct"] / 100 * H
                    out.append((cx - 0.45 * fh, cy - 0.8 * fh, cx + 0.45 * fh, cy + 0.6 * fh))  # incl. hair
    return out


def clear_of_face(x, y, w, h, ts, te, segs, srcs, W, H):
    """Keep a graphic off the speaker's face: if it overlaps, try just above the face and just
    below it (above the captions); take the spot nearest the preferred one. -> (y, moved_px, overlap_px)"""
    g4 = SOP["graphics_sop_4"]
    cap = BRAND["captions"]["own_ads"]
    cap_top = H * (100 - cap["margin_bottom_pct"] - 2.2 * cap["size_pct_of_height"]) / 100
    faces = [f for f in face_boxes(segs, srcs, ts, te, W, H) if f[0] < x + w and f[2] > x]
    if not faces:
        return y, 0, 0
    m = g4["face_margin_pct"] / 100 * H
    top, bottom = min(f[1] for f in faces) - m, max(f[3] for f in faces) + m

    def overlap(yy):
        return max(0, min(yy + h, bottom) - max(yy, top))
    if overlap(y) == 0:
        return y, 0, 0
    cands = [c for c in (top - h, bottom) if g4["min_top_pct"] / 100 * H <= c and c + h <= cap_top]
    if not cands:  # no clean spot: least overlap within the frame limits
        cands = [max(g4["min_top_pct"] / 100 * H, top - h), min(bottom, cap_top - h)]
    best = min(cands, key=lambda c: (overlap(c), abs(c - y)))
    return round(best), round(y - best), round(overlap(best))


def enforce_min_on_screen(graphics, total):
    """Every graphic stays up at least min_on_screen_s: extend into the gap after it; if the
    video ends first, start it earlier and shorten the graphic before it."""
    mn = SOP["graphics_sop_5"]["min_on_screen_s"]
    graphics.sort(key=lambda g: g["ts"])
    for i, g in enumerate(graphics):
        nxt = graphics[i + 1]["ts"] if i + 1 < len(graphics) else total
        if g["te"] - g["ts"] < mn:
            g["te"] = round(min(nxt, g["ts"] + mn, total), 3)
        if g["te"] - g["ts"] < mn and i > 0:
            g["ts"] = round(max(graphics[i - 1]["ts"] + mn, g["te"] - mn), 3)
            graphics[i - 1]["te"] = min(graphics[i - 1]["te"], g["ts"])


# ---------- captions (L5) ----------

def caption_lines(segs, srcs, emphasis, max_words):
    words = []
    for s in segs:
        for i, w in enumerate(srcs[s["src"]]["words"]):
            if s["in"] - 0.001 <= w["start"] < s["out"] and w["end"] > s["in"]:
                words.append({"w": w["w"], "start": round(s["t0"] + w["start"] - s["in"], 3),
                              "end": round(min(s["t1"], s["t0"] + w["end"] - s["in"]), 3),
                              "ref": f"{s['src']}:{i}", "new_part": False})
        if words:
            words[-1]["part_end"] = True
    lines, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or re.search(r"[.?!,]$", w["w"]) or w.get("part_end"):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    out, sentence_start = [], True
    for ln in lines:
        cand = [w for w in ln if w["ref"] in emphasis and align.norm(w["w"]) not in STOP]
        emph = sorted(cand, key=lambda w: (not re.search(r"\d", w["w"]), -len(w["w"])))[:2]
        if not emph:  # rule: caps on 1-2 key words per line
            cands = [w for w in ln if align.norm(w["w"]) not in STOP and len(align.norm(w["w"])) >= 4]
            emph = sorted(cands, key=lambda w: -len(w["w"]))[:1]
        toks = []
        for w in ln:
            t = w["w"].strip()
            core = re.sub(r"[^\w'$%-]", "", t)
            if w in emph:
                t = t.upper()
            elif sentence_start:
                t = t[:1].upper() + t[1:].lower() if not re.search(r"\d", t) else t
            elif core in ("I", "I'm", "I've", "I'll"):
                pass
            else:
                t = t.lower()
            sentence_start = bool(re.search(r"[.?!]$", w["w"]))
            toks.append(t)
        text = " ".join(toks).rstrip(",.")
        out.append({"start": ln[0]["start"], "end": ln[-1]["end"], "text": text,
                    "emphasis": [w["w"] for w in emph]})
    for i in range(len(out) - 1):
        if out[i + 1]["start"] - out[i]["end"] < 0.35:
            out[i]["end"] = out[i + 1]["start"]
        else:
            out[i]["end"] = round(out[i]["end"] + 0.15, 3)
    if out:
        out[-1]["end"] = round(out[-1]["end"] + 0.25, 3)
    return out


def write_ass(lines, W, H, path, style):
    font = BRAND["fonts"]["caption"] or BRAND["fonts"]["fallback_caption"]
    size = round(H * style["size_pct_of_height"] / 100)
    mv = round(H * style["margin_bottom_pct"] / 100)
    hdr = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2", "",
           "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
           "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
           "MarginL, MarginR, MarginV, Encoding",
           f"Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,1,0,0,0,100,100,0,0,1,"
           f"{round(size * 0.09)},{round(size * 0.05)},2,70,70,{mv},1", "", "[Events]",
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ts = ingest_ass_time
    with open(path, "w") as f:
        f.write("\n".join(hdr + [f"Dialogue: 0,{ts(c['start'])},{ts(c['end'])},Cap,,0,0,0,,{c['text']}"
                                 for c in lines]) + "\n")


def ingest_ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


# ---------- ffmpeg ----------

def segment_gains(segs):
    """Bring every piece to segment_match_lufs before joining: Andy's office takes sit
    near -43 LUFS, the second session near -17; one global gain would make them jump."""
    for s in segs:
        err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{s['in']:.3f}", "-t",
                              f"{s['out'] - s['in']:.3f}", "-i", os.path.join(ROOT, s["proxy"]), "-vn",
                              "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
        m = re.search(r"I:\s+(-?[\d.]+) LUFS", err[err.rfind("Summary:"):])
        lufs = float(m.group(1)) if m else None
        lim = AUD["segment_gain_limit_db"]
        s["lufs_in"] = lufs
        s["gain_db"] = round(max(-lim, min(lim, AUD["segment_match_lufs"] - lufs)), 1) if lufs is not None else 0.0


def audio_chain(segs, measured=None):
    F = AUD["segment_fade_ms"] / 1000
    parts = []
    for i, s in enumerate(segs):
        d = s["out"] - s["in"]
        parts.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS,volume={s.get('gain_db', 0)}dB,afade=t=in:st=0:d={F},"
                     f"afade=t=out:st={max(0, d - F):.3f}:d={F}[a{i}]")
    cat = "".join(f"[a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[acat]"
    # aim below the true-peak limit: AAC encoding overshoots by a few tenths of a dB
    ln = (f"loudnorm=I={AUD['loudness_lufs']}:TP={AUD['true_peak_dbtp'] - AUD['codec_headroom_db']}"
          f":LRA={AUD['loudness_range_lu']}")
    if measured:
        ln += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
               f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
               f":offset={measured['target_offset']}:linear=true")
    ln += ":print_format=json"
    lim = 10 ** (AUD["pre_limit_dbfs"] / 20)
    clean = (f"[acat]highpass=f={AUD['highpass_hz']},afftdn=nr={AUD['denoise_nr_db']},"
             f"alimiter=limit={lim:.4f}:attack=5:release=60:level=0,{ln},aresample=48000")
    return parts, cat, clean


def seg_inputs(segs, video=True):
    args = []
    for s in segs:
        args += ["-ss", f"{s['in']:.3f}", "-t", f"{s['out'] - s['in']:.3f}"]
        if not video:
            args += ["-vn"]
        args += ["-i", os.path.join(ROOT, s["proxy"])]
    return args


def measure_loudness(segs):
    parts, cat, clean = audio_chain(segs)
    graph = ";".join(parts + [cat, clean + "[aout]"])
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats"] + seg_inputs(segs, video=False) +
                         ["-filter_complex", graph, "-map", "[aout]", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return json.loads(err[err.rfind("{"):err.rfind("}") + 1])


def main(plan_path, draft):
    with open(plan_path) as f:
        plan = json.load(f)
    if plan["status"] != "locked" and not draft:
        bad = [k for k, v in plan.get("checks", {}).items() if v["status"] in ("fail", "pending")]
        sys.exit(f"REFUSED: plan is not locked (open checks: {', '.join(bad)}). Use --draft for a review render.")
    ad = plan["id"]
    L = preflight.load_limits()
    srcs = {k: align.load_source(os.path.join(ROOT, v["ingest"])) for k, v in plan["sources"].items()}
    W, H = plan["output"]["master"]["w"], plan["output"]["master"]["h"]
    DW, DH = plan["output"]["delivery"]["w"], plan["output"]["delivery"]["h"]
    out_dir = os.path.join(ROOT, "out", ad)
    proof = os.path.join(out_dir, "proof")
    cards = os.path.join(ROOT, "cards", ad)
    wd = os.path.join(ROOT, "work", ad)
    for d in (out_dir, proof, cards, wd):
        os.makedirs(d, exist_ok=True)
    for variant in plan["variants"]:
        render_variant(plan, plan_path, variant, draft, srcs, L, W, H, DW, DH, out_dir, proof, cards, wd, ad)


def render_variant(plan, plan_path, variant, draft, srcs, L, W, H, DW, DH, out_dir, proof, cards, wd, ad):
    print(f"\n===== variant {variant['id']} =====")
    proof = os.path.join(proof, variant["id"])
    os.makedirs(proof, exist_ok=True)
    segs = build_timeline(plan, variant, srcs)
    total = segs[-1]["t1"]
    tag = "_DRAFT" if draft else ""
    stem = f"{ad}_{variant['id']}{tag}"
    R = {"plan": os.path.relpath(plan_path, ROOT), "variant": variant["id"], "draft": draft,
         "parts": len(segs), "duration_planned_s": round(total, 3)}
    print(f"timeline: {len(segs)} parts, {total:.2f}s")

    # assets
    titles, graphics = [], []
    hero_id = plan["layers"].get("hero", {}).get(variant["id"])
    def span(item):
        try:
            a = resolve(item["at"], segs, srcs)
        except ValueError:
            return None  # belongs to another variant's lines
        return a, resolve(item["until"], segs, srcs)
    for t in plan["layers"]["titles"]:
        sp = span(t)
        if not sp:
            continue
        ts, te = sp
        t = dict(t, tier="XL" if t["id"] == hero_id else t.get("tier", "L"))
        mp = os.path.join(wd, f"matte_{t['id']}.mkv")
        stats, samples = matte_for(t, ts, te, segs, W, H, mp)
        built = slab_title_clip(t, ts, te, samples, W, H, os.path.join(cards, f"{variant['id']}_{t['id']}.mkv"))
        titles.append({"id": t["id"], "text": t["text"], "ts": ts, "te": te, "png": built["path"], "matte": mp,
                       "matte_stats": stats, "tier": t["tier"], "font_px": built["font_px"], "beat": t.get("beat"),
                       "x": built["x"], "y": built["y"], "depth": built["depth"]})
        print(f"title {t['id']} {ts:.2f}-{te:.2f}s  matte {stats}  depth {built['depth']}")
    for g in plan["layers"]["graphics"]:
        sp = span(g)
        if not sp:
            continue
        a, b = sp
        item = dict(g, tier="XL" if g["id"] == hero_id else g.get("tier", "M"))
        if g["kind"] == "ticks":
            fps = EXP["fps"]
            a0 = round(a * fps) / fps  # the clip starts on this frame; each tick on the frame nearest its word
            item["items"] = [{"text": it["text"], "t": round((resolve(it["at"], segs, srcs) - a0) * fps) / fps}
                             for it in g["items"]]
            item["text"] = " / ".join(it["text"] for it in g["items"])
        if g["kind"] in components.BUILDERS:
            graphics.append({"id": g["id"], "text": item["text"], "ts": a, "te": b, "item": item, "beat": g.get("beat")})
        else:
            p = os.path.join(cards, f"{g['id']}.png")
            gx, gy = graphic_png(g, W, H, p)
            graphics.append({"id": g["id"], "text": g["text"], "ts": a, "te": b, "png": p, "x": gx, "y": gy,
                             "kind": "still", "tier": g.get("tier", "M"), "font_px": None, "beat": g.get("beat")})
    enforce_min_on_screen(graphics, total)
    for x in graphics:
        if "item" in x:
            it = x["item"]
            order = ["XL", "L", "M", "S"]
            if it["tier"] == "XL":  # hero: shrink in small steps, never below hero_min_ratio x tier L
                g4 = SOP["graphics_sop_4"]
                floor_px = g4["hero_min_ratio"] * components.tier_px("L", W)
                px, sizes = components.tier_px("XL", W), []
                while px >= floor_px:
                    sizes.append(px)
                    px = int(px * 0.95)
                tiers = sizes or [components.tier_px("XL", W)]
            else:
                tiers = order[order.index(it["tier"]):]
            for tier in tiers:  # step down until it clears the face
                if isinstance(tier, int):
                    it["px"] = tier
                else:
                    it["tier"] = tier
                built = components.build(it, W, H, x["te"] - x["ts"], os.path.join(cards, f"{variant['id']}_{x['id']}"))
                bx = round(W * it["box"]["x_pct"] / 100) if it["kind"] == "ticks" else (W - built["w"]) // 2
                by = round(H * it["box"]["y_pct"] / 100 - built["h"] / 2)
                by, moved, over = clear_of_face(bx, by, built["w"], built["h"], x["ts"], x["te"], segs, srcs, W, H)
                if not over:
                    break
                print(f"  {x['id']} at {tier if isinstance(tier, str) else str(tier) + 'px'} still overlaps the face by {over}px")
            if moved:
                print(f"  moved {x['id']} {'up' if moved > 0 else 'down'} {abs(moved)}px to clear the face")
            x["face_overlap_px"] = over
            x.update({"png": built["path"], "kind": built["kind"], "x": bx // 2 * 2, "y": by // 2 * 2,
                      "w": built["w"], "h": built["h"], "tier": it["tier"], "font_px": built["font_px"],
                      "hold_last": built["hold_last"], "component": it["kind"],
                      "row_h": built.get("row_h"), "items": it.get("items")})
        print(f"graphic {x['id']:14s} {x.get('component', 'text'):12s} tier {x['tier']:2s} "
              f"{x['ts']:6.2f}-{x['te']:6.2f}s ({x['te'] - x['ts']:.2f}s)  {x['text']!r}")
    style = BRAND["captions"]["own_ads" if plan["ad_type"] == "own" else "client_ads"]
    caps = caption_lines(segs, srcs, set(plan["layers"]["captions"].get("emphasis", [])),
                         style["max_words_per_line"])
    ass = os.path.join(wd, f"{stem}.ass")
    write_ass(caps, W, H, ass, style)
    R["captions"] = len(caps)

    # level-match pieces, then pass 1: loudness
    t = time.time()
    segment_gains(segs)
    R["segments"] = [{"part": x["part"], "src": x["src"], "in": x["in"], "out": x["out"], "t0": x["t0"],
                      "lufs_in": x["lufs_in"], "gain_db": x["gain_db"]} for x in segs]
    print("piece levels: " + ", ".join(f"{x['part']} {x['lufs_in']}LUFS->{x['gain_db']:+}dB" for x in segs))
    meas = measure_loudness(segs)
    R["loudness_pass1"] = {k: meas[k] for k in ("input_i", "input_tp", "input_lra")}
    print(f"audio pass 1 ({time.time() - t:.1f}s): input {meas['input_i']} LUFS, TP {meas['input_tp']}")

    # pass 2: everything
    n = len(segs)
    args = seg_inputs(segs)
    idx = n
    for x in titles:
        args += ["-i", x["png"], "-i", x["matte"]]
        x["gi"], x["mi"] = idx, idx + 1
        idx += 2
    for x in graphics:
        args += (["-loop", "1", "-t", f"{total:.3f}", "-i", x["png"]] if x["kind"] == "still" else ["-i", x["png"]])
        x["gi"] = idx
        idx += 1
    card = end_card(W, H, cards)  # brief beat 9: logo card in the last seconds, nothing after it
    if card:
        args += ["-loop", "1", "-t", f"{card['s']:.3f}", "-i", card["png"],
                 "-f", "lavfi", "-t", f"{card['s']:.3f}", "-i", f"anullsrc=r={EXP['audio_rate']}:cl=stereo"]
        card["ci"], card["ai"] = idx, idx + 1
        idx += 2
        R["end_card_s"] = card["s"]
    fl = [f"[{i}:v]setpts=PTS-STARTPTS,fps={EXP['fps']},scale={W}:{H},format=yuv420p,setsar=1[v{i}]" for i in range(n)]
    aparts, _, clean = audio_chain(segs, meas)
    fl += aparts
    fl.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][acat]")
    cur = "vc"
    for k, x in enumerate(titles):
        ts, te = x["ts"], x["te"]
        fl += [f"[{cur}]split[tb{k}][tf{k}]",
               f"[{x['gi']}:v]format=rgba,setpts=PTS-STARTPTS,settb=1/{EXP['fps']},"
               f"tpad=start_duration={round(ts * EXP['fps']) / EXP['fps']:.4f}:start_mode=add:color=black@0.0[tp{k}]",
               f"[tb{k}][tp{k}]overlay={x['x']}:{x['y']}:enable='between(t,{ts:.3f},{te:.3f})'[tt{k}]",
               # matte padded with black (alpha 0) to the full length, so both branches stay in step
               f"[{x['mi']}:v]format=gray,scale={W}:{H}:flags=bicubic,setpts=PTS-STARTPTS,"
               # settb first: in the clip's 1 ms timebase tpad steps 33 ms per frame and drifts
               # ~5 frames early by 16 s (measured); 1/fps makes every padded frame exact
               f"settb=1/{EXP['fps']},tpad=start_duration={round(ts * EXP['fps']) / EXP['fps']:.4f}:start_mode=add"
               f":color=black:stop_duration={total - te + 1:.3f}"
               f":stop_mode=add:color=black[mm{k}]",
               f"[tf{k}][mm{k}]alphamerge[fg{k}]",
               f"[tt{k}][fg{k}]overlay=0:0:shortest=1:enable='between(t,{ts:.3f},{te:.3f})'[l3_{k}]"]
        cur = f"l3_{k}"
    for j, x in enumerate(graphics):
        if x["kind"] == "still":
            src = f"[{x['gi']}:v]format=rgba[gp{j}]"  # hard cut in: motion lives only in clips
        else:
            hold = (f",tpad=stop_mode=clone:stop_duration={x['te'] - x['ts'] + 1:.3f}" if x["hold_last"] else "")
            src = (f"[{x['gi']}:v]format=rgba,setpts=PTS-STARTPTS,settb=1/{EXP['fps']},"
                   f"tpad=start_duration={round(x['ts'] * EXP['fps']) / EXP['fps']:.4f}:start_mode=add"
                   f":color=black@0.0{hold}[gp{j}]")
        fl += [src, f"[{cur}][gp{j}]overlay={x['x']}:{x['y']}:enable='between(t,{x['ts']:.3f},{x['te']:.3f})'[g{j}]"]
        cur = f"g{j}"
    fl.append(f"[{cur}]ass={os.path.basename(ass)}[cap]")
    cur = "cap"
    if draft:
        ff = fontfile("Inter").replace(":", r"\:")
        fl.append(f"[cap]drawtext=fontfile='{ff}':text='DRAFT · NOT LOCKED':x=36:y=36:fontsize={round(H * 0.016)}"
                  f":fontcolor=white@0.9:box=1:boxcolor=black@0.55:boxborderw=14[dr]")
        cur = "dr"
    if card:
        fl += [f"[{card['ci']}:v]fps={EXP['fps']},scale={W}:{H},format=yuv420p,setsar=1[vcard]",
               f"[{card['ai']}:a]aresample={EXP['audio_rate']}[acard]",
               f"[{cur}][vcard]concat=n=2:v=1:a=0[vfin]", clean + "[an0]", "[an0][acard]concat=n=2:v=0:a=1[an]"]
        cur = "vfin"
    else:
        fl.append(clean + "[an]")
    fl += [f"[{cur}]split[vm][vd0]", f"[vd0]scale={DW}:{DH}:flags=lanczos[vd]", "[an]asplit[am][ad]"]
    master = os.path.join(out_dir, f"{stem}_master_{W}x{H}.mp4")
    delivery = os.path.join(out_dir, f"{stem}_{DW}x{DH}.mp4")
    common_a = ["-c:a", "aac", "-b:a", EXP["audio_bitrate"], "-ar", str(EXP["audio_rate"])]
    cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args +
           ["-filter_complex", ";".join(fl),
            "-map", "[vm]", "-map", "[am]", "-c:v", L["encoder"], "-preset", EXP["master"]["preset"],
            "-crf", str(EXP["master"]["crf"]), "-pix_fmt", "yuv420p", "-r", str(EXP["fps"]),
            "-threads", str(L["encode_threads"]), *common_a, "-movflags", "+faststart", master,
            "-map", "[vd]", "-map", "[ad]", "-c:v", L["encoder"], "-preset", EXP["delivery"]["preset"],
            "-crf", str(EXP["delivery"]["crf"]), "-pix_fmt", "yuv420p", "-r", str(EXP["fps"]),
            "-threads", str(L["encode_threads"]), *common_a, "-movflags", "+faststart", delivery])
    with open(os.path.join(wd, f"{stem}.ffmpeg.txt"), "w") as f:
        f.write(" ".join(cmd))
    peak_file = os.path.join(wd, "last_render_peak.json")
    need = None
    if os.path.exists(peak_file):
        with open(peak_file) as f:
            need = round(json.load(f)["ffmpeg_peak_ram_gb"] * 1.2, 1)
        _, avail = si.ram_bytes()
        if avail / si.GB < need:
            sys.exit(f"PREFLIGHT FAIL: last render of this ad peaked at {need / 1.2:.1f} GB; "
                     f"need {need} GB free, have {avail / si.GB:.1f} GB")
    with preflight.render_slot(os.path.join(ROOT, segs[0]["proxy"])) as pf:
        pf["need_ram_gb_this_ad"] = need
        R["preflight"] = pf
        t = time.time()
        peak = si.run_measured(cmd, cwd=wd)
        dt = time.time() - t
    R["render"] = {"seconds": round(dt, 1), "x_realtime": round(dt / total, 2),
                   "ffmpeg_peak_ram_gb": round(peak / si.GB, 2)}
    print(f"render: {R['render']}")
    with open(peak_file, "w") as f:
        json.dump(R["render"], f)
    R["hero"] = hero_id
    qa(R, plan, segs, srcs, titles, graphics, caps, master, delivery, proof, out_dir, stem, W, H, DW, style, hero_id)


def qa(R, plan, segs, srcs, titles, graphics, caps, master, delivery, proof, out_dir, stem, W, H, DW, style, hero_id):
    lo, hi = plan["output"].get("target_duration_s", [0, 1e9])
    checks = {}
    for name, path, (w, h) in (("master", master, (plan["output"]["master"]["w"], plan["output"]["master"]["h"])),
                               ("delivery", delivery, (plan["output"]["delivery"]["w"], plan["output"]["delivery"]["h"]))):
        p = media.probe(path)
        checks[f"{name}_size"] = {"status": "pass" if (p["w"], p["h"]) == (w, h) else "fail",
                                  "value": f"{p['w']}x{p['h']} @ {p['fps']}"}
        checks[f"{name}_duration"] = {"status": "pass" if lo <= p["duration"] <= hi else "fail",
                                      "value": f"{p['duration']:.2f}s (target {lo}-{hi}s)"}
        R[f"{name}_file"] = {"path": os.path.relpath(path, ROOT), "mb": round(os.path.getsize(path) / 1e6, 1)}
    lf = ingest.loudness(delivery)
    tgt = AUD["loudness_lufs"]
    checks["loudness"] = {"status": "pass" if lf["integrated_lufs"] is not None and abs(lf["integrated_lufs"] - tgt) <= 1.0
                          and lf["true_peak_dbtp"] <= AUD["true_peak_dbtp"] + 0.5 else "fail",
                          "value": f"{lf['integrated_lufs']} LUFS (target {tgt}), TP {lf['true_peak_dbtp']} dBTP, LRA {lf['loudness_range_lu']}"}
    kept = [dict(w) for s in segs for w in srcs[s["src"]]["words"] if s["in"] - 0.001 <= w["start"] < s["out"]]
    led = numbers_guard.spoken_ledger(kept)
    blocks = []
    for x in titles + graphics:
        blocks += [f"{x['text']!r}: {b}" for b in numbers_guard.check(x["text"], led)["block"]]
    for c in caps:
        blocks += [f"caption {c['text']!r}: {b}" for b in numbers_guard.check(c["text"], led)["block"]]
    checks["numbers_rule_final"] = {"status": "fail" if blocks else "pass", "value": blocks or "every number on screen was spoken"}
    bad = set(SOP["lock_sop_2_1"]["profanity"])
    heard = [w["w"] for w in kept if align.norm(w["w"]) in bad]
    checks["no_profanity"] = {"status": "fail" if heard else "pass",
                              "value": heard or "no profanity in the kept audio or captions"}
    # Phase 6: one biggest text moment, ticks on their words, component stills, beat map
    D7 = SOP["depth_sop_7"]
    bad = []
    for t in titles:
        dp = t["depth"]
        occ = dp["letters_hidden"]
        if not (occ["p90"] <= D7["occlusion_max"] and occ["median"] >= D7["occlusion_min"]):
            bad.append(f"{t['id']}: letters hidden median {occ['median']:.0%}, p90 {occ['p90']:.0%}")
        if dp["text_contrast"] < D7["min_text_contrast"]:
            bad.append(f"{t['id']}: text contrast {dp['text_contrast']}:1")
    checks["depth_title"] = {"status": "fail" if bad else ("pass" if titles else "n/a"),
                             "value": bad or [f"{t['id']}: hidden median {t['depth']['letters_hidden']['median']:.0%} "
                                              f"p90 {t['depth']['letters_hidden']['p90']:.0%}, contrast "
                                              f"{t['depth']['text_contrast']}:1, matte halo {t['depth']['matte_halo_pct']}%"
                                              for t in titles] or "no title"}
    checks["one_biggest_text_moment"] = graphics_qa.hero_check(titles, graphics, round(H * style["size_pct_of_height"] / 100), hero_id)
    tk = graphics_qa.tick_landing(delivery, graphics, DW / W, EXP["fps"],
                                  [components.PRIMARY[:3], components.SECONDARY[:3]])
    clash = [f"{x['id']} overlaps the face by {x['face_overlap_px']}px" for x in graphics if x.get("face_overlap_px")]
    checks["graphics_clear_of_face"] = {"status": "fail" if clash else "pass",
                                        "value": clash or "no graphic covers the speaker's face"}
    checks["ticks_land_on_words"] = tk
    aliases = SOP["beat_map_sop_2_2"]["aliases"]
    for sg in segs:
        sg["brief_beat"] = aliases.get((sg["beat"] or "").upper(), (sg["beat"] or "").upper())
    shots, sheet = graphics_qa.stills(delivery, graphics, titles, proof)
    tl = graphics_qa.beat_timeline(segs, graphics, titles, caps, hero_id, tk, media.probe(delivery)["duration"],
                                   os.path.join(proof, "phase6_beat_map.png"), f"{stem}: beat map and text components")
    R["phase6"] = {"stills": [{k: v for k, v in x.items() if k != "path"} for x in shots],
                   "sheet": os.path.relpath(sheet, ROOT), "beat_map": os.path.relpath(tl, ROOT)}
    R["qa"] = checks
    # proof stills: each title/graphic midpoint + contact sheet
    shots = [((x["ts"] + x["te"]) / 2, x["id"]) for x in titles + graphics]
    for t, name in shots:
        media.sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", delivery,
                  "-frames:v", "1", os.path.join(proof, f"{name}.png")])
    dur = media.probe(delivery)["duration"]
    media.sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", delivery, "-vf",
              f"fps=10/{dur:.2f},scale=270:480,tile=5x2:padding=6:color=0x121418", "-frames:v", "1",
              os.path.join(proof, "contact_sheet.png")])
    with open(os.path.join(out_dir, f"{stem}_qa.json"), "w") as f:
        json.dump(R, f, indent=2)
    print("\nQA:")
    for k, v in checks.items():
        print(f"  [{v['status'].upper():4s}] {k}: {v['value']}")
    for r in tk["rows"]:
        print(f"         tick {r['tick']!r:20s} word {r['word_at_s']}s  appears {r['appears_at_s']}s  {r['offset_ms']} ms")
    print(f"files: {R['master_file']}  {R['delivery_file']}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    main(a[0], "--draft" in a)
