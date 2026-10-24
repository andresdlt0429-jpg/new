"""Phase 6 QA on a rendered variant: one biggest text moment, ticks landing on words,
proof stills of every component, and a beat-map timeline image."""
import json
import os
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "spec", "sop-defaults.json")) as f:
    SOP = json.load(f)
G4 = SOP["graphics_sop_4"]
BEAT_COLORS = {"HOOK": (230, 120, 40), "PROBLEM": (190, 70, 70), "DIAGNOSIS": (150, 80, 170),
               "SOLUTION": (40, 120, 230), "FUTURE-PACE": (60, 160, 120), "GUARANTEE": (235, 190, 60),
               "PROOF": (120, 120, 200), "CTA": (40, 170, 200), "SCARCITY": (160, 160, 160)}


def font(px, bold=False):
    path = subprocess.run(["fc-match", "-f", "%{file}", "Inter:bold" if bold else "Inter"],
                          capture_output=True, text=True).stdout
    return ImageFont.truetype(path, px)


def hero_check(titles, graphics, caption_px, hero_id):
    sizes = [(x["id"], x.get("tier"), x["font_px"]) for x in titles + graphics if x.get("font_px")]
    sizes.append(("captions", "-", caption_px))
    hero = [s for s in sizes if s[0] == hero_id]
    xl = [s for s in sizes if s[1] == "XL"]
    if not hero:
        return {"status": "fail", "value": f"no hero in this variant (plan hero = {hero_id})", "sizes": sizes}
    others = max(s[2] for s in sizes if s[0] != hero_id)
    ratio = hero[0][2] / others
    ok = len(xl) == 1 and ratio >= G4["hero_min_ratio"]
    return {"status": "pass" if ok else "fail", "sizes": sizes,
            "value": f"hero {hero_id} {hero[0][2]}px = {ratio:.2f}x the next biggest text "
                     f"(need >= {G4['hero_min_ratio']}x), XL count {len(xl)}"}


def crop_frames(video, t0, dur, box):
    x, y, w, h = box
    out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0, t0):.3f}", "-t", f"{dur:.3f}",
                          "-i", video, "-vf", f"crop={w}:{h}:{x}:{y},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    n = len(out) // (w * h)
    return np.frombuffer(out[: n * w * h], np.uint8).reshape(n, h, w).astype(np.int16)


def tick_landing(video, graphics, scale, fps):
    """For every tick item: when it actually appears in the rendered video vs its spoken word."""
    rows = []
    for g in graphics:
        if g.get("component") != "ticks":
            continue
        for i, it in enumerate(g["items"]):
            want = g["ts"] + it["t"]
            bx, by = round(g["x"] * scale), round((g["y"] + i * g["row_h"]) * scale)
            box = (bx // 2 * 2, by // 2 * 2, round(g["w"] * scale) // 2 * 2, round(g["row_h"] * scale) // 2 * 2)
            win0 = want - 0.4
            fr = crop_frames(video, win0, 0.8, box)
            diff = np.abs(fr - fr[0]).mean(axis=(1, 2))
            hit = int(np.argmax(diff > max(4.0, 0.3 * diff.max()))) if diff.max() > 4.0 else None
            got = None if hit is None else win0 + hit / fps
            rows.append({"tick": it["text"], "word_at_s": round(want, 3),
                         "appears_at_s": None if got is None else round(got, 3),
                         "offset_ms": None if got is None else round((got - want) * 1000)})
    tol = 1000 / fps + 17
    ok = rows and all(r["offset_ms"] is not None and abs(r["offset_ms"]) <= tol for r in rows)
    return {"status": "pass" if ok else ("n/a" if not rows else "fail"), "rows": rows,
            "value": (f"{len(rows)} ticks, worst offset "
                      f"{max(abs(r['offset_ms']) for r in rows if r['offset_ms'] is not None)} ms (limit {tol:.0f} ms)")
            if rows and all(r["offset_ms"] is not None for r in rows) else "tick not detected" if rows else "no ticks"}


def grab(video, t, path):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0, t):.3f}", "-i", video,
                    "-frames:v", "1", path], check=True)
    return path


