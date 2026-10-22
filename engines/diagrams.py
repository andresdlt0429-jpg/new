"""Draw the architecture diagrams into plans/: pipeline.png and layers.png.

  python3 engines/diagrams.py
"""
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG, INK, DIM = (18, 20, 24), (235, 235, 235), (160, 165, 175)
DONE, NEXT, WAIT = (40, 150, 90), (30, 110, 220), (170, 110, 20)


def font(size, bold=False):
    path = subprocess.run(["fc-match", "-f", "%{file}", "Inter:bold" if bold else "Inter"],
                          capture_output=True, text=True).stdout
    return ImageFont.truetype(path, size)


def box(d, xy, fill, title, lines, w_title=30, w_line=21):
    d.rounded_rectangle(xy, radius=18, fill=fill)
    x, y = xy[0] + 22, xy[1] + 16
    d.text((x, y), title, font=font(w_title, True), fill=(255, 255, 255))
    for k, ln in enumerate(lines):
        d.text((x, y + 44 + k * 28), ln, font=font(w_line), fill=(235, 240, 245))


def arrow(d, x0, y0, x1, y1, color=DIM):
    d.line([x0, y0, x1, y1], fill=color, width=4)
    if x0 == x1:
        s = 1 if y1 > y0 else -1
        d.polygon([(x1 - 11, y1 - 16 * s), (x1 + 11, y1 - 16 * s), (x1, y1)], fill=color)
    else:
        s = 1 if x1 > x0 else -1
        d.polygon([(x1 - 16 * s, y1 - 11), (x1 - 16 * s, y1 + 11), (x1, y1)], fill=color)


def pipeline(path):
    W, H = 1800, 1910
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((60, 40), "DLT auto-editor: pipeline", font=font(52, True), fill=INK)
    d.text((60, 110), "One clip in, ad variants out. Every arrow is a file on disk, so any step can be re-run alone.",
           font=font(26), fill=DIM)
    for k, (c, lab) in enumerate([(DONE, "built (Phase 1-2)"), (NEXT, "to build"), (WAIT, "needs spec/ from Andy")]):
        d.rounded_rectangle([60 + k * 380, 165, 90 + k * 380, 195], radius=6, fill=c)
        d.text((102 + k * 380, 164), lab, font=font(24), fill=INK)

    L, R, BW = 80, 960, 760
    rows = [
        (240, (L, DONE, "1  check_env", ["once per machine: versions, RAM, GPU", "-> engines/machine_limits.json"]),
              (R, WAIT, "3  script parse", ["spec/scripts/*.md: TIME/BEAT, SAY,", "ON SCREEN, hooks, Body A/B, Meta scan",
                                             "-> plans/<ad>/script.json"])),
        (470, (L, DONE, "2  ingest", ["raw clip -> 1440p working copy (proxy/)", "words, numbers ledger, pauses,",
                                       "loudness, faces -> work/<clip>/"]), None),
    ]
    for y, a, b in rows:
        box(d, [a[0], y, a[0] + BW, y + 190], a[1], a[2], a[3])
        if b:
            box(d, [b[0], y, b[0] + BW, y + 190], b[1], b[2], b[3])
    arrow(d, L + BW / 2, 430, L + BW / 2, 470)

    y = 730
    box(d, [L, y, R + BW, y + 210], NEXT, "4  plan draft  ->  plans/<ad>/plan.json",
        ["Script SAY lines are matched to the transcript to find the cut points (in/out per beat).",
         "ON SCREEN lines become layers anchored to the words they belong with.",
         "Andy edits the plan (or tells me what to change). The plan is the only thing the renderer reads."])
    arrow(d, L + BW / 2, 660, L + BW / 2, y)
    arrow(d, R + BW / 2, 430, R + BW / 2, y)

    y2 = 1010
    box(d, [L, y2, L + BW, y2 + 250], NEXT, "5  validate",
        ["schema + brand.json caption rules", "rule 4: every on-screen number spoken",
         "script SAY vs transcript (numbers too)", "speaker present for behind-head titles",
         "safe zones, duration, Meta scan present"])
    box(d, [R, y2, R + BW, y2 + 250], NEXT, "6  lock  (Andy)",
        ["\"Lock before this runs\"", "lock = hash of plan + script + brand", "any edit after lock = unlocked again",
         "renderer refuses unlocked plans"])
    arrow(d, L + BW / 2, 940, L + BW / 2, y2)
    arrow(d, L + BW, y2 + 125, R, y2 + 125)

    y3 = 1330
    box(d, [L, y3, R + BW, y3 + 220], NEXT, "7  build assets  (one at a time, preflight each)",
        ["L2/L4 text + cards -> cards/<ad>/*.png  (Pillow, brand fonts)",
         "L3 speaker matte -> work/<clip>/matte_*.mkv, ONLY for behind-head moments (multiclass, 144 ms/frame)",
         "L5 captions -> .ass from the kept words (own vs client style from brand.json)"])
    arrow(d, R + BW / 2, y2 + 250, R + BW / 2, y3)

    y4 = 1620
    box(d, [L, y4, L + BW, y4 + 220], NEXT, "8  compose  (one ffmpeg pass)",
        ["cut + reframe -> L1..L5 -> loudness", "-> out/<ad>/<variant>_master_1440.mp4",
         "-> 1080x1920 delivery copy", "measured: 9.3 fps = ~193 s per output min"])
    box(d, [R, y4, R + BW, y4 + 220], NEXT, "9  QA  ->  Andy",
        ["probe size/fps/duration, loudness, black", "frames, re-check rule 4 on final text,",
         "proof stills + qa.json", "Andy's corrections -> my-standards.md"])
    arrow(d, L + BW / 2, y3 + 220, L + BW / 2, y4)
    arrow(d, L + BW, y4 + 110, R, y4 + 110)
    d.line([R + BW + 10, y4 + 110, R + BW + 40, y4 + 110, R + BW + 40, 835, R + BW + 10, 835], fill=DIM, width=3)
    d.polygon([(R + BW + 10, 835), (R + BW + 26, 824), (R + BW + 26, 846)], fill=DIM)
    d.text((R + 20, y4 + 236), "Andy's corrections loop back up to the plan (arrow on the right)", font=font(22), fill=DIM)
    img.save(path)


