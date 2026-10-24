"""Client cards (Phase 10): one JSON per client, clients/<id>/card.json (schema: clients/card.schema.json).

  python3 engines/client_card.py check clients/<id>/card.json     validate + readiness codes
  python3 engines/client_card.py new <id>                          copy the template

A plan with ad_type "client" and client "<id>" renders with that client's colours, logo, fonts
and end card; captions are plain white (Andy's rule for client ads). Everything else (beats,
QA, policy, rule 4) is the same as our own ads.

Card codes (BLOCK = cannot render; HOLD = renders as a draft only, until fixed or accepted):
  C01 BLOCK  card does not match the schema
  C02 BLOCK  a colour is not a #RRGGBB hex code
  C03 BLOCK  logo missing or unreadable
  C04 HOLD   logo too small for the end card (< 600 px wide)
  C05 HOLD   white text on the primary colour is below 4.5:1 and the dark text colour does not fix it
  C06 HOLD   card status is not "approved" (the client has not signed it off)
  C07 HOLD   a claim has no proof or no approver
  C08 HOLD   guarantee without a terms page (Meta reviews the page with the ad)
  C09 HOLD   policy scan finds a problem in the offer, CTA, guarantee or claims (spec/QA-policy.md)
  C10 HOLD   unfilled [BRACKET] placeholders left in the card
"""
import copy
import json
import os
import re
import shutil
import sys

import jsonschema
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENTS = os.path.join(ROOT, "clients")
with open(os.path.join(CLIENTS, "card.schema.json")) as f:
    SCHEMA = json.load(f)
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
MIN_LOGO_W = 600


def load(card_id_or_path):
    p = card_id_or_path if card_id_or_path.endswith(".json") else os.path.join(CLIENTS, card_id_or_path, "card.json")
    with open(p) as f:
        return json.load(f), os.path.dirname(os.path.abspath(p))


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def contrast(a, b):
    import depth  # noqa: E402  (pulls in cv2; only needed here)
    return depth.contrast(a, [[b]])


def text_on_primary(card):
    """White if it reads on the primary colour (>= 4.5:1), else the card's dark text colour if
    that reads, else white (and C05 holds the card). -> (hex, ratio)"""
    c = card["brand"]["colors"]
    prim = c.get("primary")
    if not prim or not HEX.match(prim):
        return "#FFFFFF", None
    w = contrast(rgb("#FFFFFF"), rgb(prim))
    if w >= 4.5:
        return "#FFFFFF", w
    dark = c.get("text_dark")
    if dark and HEX.match(dark):
        d = contrast(rgb(dark), rgb(prim))
        if d >= 4.5:
            return dark, d
    return "#FFFFFF", w