def stills(video, graphics, titles, proof):
    """Proof stills at the moments that matter for each component."""
    shots = []
    for g in graphics:
        c = g.get("component", "text")
        if c == "icp_callout":
            shots.append((0.0, f"{g['id']} icp_callout {g['tier']} (first frame)"))
        elif c == "ticks":
            for it in g["items"]:
                shots.append((g["ts"] + it["t"] + 0.2, f"tick '{it['text']}' lands"))
        elif c == "cta_button":
            per = 1 / G4["pulse_hz"]
            shots += [(g["ts"] + 0.15, f"{g['id']} cta pulse low"), (g["ts"] + 0.15 + per / 2, f"{g['id']} cta pulse high")]
        else:
            shots.append(((g["ts"] + g["te"]) / 2, f"{g['id']} {c} {g['tier']}"))
    for t in titles:
        shots.append(((t["ts"] + t["te"]) / 2, f"{t['id']} title-behind-head {t['tier']}"))
    shots.sort()
    out = []
    for k, (t, label) in enumerate(shots):
        p = grab(video, t, os.path.join(proof, f"p6_{k:02d}.png"))
        out.append({"t": round(t, 2), "label": label, "path": p})
    # contact sheet
    ims = [Image.open(o["path"]).convert("RGB").resize((270, 480)) for o in out]
    cols = 5
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 280 + 10, rows * 530 + 10), (18, 20, 24))
    d = ImageDraw.Draw(sheet)
    f = font(15)
    for i, (im, o) in enumerate(zip(ims, out)):
        x, y = 10 + (i % cols) * 280, 10 + (i // cols) * 530
        sheet.paste(im, (x, y + 40))
        d.text((x, y + 4), f"{o['t']:.2f}s", font=font(16, True), fill=(235, 235, 235))
        d.text((x, y + 22), o["label"][:36], font=f, fill=(170, 175, 185))
    sp = os.path.join(proof, "phase6_components.png")
    sheet.save(sp)
    return out, sp


def beat_timeline(segs, graphics, titles, caps, hero_id, ticks, total, path, title):
    W, PAD = 2400, 60
    rows = [("beats", 70)] + [(x["id"], 54) for x in sorted(titles + graphics, key=lambda x: x["ts"])] + [("captions", 40)]
    H = 150 + sum(h for _, h in rows) + 40 + 34 * (len(ticks["rows"]) + 1)
    img = Image.new("RGB", (W, H), (18, 20, 24))
    d = ImageDraw.Draw(img)
    X = lambda t: PAD + 240 + (W - 2 * PAD - 240) * t / total  # noqa: E731
    d.text((PAD, 24), title, font=font(34, True), fill=(235, 235, 235))
    d.text((PAD, 74), "Beat map (Ad Editor Brief) -> component -> size tier. Star = the one biggest text moment.",
           font=font(22), fill=(170, 175, 185))
    for s in range(int(total) + 1):
        d.line([X(s), 120, X(s), H - 34 * (len(ticks["rows"]) + 1) - 40], fill=(40, 42, 48))
        if s % 5 == 0:
            d.text((X(s) - 8, 112 - 24), f"{s}s", font=font(16), fill=(130, 130, 140))
    y = 130
    for name, h in rows:
        if name == "beats":
            d.text((PAD, y + 20), "beat", font=font(22, True), fill=(220, 220, 220))
            runs = []
            for sg in segs:
                if runs and runs[-1][0] == sg["brief_beat"]:
                    runs[-1][2] = sg["t1"]
                else:
                    runs.append([sg["brief_beat"], sg["t0"], sg["t1"]])
            for b, t0, t1 in runs:
                d.rectangle([X(t0) + 1, y + 6, X(t1) - 1, y + h - 6], fill=BEAT_COLORS.get(b, (120, 120, 120)))
                d.text((X(t0) + 8, y + 22), b, font=font(18, True), fill=(15, 15, 15))
        elif name == "captions":
            d.text((PAD, y + 8), "captions", font=font(20), fill=(200, 200, 200))
            for c in caps:
                d.rectangle([X(c["start"]), y + 8, X(c["end"]), y + h - 8], fill=(90, 95, 110), outline=(18, 20, 24))
        else:
            x = next(g for g in titles + graphics if g["id"] == name)
            star = " ★" if name == hero_id else ""
            comp = x.get("component", "title" if name.startswith("t-") else "text")
            d.text((PAD, y + 14), f"{comp} {x.get('tier', '')}{star}"[:26], font=font(20, star != ""), fill=(220, 220, 220))
            c = BEAT_COLORS.get(x.get("beat"), (120, 120, 120))
            d.rectangle([X(x["ts"]), y + 8, X(x["te"]), y + h - 8], fill=c)
            d.text((X(x["ts"]) + 8, y + 16), x["text"][:48], font=font(17, True), fill=(15, 15, 15))
            if x.get("component") == "ticks":
                for it in x["items"]:
                    tx = X(x["ts"] + it["t"])
                    d.line([tx, y + 4, tx, y + h - 4], fill=(255, 255, 255), width=3)
        y += h
    y += 20
    d.text((PAD, y), f"Ticks vs spoken words (measured in the rendered video): {ticks['value']}",
           font=font(22, True), fill=(235, 235, 235))
    for r in ticks["rows"]:
        y += 34
        d.text((PAD + 20, y), f"{r['tick']:<16s} word {r['word_at_s']:6.2f}s  appears {r['appears_at_s']}s  "
                              f"offset {r['offset_ms']} ms", font=font(20), fill=(200, 205, 215))
    img.save(path)
    return path
