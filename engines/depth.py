"""Phase 7: the depth look = person matte + title behind the head + slab motion.

  matte      MediaPipe multiclass person mask (half size), refined with a guided filter so
             edges follow the real image edges, then a soft contrast curve that kills faint halos
  slab       a solid brand-colour panel with the logo's slanted ends; the title sits on it, so it
             reads on any background (white text alone fails on a bright sky: measured)
  motion     the slab rises from behind the shoulders (ease-out), text fades in on it, then a slow
             upward drift while it holds; the head stays in front (layer L3), which is the depth cue

Numbers live in spec/sop-defaults.json depth_sop_7 (placeholder until the SOP).
"""
import json
import math
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import components  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "spec", "sop-defaults.json")) as f:
    D7 = json.load(f)["depth_sop_7"]


# ---------- matte ----------

def refine(matte, guide_rgb):
    """Guided filter (edges snap to the image), then a smoothstep that removes faint halo alpha."""
    # guide in 0..1: guided_eps is in those units (a 0..255 guide made eps ~0 and produced NaNs)
    g = cv2.cvtColor(np.ascontiguousarray(guide_rgb), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    m = cv2.ximgproc.guidedFilter(guide=g, src=np.nan_to_num(matte).astype(np.float32),
                                  radius=D7["guided_radius"], eps=D7["guided_eps"])
    m = np.nan_to_num(m)
    lo, hi = D7["alpha_lo"], D7["alpha_hi"]
    x = np.clip((m - lo) / (hi - lo), 0, 1)
    return (x * x * (3 - 2 * x)).astype(np.float32)


def edge_metrics(alpha):
    """soft_edge_px: average width of the semi-transparent edge; halo_pct: mean alpha in a ring
    20-48 px outside the person, beyond any real hair edge (should be ~0; >0 = background haze
    that would veil a title placed behind the head)."""
    a = alpha.astype(np.float32)
    hard = (a > 0.5).astype(np.uint8)
    cnts, _ = cv2.findContours(hard, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    perim = max(1.0, sum(cv2.arcLength(c, True) for c in cnts))
    soft = ((a > 0.05) & (a < 0.95)).sum()
    k = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))  # noqa: E731
    ring = (cv2.dilate(hard, k(48)) > 0) & ~(cv2.dilate(hard, k(20)) > 0)
    return {"soft_edge_px": round(float(soft / perim), 2),
            "halo_pct": round(float(a[ring].mean() * 100) if ring.any() else 0.0, 2)}


def flicker(alphas, ref=None):
    """Mean frame-to-frame alpha change inside the edge band of `ref` (default: the same mattes).
    Pass the same ref to compare two mattes on exactly the same pixels."""
    ref = ref or alphas
    d = []
    for (a, b), (ra, rb) in zip(zip(alphas, alphas[1:]), zip(ref, ref[1:])):
        band = ((ra > 0.05) & (ra < 0.95)) | ((rb > 0.05) & (rb < 0.95))
        if band.any():
            d.append(float(np.abs(a - b)[band].mean()))
    return round(float(np.mean(d)) * 100, 2) if d else 0.0


# ---------- contrast ----------

def rel_lum(rgb):
    c = np.asarray(rgb, np.float64) / 255
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]


