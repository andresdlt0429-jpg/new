"""Phase 6: on-screen text components (stand-in for SOP 4 + beat map 2.2: spec/ad-editor-brief.md).

Each component turns one plan graphic into something ffmpeg overlays in the single render pass:
a still PNG (no motion) or a short RGBA clip (FFV1 + alpha), cropped to the component's box.

Motion is limited to what the brief allows:
  pop    the text arrives on its spoken word (scale pop_from -> 1 over pop_s); then it holds
  cut    hard cut in, no motion (hook: "flat reads as real"; guarantee: "no animated text")
  pulse  CTA button text pulses ("pulsing button text"), arrow points down to the link
  ticks  each list item lands on the word it shows ("ticks that lag behind the words" = mistake)
Never: word-by-word effects, count-ups (a count-up shows numbers that were never spoken).

Sizes come in tiers (graphics_sop_4.tiers_pct_of_width); XL is reserved for the one
biggest text moment per variant (beat_map_sop_2_2.hero_priority).
"""
import json
import math
import os
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "spec", "sop-defaults.json")) as f:
    SOP = json.load(f)
with open(os.path.join(ROOT, "brand.json")) as f:
    BRAND = json.load(f)
G4 = SOP["graphics_sop_4"]
FPS = SOP["export_sop_5"]["fps"]


def _font_path(bold=True):
    fam = BRAND["fonts"]["headline"] or BRAND["fonts"]["fallback_headline"]
    return subprocess.run(["fc-match", "-f", "%{file}", f"{fam}:{'black' if bold else 'bold'}"],
                          capture_output=True, text=True).stdout


def rgba(h, a=255):
    h = (h or "#FFFFFF").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


PRIMARY = rgba(BRAND["colors"]["primary"] or "#1070F0")
SECONDARY = rgba(BRAND["colors"]["secondary"] or "#F8D898")
WHITE, SHADOW = rgba("#FFFFFF"), (0, 0, 0, 150)


def tier_px(tier, W):
    return round(W * G4["tiers_pct_of_width"][tier] / 100)


def fit_lines(text, font_px, max_w, max_lines):
    """Wrap to max_lines; shrink the font until every line fits max_w. Returns (font, lines)."""
    px = font_px
    while px > 12:
        font = ImageFont.truetype(_font_path(), px)
        d = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if d.textlength(t, font=font) <= max_w or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= max_lines and all(d.textlength(ln, font=font) <= max_w for ln in lines):
            return font, lines
        px = int(px * 0.94)
    return ImageFont.truetype(_font_path(), px), [text]


def text_block(lines, font, color, box=None, pad=0.42, shadow=True, align="center"):
    """Render lines (optionally on a rounded box) to a tight RGBA image."""
    d0 = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    asc, desc = font.getmetrics()
    lh = round((asc + desc) * 0.98)
    tw = max(d0.textlength(ln, font=font) for ln in lines)
    p = round(font.size * pad) if box else round(font.size * 0.12)
    w, h = round(tw + 2 * p), round(lh * len(lines) + 2 * p * (0.8 if box else 1))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if box:
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=round(font.size * 0.32), fill=box)
    if shadow and not box:
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ds = ImageDraw.Draw(sh)
        for i, ln in enumerate(lines):
            x = (w - d0.textlength(ln, font=font)) / 2 if align == "center" else p
            ds.text((x, p * 0.8 + i * lh + font.size * 0.05), ln, font=font, fill=SHADOW)
        img = Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(font.size * 0.05)), img)
        d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        x = (w - d0.textlength(ln, font=font)) / 2 if align == "center" else p
        d.text((x, (p * 0.8 if box else p) + i * lh), ln, font=font, fill=color)
    return img


def scaled(img, s):
    if abs(s - 1) < 1e-3:
        return img
    w, h = img.size
    return img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def pop_scale(t):
    """0 -> pop_from at t=0, slight overshoot, 1.0 by pop_s."""
    d = G4["pop_s"]
    if t >= d:
        return 1.0
    x = t / d
    return G4["pop_from"] + (1 - G4["pop_from"]) * (1 + 0.18 * math.sin(math.pi * x)) * x


# ---------- components: each returns a list of frames (RGBA, same canvas size) or a still ----------

def size_px(item, W):
    return item.get("px") or tier_px(item["tier"], W)


def icp_callout(item, W, H):
    font, lines = fit_lines(item["text"], size_px(item, W), W * 0.86, G4["max_lines"])
    return {"still": text_block(lines, font, WHITE, box=PRIMARY), "font_px": font.size}


def phrase(item, W, H):
    font, lines = fit_lines(item["text"], size_px(item, W), W * 0.86, G4["max_lines"])
    img = text_block(lines, font, WHITE, box=(0, 0, 0, 165))
    return {"anim": "pop" if item.get("anim") == "pop" else None, "still": img, "font_px": font.size}


def guarantee(item, W, H):
    # one oversized line (two at most), one color, no box, no motion
    font, lines = fit_lines(item["text"], size_px(item, W), W * 0.94, G4["max_lines"])
    return {"still": text_block(lines, font, SECONDARY if item["tier"] == "XL" else WHITE), "font_px": font.size}


