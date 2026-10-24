"""Fonts: the repo ships open-licence WOFF2 files (fonts/OFL.txt). They are unpacked to TTF once, each under its own
family name, so libass and Pillow always pick the exact weight."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fonts"

# role -> (woff2 file, family name). The reference cuts use Montserrat (SOP-11c Fonts); Archivo Black is the caps stand-in
# the README names; Poppins ExtraBold is the breakout headline face (SOP-11d §5).
FACES = {
    "head": ("archivo-latin-900-normal.woff2", "AE Head"),
    "bold": ("archivo-latin-700-normal.woff2", "AE Bold"),
    "thin": ("source-sans-3-latin-600-normal.woff2", "AE Thin"),
    "script": ("dancing-script-latin-700-normal.woff2", "AE Script"),
    "mont800": ("montserrat-latin-800-normal.woff2", "AE Mont800"),
    "mont700": ("montserrat-latin-700-normal.woff2", "AE Mont700"),
    "mont600": ("montserrat-latin-600-normal.woff2", "AE Mont600"),
    "mont800i": ("montserrat-latin-800-italic.woff2", "AE Mont800 Italic"),
    "poppins": ("poppins-latin-800-normal.woff2", "AE Poppins"),
}


def ensure(work):
    """Unpack every face into work/fonts; returns {role: (ttf path, family)}."""
    from fontTools.ttLib import TTFont
    out = Path(work) / "fonts"
    out.mkdir(parents=True, exist_ok=True)
    faces = {}
    for role, (src, fam) in FACES.items():
        dst = out / (fam.replace(" ", "") + ".ttf")
        if not dst.exists():
            f = TTFont(ROOT / src)
            f.flavor = None
            for n in f["name"].names:
                if n.nameID in (1, 4, 16):
                    n.string = fam
                elif n.nameID in (2, 17):
                    n.string = "Regular"
                elif n.nameID == 6:
                    n.string = fam.replace(" ", "")
            f.save(dst)
        faces[role] = (str(dst), fam)
    return faces


_CACHE = {}


def _font(path):
    """Pillow font at 100 px em, plus libass's scale: an ASS font size is the font's full Windows height
    (usWinAscent + usWinDescent), not its em, so Archivo renders at ~0.66 em per size unit (measured 10/24)."""
    if path not in _CACHE:
        from PIL import ImageFont
        from fontTools.ttLib import TTFont
        t = TTFont(path)
        os2 = t["OS/2"]
        k = t["head"].unitsPerEm / max(1, os2.usWinAscent + os2.usWinDescent)
        _CACHE[path] = (ImageFont.truetype(path, 100), k)
    return _CACHE[path]


def width(faces, role, text, size):
    """Width in px that libass draws text at an ASS font size."""
    font, k = _font(faces[role][0])
    return font.getlength(text) * size * k / 100


def fit(faces, role, text, size, maxw):
    """The largest size up to `size` at which text fits in maxw px."""
    w = width(faces, role, text, 100)
    return int(min(size, maxw * 100 / max(w, 1)))