def contrast(fg_rgb, bg_pixels):
    """WCAG contrast ratio, worst case: light text against the brightest 10% of the background
    behind it (dark text against the darkest 10%). A median hides a bright patch of sky."""
    lf = float(rel_lum(np.array(fg_rgb[:3])))
    lum = rel_lum(np.asarray(bg_pixels).reshape(-1, 3))
    lb = float(np.percentile(lum, 90 if lf > 0.5 else 10))
    hi, lo = max(lf, lb), min(lf, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def occlusion(layer_rgba, person_alpha, text_only_rgba=None):
    """Share of the title's letter pixels hidden behind the person."""
    a = np.asarray(text_only_rgba if text_only_rgba is not None else layer_rgba)[..., 3] > 128
    if not a.any():
        return 0.0
    return round(float((person_alpha[a] > 0.5).mean()), 3)


def depth_ok(occ):
    """The over-time rule (sop-defaults depth_sop_7 _depth_rule)."""
    return occ["p90"] <= D7["occlusion_max"] and occ["depth_frames"] >= D7["depth_frames_min"]


def place_title_over_time(text, W, H, alphas, y_guess, tier="L", slab=True):
    """Like place_title, but over every sampled frame of the title's time on screen. A moving
    speaker swings letter occlusion from 0 to ~17% at one height, so the rule splits in two:
    depth = the person cuts into the slab in >= depth_frames_min of frames; readability = the 90th
    percentile frame hides <= occlusion_max of the letters. Among heights meeting both, the most
    depth frames wins. Returns (y_center, {"median", "p90", "max", "depth_frames"})."""
    best = None
    step = max(4, H // 320)
    for dy in range(-H // 3, H // 6, step):
        yc = y_guess + dy
        layer, (x0, y0, bw, bh), _ = slab_frame(text, W, H, yc, 1.0, tier, slab, draw_text=False)
        if y0 < H * 0.03:
            continue
        txt, _, _ = slab_frame(text, W, H, yc, 1.0, tier, False)
        glyph = np.asarray(txt)[..., 3] > 128
        panel = np.asarray(layer)[..., 3] > 128 if slab else glyph
        occ = np.array([float((a[glyph] > 0.5).mean()) for a in alphas])
        over = np.array([float((a[panel] > 0.5).mean()) for a in alphas])
        r = {"median": round(float(np.median(occ)), 3), "p90": round(float(np.percentile(occ, 90)), 3),
             "max": round(float(occ.max()), 3), "depth_frames": round(float((over >= D7["slab_overlap_min"]).mean()), 3)}
        score = (0 if depth_ok(r) else 1, max(0.0, r["p90"] - D7["occlusion_max"]),
                 max(0.0, D7["depth_frames_min"] - r["depth_frames"]), -r["depth_frames"], r["p90"], abs(dy))
        if best is None or score < best[0]:
            best = (score, yc, r)
    return best[1], best[2]


def place_title(text, W, H, person_alpha, y_guess, tier="L", slab=True, t=1.0):
    """Move the title until the person hides occlusion_min..max of its letters (closest to target).
    Returns (y_center, occlusion)."""
    best = None
    step = max(8, H // 160)
    for dy in range(-H // 3, H // 6, step):
        yc = y_guess + dy
        layer, (x0, y0, bw, bh), L = slab_frame(text, W, H, yc, t, tier, slab)
        if y0 < H * 0.03:
            continue
        txt, _, _ = slab_frame(text, W, H, yc, t, tier, False) if slab else (layer, None, None)
        occ = occlusion(layer, person_alpha, txt)
        ok = D7["occlusion_min"] <= occ <= D7["occlusion_max"]
        score = (0 if ok else 1, abs(occ - D7["occlusion_target"]), abs(dy))
        if best is None or score < best[0]:
            best = (score, yc, occ)
    return best[1], best[2]


# ---------- slab ----------

def ease_out(x):
    return 1 - (1 - min(1.0, max(0.0, x))) ** 3


def slab_layout(text, W, tier="L", slab=True):
    """Font, lines and the slab rectangle size (px) for a title."""
    font, lines = components.fit_lines(text, components.tier_px(tier, W), W * D7["slab_max_w"], 2)
    d = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    asc, desc = font.getmetrics()
    lh = round((asc + desc) * 0.95)
    tw = max(d.textlength(ln, font=font) for ln in lines)
    padx, pady = (round(font.size * D7["slab_pad_x"]), round(font.size * D7["slab_pad_y"])) if slab else (0, 0)
    skew = round(font.size * D7["slab_skew"]) if slab else 0
    return {"font": font, "lines": lines, "lh": lh, "w": round(tw + 2 * padx + 2 * skew),
            "h": round(lh * len(lines) + 2 * pady), "padx": padx, "pady": pady, "skew": skew}


def slab_frame(text, W, H, y_center, t, tier="L", slab=True, color=None, text_color=None, draw_text=True):
    """Full-frame RGBA of the slab title at time t (seconds since it starts)."""
    L = slab_layout(text, W, tier, slab)
    color = color or components.PRIMARY
    text_color = text_color or components.ON_PRIMARY
    rise = ease_out(t / D7["rise_s"]) if slab else 1.0
    drift = -D7["drift_pct_per_s"] / 100 * H * max(0.0, t - D7["rise_s"])
    y_off = (1 - rise) * L["h"] * D7["rise_from"] + drift
    x0 = (W - L["w"]) / 2
    y0 = y_center - L["h"] / 2 + y_off
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if slab:
        s = L["skew"]
        # parallelogram with the logo's slanted cut on both ends
        d.polygon([(x0 + s, y0), (x0 + L["w"], y0), (x0 + L["w"] - s, y0 + L["h"]), (x0, y0 + L["h"])], fill=color)
    ta = ease_out((t - D7["text_in_s"][0]) / (D7["text_in_s"][1] - D7["text_in_s"][0])) if slab else 1.0
    if ta > 0 and draw_text:
        tl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dt = ImageDraw.Draw(tl)
        for i, ln in enumerate(L["lines"]):
            lw = dt.textlength(ln, font=L["font"])
            dt.text(((W - lw) / 2, y0 + L["pady"] + i * L["lh"] - L["font"].size * 0.06), ln,
                    font=L["font"], fill=text_color[:3] + (round(255 * ta),))
        img = Image.alpha_composite(img, tl)
    return img, (x0, y0, L["w"], L["h"]), L


def composite(frame_rgb, layer_rgba, alpha):
    """bg -> layer (title/slab) -> person cut-out on top: the depth stack."""
    bg = Image.fromarray(frame_rgb).convert("RGBA")
    out = Image.alpha_composite(bg, layer_rgba)
    person = Image.fromarray(frame_rgb).convert("RGBA")
    person.putalpha(Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8)))
    return Image.alpha_composite(out, person)


def motion_curve(steps=None):
    """(t, y_offset_fraction_of_slab_h, text_alpha) samples, for the proof."""
    out = []
    for t in steps or [0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.5, 1.0]:
        rise = ease_out(t / D7["rise_s"])
        ta = ease_out((t - D7["text_in_s"][0]) / (D7["text_in_s"][1] - D7["text_in_s"][0]))
        out.append((t, round((1 - rise) * D7["rise_from"], 3), round(ta, 2)))
    return out


__all__ = ["refine", "edge_metrics", "flicker", "contrast", "slab_frame", "composite", "motion_curve", "math"]
