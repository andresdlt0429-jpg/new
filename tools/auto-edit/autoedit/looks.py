"""The three looks, drawn as ASS (libass) events.

agency   README default look: combo (WHITE CAPS + gold script), stack (white over glowing yellow), number (huge glowing
         figure + small line), thin lowercase captions built word by word, TAP / GET OFFER, logo + CTA end card.
client   SOP-11b (10/19 final, overrides rules 1, 2, 5): heavy white caps + gold italic script extruded in 3D with warm
         gold depth, nothing behind the text; big gold figure only when the price is said; bare 3D offer; agency
         name tags; small clean white sentence-case captions; brand-colour callout behind the head; TAP / GET OFFER.
breakout SOP-11d: kicker pill over red headline box, stat CARD, tick CHECK list, name TAG; ECL red/white/black slabs
         that swing in from the left; white captions with the spoken word on a red pill.
Every element is extruded type (stacked offset copies from a front depth colour to a back one) with a soft shadow.
"""
import re

from . import config, fonts


def ts(x):
    x = max(0.0, x)
    return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{x % 60:05.2f}"


def col(hexrgb, alpha=0):
    h = hexrgb.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}&"


def lerp(c1, c2, k):
    f = lambda c: [int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * k):02X}" for x, y in zip(f(c1), f(c2)))


def esc(text):
    return text.replace("{", "(").replace("}", ")").replace("\n", r"\N")


def rrect(w, h, r):
    """An ASS vector rounded rectangle path, origin top-left."""
    r = min(r, w / 2, h / 2)
    k = r * 0.45
    return (f"m {r} 0 l {w - r} 0 b {w - k} 0 {w} {k} {w} {r} l {w} {h - r} b {w} {h - k} {w - k} {h} {w - r} {h} "
            f"l {r} {h} b {k} {h} 0 {h - k} 0 {h - r} l 0 {r} b 0 {k} {k} 0 {r} 0")


def c_org(c, x):
    """One pivot per lane, left of the frame, so every slab and its text turn together."""
    return -0.2 * c.W


