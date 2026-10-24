"""Hook-matrix export (Phase 10, SOP 2.5 stand-in): one file per hook x body, named for Ads Manager.

  python3 engines/export_matrix.py plans/<ad>/plan.json

Name: <AdName>_H<hook>_Body<body>_v<version>.mp4, e.g. OfficeAd_H3_BodyA_v2.mp4
  hook     the variant's hook group number (H1 when every variant shares one hook)
  body     the variant's body group id
  version  goes up only when what the cut contains changes: a content hash over its parts (source,
           in/out), its on-screen text and anchors, captions, audio and the brand/SOP numbers. The
           same content re-exported keeps its version.
Gate (engines/qa_gate.py) decides where a cut goes:
  BLOCK -> refused, not exported        HOLD -> out/<ad>/matrix/review/        PASS -> out/<ad>/matrix/ship/
The gate must be newer than the render it judged; otherwise run the gate first.
Writes out/<ad>/matrix/manifest.json (every version ever exported) and manifest.csv (this export).
"""
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy_scan  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def camel(s):
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[^A-Za-z0-9]+", s) if w)


def hook_no(plan, variant):
    hooks = sorted({p["group"].split(":", 1)[1] for p in plan["parts"] if p["group"].startswith("hook:")})
    h = variant.get("hook")
    if not h:
        return 1
    return hooks.index(h) + 1 if h in hooks else int(re.sub(r"\D", "", h) or 1)


def content_hash(plan, variant):
    def wanted(p):
        g = p["group"]
        return g in ("shared", "cta") or g == f"hook:{variant.get('hook')}" or g == f"body:{variant.get('body')}"
    parts = [{k: p.get(k) for k in ("id", "src", "in", "out", "say")} for p in plan["parts"] if wanted(p)]
    lines = {p["id"].split("_")[0] for p in parts}
    layers = [{k: x.get(k) for k in ("id", "text", "items", "at", "until", "tier", "kind", "beat")}
              for x in plan["layers"]["titles"] + plan["layers"]["graphics"] if x["id"].split("-")[1] in lines]
    blob = {"parts": parts, "layers": layers, "captions": plan["layers"].get("captions"), "audio": plan.get("audio"),
            "hero": (plan["layers"].get("hero") or {}).get(variant["id"]), "ad_type": plan.get("ad_type"),
            "client": plan.get("client")}
    files = ["brand.json", "spec/sop-defaults.json"]
    if plan.get("ad_type") == "client":
        files.append(f"clients/{plan['client']}/card.json")
    h = hashlib.sha1(json.dumps(blob, sort_keys=True).encode())
    for f in files:
        with open(os.path.join(ROOT, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


def sha1_file(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(plan_path):
    with open(plan_path) as f:
        plan = json.load(f)
    ad = plan["id"]
    out = os.path.join(ROOT, "out", ad)
    mdir = os.path.join(out, "matrix")
    man_p = os.path.join(mdir, "manifest.json")
    man = json.load(open(man_p)) if os.path.exists(man_p) else {"ad": ad, "exports": []}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = []
    for v in plan["variants"]:
        vid = v["id"]
        key = f"H{hook_no(plan, v)}_Body{v.get('body') or vid}"
        gate_p = os.path.join(out, f"{ad}_{vid}_gate.json")
        qa_p = next((p for p in (os.path.join(out, f"{ad}_{vid}{t}_qa.json") for t in ("", "_DRAFT")) if os.path.exists(p)), None)
        row = {"variant": vid, "key": key, "made_at": stamp}
        if not qa_p:
            rows.append(dict(row, status="refused", why="no render for this variant"))
            continue
        if not os.path.exists(gate_p) or os.path.getmtime(gate_p) < os.path.getmtime(qa_p):
            rows.append(dict(row, status="refused", why="gate missing or older than the render: run engines/qa_gate.py"))
            continue
        gate = json.load(open(gate_p))
        qa = json.load(open(qa_p))
        if gate["verdict"] == "BLOCK":
            why = "; ".join(f"{r['item']}: {str(r['detail'])[:80]}" for r in gate["rows"] if r["status"] == "BLOCK")
            rows.append(dict(row, status="refused", verdict="BLOCK", why=why))
            continue
        h = content_hash(plan, v)
        prior = [e for e in man["exports"] if e["key"] == key]
        same = next((e for e in prior if e["content"] == h), None)
        ver = same["version"] if same else max([e["version"] for e in prior] or [0]) + 1
        name = f"{camel(ad)}_{key}_v{ver}.mp4"
        dst_dir = os.path.join(mdir, "ship" if gate["verdict"] == "PASS" else "review")
        os.makedirs(dst_dir, exist_ok=True)
        for other in ("ship", "review"):  # a cut lives in one place only
            stale = os.path.join(mdir, other, name)
            if os.path.exists(stale) and os.path.dirname(stale) != dst_dir:
                os.remove(stale)
        src = os.path.join(ROOT, qa["delivery_file"]["path"])
        dst = os.path.join(dst_dir, name)
        if os.path.exists(dst):
            os.remove(dst)
        try:
            os.link(src, dst)  # same disk: no second copy of the video
        except OSError:
            shutil.copy2(src, dst)
        spoken, shown = policy_scan.variant_texts(plan, v)
        e = {"key": key, "version": ver, "file": os.path.relpath(dst, ROOT), "variant": vid, "content": h,
             "verdict": gate["verdict"], "draft_render": bool(qa.get("draft")), "duration_s": qa.get("delivery_duration_s")
             or round(qa.get("duration_planned_s", 0) + qa.get("end_card_s", 0), 2),
             "hook_text": spoken[0][1] if spoken else "", "on_screen": [t for _, t in shown],
             "sha1": sha1_file(dst), "made_at": stamp,
             "holds": [r["item"] for r in gate["rows"] if r["status"] == "HOLD"]}
        if same:
            same.update(e)
        else:
            man["exports"].append(e)
        rows.append(dict(row, status="exported", verdict=gate["verdict"], version=ver, file=e["file"],
                         why="new version" if not same else "same content, version kept"))
    os.makedirs(mdir, exist_ok=True)
    with open(man_p, "w") as f:
        json.dump(man, f, indent=1)
    with open(os.path.join(mdir, "manifest.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, ["key", "version", "variant", "verdict", "status", "file", "why", "made_at"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"{r['key']:12s} {r['status']:9s} {r.get('verdict', ''):6s} {r.get('file', '')}  ({r.get('why', '')})")
    return rows


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