def layers(path):
    W, H = 1800, 1250
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((60, 40), "Layer order (bottom to top)", font=font(52, True), fill=INK)
    d.text((60, 110), "Like clear sheets stacked on a light table: each sheet covers the ones under it.",
           font=font(26), fill=DIM)
    stack = [
        ("L5  captions", (235, 235, 235), (20, 20, 20), "Always on top. libass from the kept words.",
         "Own ads: sentence case, caps on 1-2 key words.  Client ads: plain white."),
        ("L4  front graphics", (30, 110, 220), (255, 255, 255), "Cards, lower thirds, logo, CTA, end card.",
         "PNG from cards/. Never covers captions."),
        ("L3  speaker matte", (40, 150, 90), (255, 255, 255), "The speaker cut out of the SAME footage.",
         "Only exists while a behind-head title is on screen."),
        ("L2  behind-head title", (230, 170, 0), (20, 20, 20), "Big headline drawn BEFORE the speaker,",
         "so the head covers part of it. Needs a face in frame."),
        ("L1  background", (90, 96, 110), (255, 255, 255), "The footage itself (cut + reframed), or b-roll,",
         "a brand color, or a blurred fill for non-9:16 clips."),
    ]
    x0, y0, sw, shh, step = 120, 200, 620, 150, 190
    for k, (name, fill, txt, l1, l2) in enumerate(stack):
        y = y0 + k * step
        off = (len(stack) - 1 - k) * 22
        d.polygon([(x0 + off, y + 40), (x0 + sw + off, y + 40), (x0 + sw - 60 + off, y + shh),
                   (x0 - 60 + off, y + shh)], fill=fill)
        d.text((x0 + 40 + off, y + 72), name, font=font(34, True), fill=txt)
        d.text((x0 + sw + 120, y + 58), l1, font=font(28), fill=INK)
        d.text((x0 + sw + 120, y + 98), l2, font=font(24), fill=DIM)
    d.text((60, H - 70), "ffmpeg: [footage]+[title] -> +[footage x matte] -> +[graphics] -> ass(captions)",
           font=font(24), fill=DIM)
    img.save(path)


if __name__ == "__main__":
    os.makedirs(os.path.join(ROOT, "plans"), exist_ok=True)
    pipeline(os.path.join(ROOT, "plans", "pipeline.png"))
    layers(os.path.join(ROOT, "plans", "layers.png"))
    print("wrote plans/pipeline.png, plans/layers.png")