class Canvas:
    """Collects ASS events in three stacks: behind the speaker, in front, and captions."""

    def __init__(self, size, faces, glow=0.0):
        self.W, self.H = size
        self.faces = faces
        self.glow = glow
        self.behind, self.front, self.caps = [], [], []

    def ev(self, stack, layer, start, end, tags, text):
        stack.append(f"Dialogue: {layer},{ts(start)},{ts(end)},Free,,0,0,0,,{{{tags}}}{text}")

    # --- extruded type ------------------------------------------------------------------------------------------
    def type3d(self, start, end, x, y, text, role, size, face, depth=config.GOLD_DEPTH, steps=9, ext=1.0, glow=None,
               anim="rise", stack=None, fade_out=True, align=5, extra="", layer=10):
        stack = self.front if stack is None else stack
        fam = self.faces[role][1]
        glow = self.glow if glow is None else glow
        fo = 160 if fade_out else 0

        def one(layer, dx, dy, tags):
            if anim == "rise":
                mv = rf"\move({x + dx:.1f},{y + dy + 46:.1f},{x + dx:.1f},{y + dy:.1f},0,280)\fscx90\fscy90\t(0,280,\fscx100\fscy100)"
            elif anim == "swing":   # swings in from the left, small overshoot, rests turned 8 deg (SOP-11d §7)
                mv = rf"\pos({x + dx:.1f},{y + dy:.1f})\org({c_org(self, x):.0f},{y:.0f})\fry-75\t(0,420,\fry12)\t(420,560,\fry8)"
            elif anim == "rest":    # already landed: same pivot and angle as a swung slab
                mv = rf"\pos({x + dx:.1f},{y + dy:.1f})\org({c_org(self, x):.0f},{y:.0f})\fry8"
            elif anim == "flip":
                mv = rf"\pos({x + dx:.1f},{y + dy:.1f})\frx80\t(0,300,\frx0)"
            else:
                mv = rf"\pos({x + dx:.1f},{y + dy:.1f})"
            self.ev(stack, layer, start, end, rf"\an{align}\fn{fam}\fs{size}{mv}\fad(140,{fo})\bord0\shad0{extra}{tags}",
                    esc(text))

        one(layer, 10 * ext, 18 * ext, r"\1c&H000000&\1a&H70&\blur12")
        if glow:
            a1 = int(255 * (1 - glow))
            one(layer, 0, 0, rf"\1c{col(face)}\1a&H{a1:02X}&\bord10\3c{col(face)}\3a&H{min(255, a1 + 60):02X}&\blur18")
        for k in range(steps, 0, -1):
            one(layer + 1, 0.6 * k * ext, 1.25 * k * ext, rf"\1c{col(lerp(depth[0], depth[1], k / steps))}")
        one(layer + 2, 0, 0, rf"\1c{col(face)}\3c{col(lerp(face, '#FFFFFF', 0.5))}\bord0.8\blur0.6")

    def slab(self, start, end, x, y, w, h, fill, depth=config.RED_DEPTH, r=14, anim="swing", stack=None, gradient=None,
             edge=None, alpha=0):
        """A 3D slab: a rounded box with 12 px of depth (SOP-11d §7), optional top-lit gradient and hairline edge."""
        stack = self.front if stack is None else stack
        path = rrect(w, h, r)

        def one(layer, dx, dy, tags, p=path):
            if anim == "swing":
                mv = (rf"\pos({x + dx:.1f},{y + dy:.1f})\org({c_org(self, x):.0f},{y + h / 2:.0f})\fry-75\t(0,420,\fry12)"
                      rf"\t(420,560,\fry8)")
            elif anim == "rest":
                mv = rf"\pos({x + dx:.1f},{y + dy:.1f})\org({c_org(self, x):.0f},{y + h / 2:.0f})\fry8"
            else:
                mv = rf"\pos({x + dx:.1f},{y + dy:.1f})"
            self.ev(stack, layer, start, end, rf"\an7{mv}\fad(140,160)\bord0\shad0\p1{tags}", p)

        one(20, 8, 14, r"\1c&H000000&\1a&H80&\blur10")
        for k in range(12, 0, -2):
            one(21, 0.5 * k, k, rf"\1c{col(lerp(depth[0], depth[1], k / 12))}")
        one(22, 0, 0, rf"\1c{col(fill)}\1a&H{alpha:02X}&")
        if gradient:   # top-lit: a lighter inset top band that turns with the slab (a screen clip would not)
            one(23, 3, 3, rf"\1c{col(gradient)}\1a&H30&\blur3", rrect(w - 6, h * 0.5, max(2, r - 3)))
        if edge:
            one(24, 0, 0, rf"\1c{col(edge)}\1a&HE6&\bord1\3c{col(edge)}\3a&HE0&")

    def glint(self, start, x, y, w, h, stack=None):
        """One glint across a face after it lands (README Title3D; SOP-11d §7)."""
        stack = self.front if stack is None else stack
        band = f"m 0 0 l 40 0 l {40 + h * 0.5:.0f} {h:.0f} l {h * 0.5:.0f} {h:.0f}"
        self.ev(stack, 30, start + 0.6, start + 1.2,
                rf"\an7\move({x - 60:.0f},{y:.0f},{x + w:.0f},{y:.0f})\clip({x:.0f},{y:.0f},{x + w:.0f},{y + h:.0f})"
                r"\1c&HFFFFFF&\1a&HA0&\blur6\bord0\shad0\p1", band)


# --- agency ---------------------------------------------------------------------------------------------------------
def agency_combo(c, start, end, y, lines, k=1.0):
    caps, script = lines[0], lines[1] if len(lines) > 1 else ""
    s1 = int(fonts.fit(c.faces, "head", caps, 150, 0.9 * c.W) * k)
    c.type3d(start, end, c.W / 2, y, caps, "head", s1, config.WHITE, ext=s1 / 118, glow=0)
    if script:
        s2 = int(fonts.fit(c.faces, "script", script, 112, 0.84 * c.W) * k)
        c.type3d(start + 0.12, end, c.W / 2, y + s1 * 0.82, script, "script", s2, config.GOLD,
                 depth=("#6A430C", "#241604"), steps=5, ext=0.7, glow=c.glow * 0.7)
        return s1 * 0.5 + s1 * 0.82 + s2 * 0.6
    return s1


def agency_stack(c, start, end, y, lines, k=1.0):
    top, bottom = lines[0], lines[1] if len(lines) > 1 else ""
    s1 = int(fonts.fit(c.faces, "head", top, 220, 0.86 * c.W) * k)
    c.type3d(start, end, c.W / 2, y, top, "head", s1, config.WHITE, ext=s1 / 118, glow=0)
    if bottom:
        s2 = int(fonts.fit(c.faces, "head", bottom, 240, 0.9 * c.W) * k)
        c.type3d(start + 0.1, end, c.W / 2, y + s1 * 0.5 + s2 * 0.55, bottom, "head", s2, config.YELLOW, ext=s2 / 110,
                 steps=11)
        return s1 * 0.42 + s2 * 1.0
    return s1