def cta_button(item, W, H, dur):
    font, lines = fit_lines(item["text"], tier_px(item["tier"], W), W * 0.78, 1)
    btn = text_block(lines, font, WHITE, box=PRIMARY, pad=0.55)
    aw = round(font.size * 0.9)
    arrow = Image.new("RGBA", (aw, aw), (0, 0, 0, 0))
    ImageDraw.Draw(arrow).polygon([(0, aw * 0.25), (aw, aw * 0.25), (aw / 2, aw)], fill=WHITE)
    amp = G4["pulse_amp"]
    cw, ch = round(btn.width * (1 + amp) + 4), round(btn.height * (1 + amp) + aw * 1.6)
    frames = []
    for n in range(max(1, round(dur * FPS))):
        t = n / FPS
        s = pop_scale(t) * (1 + amp * 0.5 * (1 - math.cos(2 * math.pi * G4["pulse_hz"] * t)))
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        b = scaled(btn, s)
        canvas.alpha_composite(b, ((cw - b.width) // 2, (round(btn.height * (1 + amp)) - b.height) // 2))
        bob = round(aw * 0.18 * (0.5 - 0.5 * math.cos(2 * math.pi * G4["pulse_hz"] * t)))
        canvas.alpha_composite(arrow, ((cw - aw) // 2, round(btn.height * (1 + amp)) + round(aw * 0.2) + bob))
        frames.append(canvas)
    return {"frames": frames, "font_px": font.size}


def ticks(item, W, H, dur):
    """Vertical tick list. item["items"] = [{"text", "t"}] with t = seconds after the list starts."""
    px = tier_px(item["tier"], W)
    font = ImageFont.truetype(_font_path(), px)
    d0 = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    rows = item["items"]
    lh = round(px * 1.38)
    mark = round(px * 0.95)
    tw = max(d0.textlength(r["text"], font=font) for r in rows)
    cw, ch = round(mark * 1.35 + tw + px * 0.6), round(lh * len(rows) + px * 0.3)
    row_imgs = []
    for i, r in enumerate(rows):
        last = i == len(rows) - 1
        img = Image.new("RGBA", (cw, lh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, (lh - mark) / 2, mark, (lh + mark) / 2], radius=round(mark * 0.28),
                            fill=SECONDARY if last else PRIMARY)
        d.line([(mark * 0.24, lh / 2), (mark * 0.43, lh / 2 + mark * 0.2), (mark * 0.78, lh / 2 - mark * 0.22)],
               fill=(20, 20, 20, 255) if last else WHITE, width=max(3, round(mark * 0.12)), joint="curve")
        sh = Image.new("RGBA", (cw, lh), (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((mark * 1.35, (lh - px) / 2 - px * 0.08 + px * 0.05), r["text"], font=font, fill=SHADOW)
        img = Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(px * 0.05)), img)
        ImageDraw.Draw(img).text((mark * 1.35, (lh - px) / 2 - px * 0.08), r["text"], font=font,
                                 fill=SECONDARY if last else WHITE)
        row_imgs.append(img)
    frames = []
    for n in range(max(1, round(dur * FPS))):
        t = n / FPS
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        for i, (r, img) in enumerate(zip(rows, row_imgs)):
            if t + 1e-6 < r["t"]:  # r["t"] is frame-aligned by the renderer
                continue
            s = pop_scale(t - r["t"])
            im = scaled(img, s)
            canvas.alpha_composite(im, (0, i * lh + (lh - im.height) // 2))
        frames.append(canvas)
    return {"frames": frames, "font_px": px, "row_h": lh}


def pop_frames(still, dur):
    w, h = still.size
    n_pop = min(max(1, round(dur * FPS)), round(G4["pop_s"] * FPS) + 1)
    frames = []
    for n in range(n_pop):
        s = pop_scale(n / FPS)
        c = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        im = scaled(still, s)
        c.alpha_composite(im, ((w - im.width) // 2, (h - im.height) // 2))
        frames.append(c)
    return frames  # the renderer holds the last frame (tpad clone) for the rest of the span


BUILDERS = {"icp_callout": icp_callout, "phrase": phrase, "guarantee": guarantee,
            "cta_button": cta_button, "ticks": ticks}


def write_clip(frames, path):
    w, h = frames[0].size
    w2, h2 = w + w % 2, h + h % 2
    p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                          "-s", f"{w2}x{h2}", "-r", str(FPS), "-i", "-", "-c:v", "ffv1", "-pix_fmt", "bgra", path],
                         stdin=subprocess.PIPE)
    for fr in frames:
        if fr.size != (w2, h2):
            c = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
            c.paste(fr, (0, 0))
            fr = c
        p.stdin.write(fr.tobytes())
    p.stdin.close()
    p.wait()


def build(item, W, H, dur, out_base):
    """-> {"path", "kind": "still"|"clip", "w", "h", "font_px", "hold_last": bool}"""
    kind = item["kind"]
    if kind in ("cta_button", "ticks"):
        r = BUILDERS[kind](item, W, H, dur)
    else:
        r = BUILDERS[kind](item, W, H)
    if "frames" not in r and r.get("anim") == "pop":
        r["frames"] = pop_frames(r["still"], dur)
        r["hold_last"] = True
    if "frames" in r:
        path = out_base + ".mkv"
        write_clip(r["frames"], path)
        w, h = r["frames"][0].size
        return {"path": path, "kind": "clip", "w": w, "h": h, "font_px": r["font_px"],
                "hold_last": r.get("hold_last", False), "preview": r["frames"][-1], "row_h": r.get("row_h")}
    path = out_base + ".png"
    r["still"].save(path)
    return {"path": path, "kind": "still", "w": r["still"].width, "h": r["still"].height,
            "font_px": r["font_px"], "hold_last": False, "preview": r["still"]}
