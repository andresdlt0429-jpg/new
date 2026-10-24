"""Phase 10 proof: the same frame and components in our brand and in a client card's brand.

  python3 engines/phase10_client_proof.py [client-id]      default: example-client
Stills -> out/phase10-proof/client_brand_<id>.png, numbers -> out/phase10-proof/report.json
"""
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import client_card  # noqa: E402
import components  # noqa: E402
import depth  # noqa: E402
import phase7_depth as p7  # noqa: E402
import render  # noqa: E402
from graphics_qa import font  # noqa: E402

ROOT = p7.ROOT
OUT = os.path.join(ROOT, "out", "phase10-proof")
W, H = p7.W, p7.H


def frame_for(brand, full, alpha, title):
    components.use_brand(brand)
    render.BRAND = brand
    L = depth.slab_layout(title, W, "L", True)
    yc, occ = depth.place_title(title, W, H, alpha, p7.head_y("light", L["h"]) + L["h"] / 2, "L", True)
    layer, _, _ = depth.slab_frame(title, W, H, yc, 1.0, "L", True)
    img = depth.composite(full, layer, alpha).convert("RGBA")
    btn = components.cta_button({"text": "SEE IF YOU QUALIFY", "tier": "M"}, W, H, 0.5)["frames"][-1]
    img.alpha_composite(btn, ((W - btn.width) // 2, round(H * 0.60)))
    tk = components.ticks({"tier": "S", "items": [{"text": "OFFER", "t": 0}, {"text": "ADS", "t": 0},
                                                   {"text": "LIVE IN 4 DAYS", "t": 0}]}, W, H, 0.4)["frames"][-1]
    img.alpha_composite(tk, (round(W * 0.09), round(H * 0.40)))
    # caption sample: plain white for client ads (Andy's rule), our own style otherwise
    cap = "Exterior cleaners who want more booked jobs"
    d = ImageDraw.Draw(img)
    px = round(H * 0.026)
    while d.textlength(cap, font=font(px, True)) > W * 0.88:
        px -= 2
    f = font(px, True)
    tw = d.textlength(cap, font=f)
    d.text(((W - tw) / 2 + 3, H * 0.82 + 3), cap, font=f, fill=(0, 0, 0, 160))
    d.text(((W - tw) / 2, H * 0.82), cap, font=f, fill=(255, 255, 255, 255))
    card = Image.open(render.end_card(W, H, os.path.join(OUT, f"_card_{brand.get('client', {}).get('id', 'own')}"))["png"])
    c = depth.contrast(components.ON_PRIMARY, np.array([[components.PRIMARY[:3]]], np.uint8))
    return img.convert("RGB"), card, {"title_letters_hidden": occ, "text_on_primary": "#%02X%02X%02X" % components.ON_PRIMARY[:3],
                                      "text_contrast": c, "primary": brand["colors"]["primary"]}


def main(cid):
    os.makedirs(OUT, exist_ok=True)
    (full,), (half,) = p7.load("light")
    _, ref = p7.mattes([half])
    alpha = p7.up(ref[0])
    base = json.load(open(os.path.join(ROOT, "brand.json")))
    title = p7.TITLE
    cells, rep = [], {}
    for tag, plan in (("DLT (own ad)", {"ad_type": "own"}), (f"client: {cid}", {"ad_type": "client", "client": cid})):
        b, codes = client_card.brand_for(plan, base)
        os.makedirs(os.path.join(OUT, f"_card_{b.get('client', {}).get('id', 'own')}"), exist_ok=True)
        img, card, r = frame_for(b, full, alpha, title)
        r["card_codes"] = [list(c) for c in codes]
        rep[tag] = r
        cells.append([p7.label(img, tag, f"slab text {r['text_on_primary']} on {r['primary']}: {r['text_contrast']}:1"),
                      p7.label(card.convert("RGB"), f"{tag}: end card", "last 2.5 s")])
    p = p7.grid([[c[0] for c in cells] + [c[1] for c in cells]],
                f"Phase 10: own brand vs client card ({cid})",
                os.path.join(OUT, f"client_brand_{cid}.png"), scale=0.25)
    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump(rep, f, indent=2)
    print(os.path.relpath(p, ROOT), json.dumps(rep, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "example-client")