def agency_number(c, start, end, y, lines, k=1.0):
    s1 = int(fonts.fit(c.faces, "head", lines[0], 300, 0.82 * c.W) * k)
    c.type3d(start, end, c.W / 2, y, lines[0], "head", s1, config.YELLOW, ext=1.4, steps=11)
    if len(lines) > 1:
        s2 = fonts.fit(c.faces, "bold", lines[1], 64, 0.8 * c.W)
        c.type3d(start + 0.1, end, c.W / 2, y + s1 * 0.66, lines[1], "bold", s2, config.WHITE,
                 depth=("#5A5F66", "#1A1C20"), steps=4, ext=0.5, glow=0)
    return s1 * 1.25


def cta(c, start, end, y, lines, second=config.YELLOW, k=1.0):
    s1 = int(fonts.fit(c.faces, "head", lines[0], 150, 0.5 * c.W) * k)
    c.type3d(start, end, c.W / 2, y, lines[0], "head", s1, config.WHITE, ext=1.0, glow=0, fade_out=False)
    if len(lines) > 1:
        s2 = int(fonts.fit(c.faces, "head", lines[1], 220, 0.82 * c.W) * k)
        c.type3d(start + 0.14, end, c.W / 2, y + s1 * 1.15, lines[1], "head", s2, second, ext=1.1, steps=10,
                 fade_out=False)
    return s1 * 2.4


def end_card(c, start, end, brand):
    mark = brand.get("wordmark") or brand.get("name", "").upper()
    y = 0.33 * c.H
    if mark and not brand.get("logo"):
        s = fonts.fit(c.faces, "head", mark, 160, 0.86 * c.W)
        c.type3d(start, end, c.W / 2, y, mark, "head", s, config.WHITE, ext=0.9, glow=0, fade_out=False)
        y += s * 0.85
    else:
        y += 0.06 * c.H   # the logo image itself is overlaid by the renderer above this line
    if brand.get("tagline"):
        s2 = fonts.fit(c.faces, "script", brand["tagline"], 76, 0.84 * c.W)
        c.type3d(start + 0.1, end, c.W / 2, y, brand["tagline"], "script", s2, config.GOLD, depth=("#6A430C", "#241604"),
                 steps=4, ext=0.5, fade_out=False)


# --- client (SOP-11b) -------------------------------------------------------------------------------------------------
def client_callout(c, start, end, y, lines, color):
    """Rule 1: ONE line of heavy caps ~90% of the frame width, behind his head, brand colour, darker depth."""
    s = fonts.fit(c.faces, "head", lines[0], 520, 0.9 * c.W)
    c.type3d(start, end, c.W / 2, y, lines[0], "head", s, color, depth=(lerp(color, "#000000", 0.45), lerp(color, "#000000", 0.85)),
             ext=s / 120, glow=0, stack=c.behind)
    return s


def client_big(c, start, end, y, lines):
    """Rule 2: one of his key phrases as one big white 3D line behind his head."""
    s = fonts.fit(c.faces, "head", lines[0], 460, 0.9 * c.W)
    c.type3d(start, end, c.W / 2, y, lines[0], "head", s, config.WHITE, ext=s / 120, glow=0, stack=c.behind)
    return s


def client_number(c, start, end, y, lines):
    """Rule 4 / 10-19: a big gold 3D figure with a small white line in his words, in front at chest height."""
    s1 = fonts.fit(c.faces, "head", lines[0], 280, 0.8 * c.W)
    c.type3d(start, end, c.W / 2, y, lines[0], "head", s1, config.GOLD, ext=1.4, steps=11, glow=0)
    if len(lines) > 1:
        c.type3d(start + 0.1, end, c.W / 2, y + s1 * 0.66, lines[1], "bold", fonts.fit(c.faces, "bold", lines[1], 58, 0.8 * c.W),
                 config.WHITE, depth=("#5A5F66", "#1A1C20"), steps=4, ext=0.5, glow=0)
    return s1 * 1.25


