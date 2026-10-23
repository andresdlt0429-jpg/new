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
    img.save(path)


def title_png(item, W, H, path):
    g = BRAND["graphics"]
    font = ImageFont.truetype(fontfile(BRAND["fonts"]["headline"] or BRAND["fonts"]["fallback_headline"]),
                              round(W * g["title_size_pct_of_width"] / 100))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lines = wrap(d, item["text"], font, W * 0.92)
    lh = font.size * 1.02
    y0 = H * item.get("y_pct", 25) / 100 - lh * len(lines) / 2
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    for i, ln in enumerate(lines):
        lw = d.textlength(ln, font=font)
        x, y = (W - lw) / 2, y0 + i * lh
        ds.text((x + font.size * 0.04, y + font.size * 0.05), ln, font=font,
                fill=(0, 0, 0, round(255 * g["title_shadow_alpha"])))
        d.text((x, y), ln, font=font, fill=hex_rgb(g["title_color"]))
    Image.alpha_composite(shadow, img).save(path)


def matte_for(title, ts, te, segs, W, H, path):
    """Speaker matte (multiclass, half size, smoothed) for the title's time range only."""
    s = next(x for x in segs if x["t0"] - 0.001 <= ts < x["t1"])
    start = s["in"] + (ts - s["t0"])
    hw, hh = W // 2, H // 2
    seg = spike_layers.segmenter("multiclass")
    enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt",
                            "gray", "-s", f"{hw}x{hh}", "-r", "30", "-i", "-", "-c:v", "ffv1", path],
                           stdin=subprocess.PIPE)
    prev, n, t = None, 0, time.time()
    for _, small in media.read_frames(os.path.join(ROOT, s["proxy"]), hw, hh, 30, start=start, dur=te - ts):
        m = spike_layers.person_mask(seg, "multiclass", small)
        m = m if prev is None else spike_layers.EMA * m + (1 - spike_layers.EMA) * prev
        prev = m
        enc.stdin.write((m * 255).astype(np.uint8).tobytes())
        n += 1
    enc.stdin.close()
    enc.wait()
    seg.close()
    return {"frames": n, "seconds": round(time.time() - t, 1)}


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
        emph = [w for w in ln if w["ref"] in emphasis][:2]
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
    ln = f"loudnorm=I={AUD['loudness_lufs']}:TP={AUD['true_peak_dbtp']}:LRA={AUD['loudness_range_lu']}"
    if measured:
        ln += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
               f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
               f":offset={measured['target_offset']}:linear=true")
    ln += ":print_format=json"
    clean = f"[acat]highpass=f={AUD['highpass_hz']},afftdn=nr={AUD['denoise_nr_db']},{ln},aresample=48000"
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
        p = os.path.join(cards, f"{t['id']}.png")
        title_png(t, W, H, p)
        mp = os.path.join(wd, f"matte_{t['id']}.mkv")
        stats = matte_for(t, ts, te, segs, W, H, mp)
        titles.append({"id": t["id"], "text": t["text"], "ts": ts, "te": te, "png": p, "matte": mp, "matte_stats": stats})
        print(f"title {t['id']} {ts:.2f}-{te:.2f}s  matte {stats}")
    for g in plan["layers"]["graphics"]:
        sp = span(g)
        if not sp:
            continue
        a, b = sp
        p = os.path.join(cards, f"{g['id']}.png")
        graphic_png(g, W, H, p)
        graphics.append({"id": g["id"], "text": g["text"], "ts": a, "te": b, "png": p})
        print(f"graphic {g['id']} {a:.2f}-{b:.2f}s  {g['text']!r}")
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
        args += ["-loop", "1", "-t", f"{total:.3f}", "-i", x["png"], "-i", x["matte"]]
        x["gi"], x["mi"] = idx, idx + 1
        idx += 2
    for x in graphics:
        args += ["-loop", "1", "-t", f"{total:.3f}", "-i", x["png"]]
        x["gi"] = idx
        idx += 1
    fl = [f"[{i}:v]setpts=PTS-STARTPTS,fps={EXP['fps']},scale={W}:{H},format=yuv420p,setsar=1[v{i}]" for i in range(n)]
    aparts, _, clean = audio_chain(segs, meas)
    fl += aparts
    fl.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][acat]")
    cur = "vc"
    for k, x in enumerate(titles):
        ts, te = x["ts"], x["te"]
        fl += [f"[{cur}]split[tb{k}][tf{k}]",
               f"[{x['gi']}:v]format=rgba,fade=t=in:st={ts:.3f}:d=0.12:alpha=1[tp{k}]",
               f"[tb{k}][tp{k}]overlay=0:0:enable='between(t,{ts:.3f},{te:.3f})'[tt{k}]",
               f"[tf{k}]trim=start={ts:.3f}:end={te:.3f},setpts=PTS-STARTPTS[fv{k}]",
               f"[{x['mi']}:v]format=gray,scale={W}:{H}:flags=bicubic,setpts=PTS-STARTPTS[mm{k}]",
               f"[fv{k}][mm{k}]alphamerge,setpts=PTS+{ts:.3f}/TB[fg{k}]",
               f"[tt{k}][fg{k}]overlay=0:0:eof_action=pass:enable='between(t,{ts:.3f},{te:.3f})'[l3_{k}]"]
        cur = f"l3_{k}"
    for j, x in enumerate(graphics):
        fl += [f"[{x['gi']}:v]format=rgba,fade=t=in:st={x['ts']:.3f}:d=0.12:alpha=1[gp{j}]",
               f"[{cur}][gp{j}]overlay=0:0:enable='between(t,{x['ts']:.3f},{x['te']:.3f})'[g{j}]"]
        cur = f"g{j}"
    fl.append(f"[{cur}]ass={os.path.basename(ass)}[cap]")
    cur = "cap"
    if draft:
        ff = fontfile("Inter").replace(":", r"\:")
        fl.append(f"[cap]drawtext=fontfile='{ff}':text='DRAFT · NOT LOCKED':x=36:y=36:fontsize={round(H * 0.016)}"
                  f":fontcolor=white@0.9:box=1:boxcolor=black@0.55:boxborderw=14[dr]")
        cur = "dr"
    fl += [f"[{cur}]split[vm][vd0]", f"[vd0]scale={DW}:{DH}:flags=lanczos[vd]",
           clean + "[an]", "[an]asplit[am][ad]"]
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
    with preflight.render_slot(os.path.join(ROOT, segs[0]["proxy"])) as pf:
        R["preflight"] = pf
        t = time.time()
        peak = si.run_measured(cmd, cwd=wd)
        dt = time.time() - t
    R["render"] = {"seconds": round(dt, 1), "x_realtime": round(dt / total, 2),
                   "ffmpeg_peak_ram_gb": round(peak / si.GB, 2)}
    print(f"render: {R['render']}")
    qa(R, plan, segs, srcs, titles, graphics, caps, master, delivery, proof, out_dir, stem)


def qa(R, plan, segs, srcs, titles, graphics, caps, master, delivery, proof, out_dir, stem):
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
    print(f"files: {R['master_file']}  {R['delivery_file']}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    main(a[0], "--draft" in a)