def check(card, card_dir):
    """-> [(code, severity, message)]"""
    out = []
    errs = [f"{'/'.join(map(str, e.path)) or '(card)'}: {e.message}"
            for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(card)]
    if errs:
        out += [("C01", "BLOCK", e) for e in errs]
        return out
    for k, v in card["brand"]["colors"].items():
        if v is not None and not HEX.match(v):
            out.append(("C02", "BLOCK", f"colour {k} = {v!r} is not #RRGGBB"))
    if card["brand"]["colors"]["primary"] is None:
        out.append(("C02", "BLOCK", "no primary colour (slab, buttons and ticks use it)"))
    lp = card["brand"]["logo"]["path"]
    if not lp:
        out.append(("C03", "BLOCK", "no logo (the last 2-3 s are the logo card)"))
    else:
        p = os.path.join(card_dir, lp)
        try:
            with Image.open(p) as im:
                if im.width < MIN_LOGO_W:
                    out.append(("C04", "HOLD", f"logo is {im.width} px wide; the end card wants >= {MIN_LOGO_W}"))
        except (OSError, ValueError) as e:
            out.append(("C03", "BLOCK", f"logo {lp}: {e}"))
    if not any(c[0] == "C02" for c in out):
        col, ratio = text_on_primary(card)
        if ratio is not None and ratio < 4.5:
            out.append(("C05", "HOLD", f"white on {card['brand']['colors']['primary']} is {ratio}:1 (need 4.5) and "
                                       "text_dark does not fix it: pick a darker primary or set text_dark"))
    if card["status"] != "approved":
        out.append(("C06", "HOLD", "card status is draft: the client has not signed it off"))
    for c in card["approved_claims"]:
        if not c["proof"].strip() or not c["approved_by"].strip():
            out.append(("C07", "HOLD", f"claim {c['claim']!r} has no proof or approver"))
    g = card.get("guarantee")
    if g and g.get("text") and not g.get("terms_url"):
        out.append(("C08", "HOLD", "guarantee has no terms page (terms_url)"))
    import policy_scan  # noqa: E402
    texts = [("offer", card["offer"].get("headline", "")), ("offer", card["offer"].get("details", "")),
             ("cta", card["cta"].get("text", "")), ("cta", card["cta"].get("button", ""))]
    texts += [("guarantee", g["text"])] if g and g.get("text") else []
    texts += [("claim", c["claim"]) for c in card["approved_claims"]]
    for where, t in texts:
        for f in policy_scan.scan_text(t, "ad_copy"):
            if f["severity"] in ("BLOCK", "HOLD"):
                out.append(("C09", "HOLD", f"{where} {t!r}: {f['rule']} ({f['severity']} in QA-policy): {f['why']}"))
    blanks = sorted(set(re.findall(r"\[[A-Z][A-Z' ]+\]", json.dumps({k: v for k, v in card.items() if k != "_note"}))))
    if blanks:
        out.append(("C10", "HOLD", f"unfilled placeholders: {', '.join(blanks)}"))
    return out


def brand_for(plan, base):
    """The brand a plan renders with: brand.json for our own ads, the client card over it for
    client ads. Returns (brand dict, card codes or [])."""
    if plan.get("ad_type") != "client":
        return base, []
    card, card_dir = load(plan["client"])
    codes = check(card, card_dir)
    b = copy.deepcopy(base)
    cb = card["brand"]
    for k, v in cb["colors"].items():
        if v:
            b["colors"][k] = v
    b["colors"]["text_on_primary"] = text_on_primary(card)[0]
    b["logo"] = {"path": os.path.relpath(os.path.join(card_dir, cb["logo"]["path"]), ROOT) if cb["logo"]["path"] else None,
                 "background": cb["logo"].get("background") or "#FFFFFF",
                 "end_card_s": cb["logo"].get("end_card_s", base["logo"].get("end_card_s", 2.5))}
    for k in ("headline", "caption"):
        if (cb.get("fonts") or {}).get(k):
            b["fonts"][k] = cb["fonts"][k]
    b["client"] = {"id": card["id"], "name": card["name"], "cta": card["cta"], "offer": card["offer"]}
    return b, codes


def main(a):
    if len(a) == 2 and a[0] == "check":
        card, d = load(a[1])
        codes = check(card, d)
        print(f"{card.get('name')} ({card.get('id')}): "
              f"{'BLOCK' if any(c[1] == 'BLOCK' for c in codes) else 'HOLD' if codes else 'READY'}")
        for c in codes:
            print(f"  {c[0]} {c[1]:5s} {c[2]}")
        if not any(c[0] in ("C01", "C02") for c in codes):
            col, ratio = text_on_primary(card)
            print(f"  text on the primary colour: {col} at {ratio}:1")
        sys.exit(1 if any(c[1] == "BLOCK" for c in codes) else 2 if codes else 0)
    if len(a) == 2 and a[0] == "new":
        dst = os.path.join(CLIENTS, a[1])
        if os.path.exists(dst):
            sys.exit(f"{dst} already exists")
        shutil.copytree(os.path.join(CLIENTS, "_template"), dst)
        p = os.path.join(dst, "card.json")
        with open(p) as f:
            c = json.load(f)
        c["id"] = a[1]
        with open(p, "w") as f:
            json.dump(c, f, indent=2)
        print("wrote", os.path.relpath(p, ROOT))
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