def client_tag(c, start, end, y, lines, color, x=None):
    """Rule 6/7: agency name tag: white caps title over a dark brand-colour slab with a thin white bar on the left;
    swings in from the left and is gone before the next cut."""
    title = lines[0]
    sub = lines[1] if len(lines) > 1 else ""
    s1 = fonts.fit(c.faces, "head", title, 74, 0.7 * c.W)
    s2 = 34
    w = max(fonts.width(c.faces, "head", title, s1), fonts.width(c.faces, "mont700", sub, s2)) + 80
    h = s1 * 1.15 + (s2 * 1.5 if sub else 0) + 24
    x = 0.08 * c.W if x is None else x
    top = y - h / 2
    dark = lerp(color, "#000000", 0.7)
    c.slab(start, end, x, top, w, h, dark, depth=(lerp(dark, "#000000", 0.5), "#000000"), r=8)
    c.slab(start, end, x, top, 10, h, config.WHITE, depth=("#9AA3AD", "#2A3038"), r=3)
    c.type3d(start, end, x + 40, top + 12 + s1 * 0.55, title, "head", s1, config.WHITE, steps=4, ext=0.5, glow=0,
             anim="swing", align=4, layer=50, depth=("#222222", "#000000"))
    if sub:
        c.type3d(start, end, x + 42, top + 12 + s1 * 1.15 + s2 * 0.6, sub.upper(), "mont700", s2, config.MUTED, steps=2,
                 ext=0.3, glow=0, anim="swing", align=4, layer=50, depth=("#222222", "#000000"), extra=r"\fsp3")
    return h


def client_offer(c, windows, y, lines):
    """Rule 5 (10/19): bare 3D text, no card: the price gold (only if spoken), the package pearl, the bonus under it."""
    for (start, end) in windows:
        yy = y
        for k, line in enumerate(lines):
            role = "head"
            size = fonts.fit(c.faces, role, line, 92 if k == 0 else 56, 0.86 * c.W)
            face = config.PEARL if k else config.WHITE
            if k == 0 and re.match(r"\$\d", line):
                price, _, rest = line.partition(" ")
                pw = fonts.width(c.faces, role, price + " ", size)
                tw = fonts.width(c.faces, role, line, size)
                c.type3d(start, end, c.W / 2 - tw / 2 + pw / 2, yy, price, role, size, config.GOLD, ext=size / 110, glow=0,
                         anim="flip")
                c.type3d(start, end, c.W / 2 + pw / 2, yy, rest, role, size, face, ext=size / 110, glow=0, anim="flip")
            else:
                c.type3d(start + 0.08 * k, end, c.W / 2, yy, line, role, size, face, ext=size / 110, glow=0, anim="flip",
                         depth=("#6E6A62", "#1E1C1A"))
            yy += size * 1.05


# --- breakout (SOP-11d) -----------------------------------------------------------------------------------------------
def lane_x(c):
    return 0.065 * c.W   # SOP-11d §4 says 0.045 W; the 8 deg turn pulls the left edge in, so start a little further right


def bo_headline(c, start, end, y, lines):
    """H: tracked caps kicker on a black pill over a red headline box (2-5 words)."""
    kicker, head = (lines[0], lines[1]) if len(lines) > 1 else ("", lines[0])
    x = lane_x(c)
    maxw = 0.39 * c.W
    hs = fonts.fit(c.faces, "poppins", head, 88, maxw - 60)
    hw = fonts.width(c.faces, "poppins", head, hs) + 60
    hh = hs * 1.45
    top = y - hh / 2
    if kicker:
        ks = 36
        kw = fonts.width(c.faces, "mont800", kicker.upper(), ks) + len(kicker) * 3 + 40
        c.slab(start, end, x, top - ks * 1.7, kw, ks * 1.5, config.CARD_BLACK, depth=("#1A1A1C", "#000000"), r=ks * 0.75,
               edge=config.WHITE)
        c.type3d(start, end, x + 20, top - ks * 0.95, kicker.upper(), "mont800", ks, config.WHITE, steps=2, ext=0.3, glow=0,
                 anim="swing", align=4, layer=50, depth=("#222222", "#000000"), extra=r"\fsp3")
    c.slab(start, end, x, top, hw, hh, config.RED_BOTTOM, gradient=config.RED_TOP, edge=config.WHITE)
    c.type3d(start, end, x + 30, top + hh / 2, head, "poppins", hs, config.WHITE, steps=3, ext=0.4, glow=0, anim="swing",
             align=4, layer=50, depth=("#5A0008", "#200003"))


def bo_card(c, start, end, y, lines):
    """CARD: small label, the figure in a red box, one line under it. Only figures he says (README / SOP-11d §3)."""
    label, fig, under = (lines + ["", "", ""])[:3]
    x, w = lane_x(c), 0.36 * c.W
    fs = fonts.fit(c.faces, "poppins", fig, 127, w - 80)
    h = 44 + 40 + fs * 1.4 + 70
    top = y - h / 2
    c.slab(start, end, x, top, w, h, config.CARD_BLACK, depth=("#1A1A1C", "#000000"), edge=config.WHITE, alpha=0x14)
    c.type3d(start, end, x + 30, top + 44, label, "mont600", 36, config.MUTED, steps=2, ext=0.3, glow=0, anim="swing", align=4, layer=50,
             depth=("#222222", "#000000"))
    bw = fonts.width(c.faces, "poppins", fig, fs) + 50
    c.slab(start, end, x + 30, top + 74, bw, fs * 1.3, config.RED_BOTTOM, gradient=config.RED_TOP, r=10)
    c.type3d(start, end, x + 55, top + 74 + fs * 0.65, fig, "poppins", fs, config.WHITE, steps=3, ext=0.5, glow=0,
             anim="swing", align=4, layer=50, depth=("#5A0008", "#200003"))
    if under:
        c.type3d(start, end, x + 30, top + h - 40, under, "mont600", 36, config.WHITE, steps=2, ext=0.3, glow=0, anim="swing",
                 align=4, layer=50, depth=("#222222", "#000000"))


def bo_check(c, start, end, y, title, items, mode="check"):
    """CHECK: a panel whose items tick on one by one as he says them; mode x = red crosses (SOP-11d §3)."""
    x, w = lane_x(c), 0.37 * c.W
    rowh = 66
    h = 90 + rowh * len(items) + 20
    top = y - h / 2
    c.slab(start, end, x, top, w, h, config.CARD_BLACK, depth=("#1A1A1C", "#000000"), edge=config.WHITE, alpha=0x14)
    c.type3d(start, end, x + 30, top + 48, title.upper(), "mont800", 34, config.WHITE, steps=2, ext=0.3, glow=0,
             anim="swing", align=4, layer=50, depth=("#222222", "#000000"), extra=r"\fsp3")
    for k, it in enumerate(items):
        cy = top + 90 + rowh * k + rowh / 2
        at = max(start, it.get("t", start))
        c.slab(start, end, x + 30, cy - 21, 42, 42, "#2A2A2E", depth=("#111111", "#000000"), r=8)
        c.slab(at, end, x + 30, cy - 21, 42, 42, config.RED_BOTTOM, gradient=config.RED_TOP, r=8, anim="rest")
        mark = "m 8 22 l 17 31 l 34 11 l 30 7 l 17 23 l 12 18" if mode != "x" else \
            "m 9 13 l 13 9 l 21 17 l 29 9 l 33 13 l 25 21 l 33 29 l 29 33 l 21 25 l 13 33 l 9 29 l 17 21"
        c.ev(c.front, 60, at, end, rf"\an7\pos({x + 30:.0f},{cy - 21:.0f})\org({c_org(c, x):.0f},{cy:.0f})\fry8"
             r"\1c&HFFFFFF&\bord0\shad0\fad(180,160)\p1", mark)
        c.type3d(start, end, x + 92, cy, it["text"], "mont700", 44, config.WHITE, steps=2, ext=0.3, glow=0, anim="swing",
                 align=4, layer=50, depth=("#222222", "#000000"))


def bo_tag(c, start, end, lines):
    """TAG: name tag top left, once per video (SOP-11d §3, §4 top lane)."""
    name, role = (lines + [""])[:2]
    x, y = 0.055 * c.W, 0.10 * c.H
    ns = 52
    w = max(fonts.width(c.faces, "poppins", name, ns), fonts.width(c.faces, "mont600", role, 32)) + 60
    h = ns * 1.25 + 52
    c.slab(start, end, x, y - 20, w, h, config.CARD_BLACK, depth=("#1A1A1C", "#000000"), edge=config.WHITE, alpha=0x14)
    c.slab(start, end, x, y - 20, 10, h, config.RED_BOTTOM, r=3)
    c.type3d(start, end, x + 30, y + ns * 0.45, name, "poppins", ns, config.WHITE, steps=2, ext=0.3, glow=0, anim="swing",
             align=4, layer=50, depth=("#222222", "#000000"))
    c.type3d(start, end, x + 30, y + ns * 1.25, role, "mont600", 32, config.MUTED, steps=1, ext=0.2, glow=0, anim="swing",
             align=4, layer=50, depth=("#222222", "#000000"))


# --- captions ---------------------------------------------------------------------------------------------------------
def chunks(words, max_words, max_chars):
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or re.search(r"[,.?!]$", w[2]) or sum(len(x[2]) + 1 for x in cur) > max_chars:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def captions(c, words, style, y_at, hidden=lambda a, b: False, end_at=None):
    """thin   README agency: thin lowercase line, built word by word.
       clean  SOP-11b 10/19: small clean white sentence case, one short line, soft shadow; rise; spoken word ~10% bigger.
       pill   SOP-11d §6: bold white Montserrat 800 ~52 px, one line up to 5 words/30 chars, spoken word on a red pill."""
    cfg = dict(thin=(4, 20), clean=(4, 24), pill=(5, 30))[style]
    groups = chunks(words, *cfg)
    for i, g in enumerate(groups):
        g0 = g[0][0]
        g1 = min(groups[i + 1][0][0] if i + 1 < len(groups) else (end_at or g[-1][1] + 0.5), g[-1][1] + 0.45)
        if hidden(g0, g1):
            continue
        y = y_at(g0)
        if style == "thin":
            toks = [esc(re.sub(r"[,.?!]$", "", w[2]).lower()) for w in g]
        else:
            toks = [esc(re.sub(r"[,]$", "", w[2])) for w in g]
        for j, w in enumerate(g):
            s0 = g0 if j == 0 else w[0]
            s1 = g[j + 1][0] if j + 1 < len(g) else g1
            if s1 <= s0:
                continue
            if style == "thin":
                body = " ".join(t if k <= j else r"{\alpha&HFF&}" + t + r"{\alpha&H00&}" for k, t in enumerate(toks))
                pos = (r"\move(%d,%d,%d,%d,0,160)\fad(100,0)" % (c.W / 2, y + 18, c.W / 2, y)) if j == 0 else r"\pos(%d,%d)" % (c.W / 2, y)
                c.caps.append(f"Dialogue: 5,{ts(s0)},{ts(s1)},CapThin,,0,0,0,,{{\\an5{pos}}}{body}")
            elif style == "clean":
                body = " ".join((r"{\fscx110\fscy110}" + t + r"{\fscx100\fscy100}") if k == j else t for k, t in enumerate(toks))
                pos = (r"\move(%d,%d,%d,%d,0,200)\fad(120,0)" % (c.W / 2, y + 26, c.W / 2, y)) if j == 0 else r"\pos(%d,%d)" % (c.W / 2, y)
                c.caps.append(f"Dialogue: 5,{ts(s0)},{ts(s1)},CapClean,,0,0,0,,{{\\an5{pos}}}{body}")
            else:
                size = 52
                full = " ".join(toks)
                fw = fonts.width(c.faces, "mont800", full, size)
                x0 = c.W / 2 - fw / 2
                pre = fonts.width(c.faces, "mont800", " ".join(toks[:j]) + (" " if j else ""), size)
                ww = fonts.width(c.faces, "mont800", toks[j], size)
                c.caps.append(f"Dialogue: 4,{ts(s0)},{ts(s1)},CapPill,,0,0,0,,{{\\an7\\pos({x0 + pre - 13:.0f},{y - size * 0.62 - 9:.0f})"
                              f"\\p1\\1c{col(config.RED_BOTTOM)}\\bord0\\shad0}}{rrect(ww + 26, size * 1.24 + 18, 12)}")
                c.caps.append(f"Dialogue: 5,{ts(s0)},{ts(s1)},CapPill,,0,0,0,,{{\\an5\\pos({c.W / 2:.0f},{y:.0f})}}{full}")


def header(c, out_path):
    f = c.faces
    styles = [
        f"Style: Free,{f['head'][1]},100,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
        f"Style: CapThin,{f['thin'][1]},70,&H00FFFFFF,&H00FFFFFF,&H60000000,&H90000000,0,0,0,0,100,100,0.5,0,1,2,2.5,5,60,60,0,1",
        f"Style: CapClean,{f['bold'][1]},66,&H00FFFFFF,&H00FFFFFF,&H70000000,&H90000000,0,0,0,0,100,100,0.5,0,1,2.2,2.5,5,60,60,0,1",
        f"Style: CapPill,{f['mont800'][1]},52,&H00FFFFFF,&H00FFFFFF,&H70000000,&H90000000,0,0,0,0,100,100,0,0,1,1.4,2,5,60,60,0,1",
    ]
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {c.W}\nPlayResY: {c.H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
            "YCbCr Matrix: TV.709\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
            "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n" + "\n".join(styles) +
            "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def write(c, path, events):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header(c, path) + "\n".join(events) + "\n")
