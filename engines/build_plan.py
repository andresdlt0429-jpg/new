"""Phase 5: script.json + alignment.json + cuts.json -> plans/<ad>/plan.json (+ lock checks).

  python3 engines/build_plan.py plans/<ad>/ [--target 35 50]

SAY column   -> parts (the cutter's keeps, in script order)
ON SCREEN    -> L4 graphics, or an L2 behind-head title when the cell says
                "TITLE BEHIND HEAD: ...". "A / B" becomes two graphics in sequence.
                Each graphic is pinned to the spoken word it belongs with: the number
                for a numeric graphic, else the first word it shares with the SAY line.
Lock checks  -> plan.checks (SOP 2.1 placeholder list in spec/sop-defaults.json):
                auto checks evaluated here, manual ones stay "pending" until Andy confirms.
"""
import json
import os
import sys

import jsonschema

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align  # noqa: E402
import numbers_guard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOP = set("a an the and or of to in on for your you youre is are it this that at all just with if so we i "
           "be do get see more my me our us not no".split())


def load(p):
    with open(p) as f:
        return json.load(f)


def kept_words(cuts, srcs):
    out = []
    for k in cuts["keeps"]:
        w = srcs[k["src"]]["words"]
        out += [dict(w[i], ref=f"{k['src']}:{i}", line=k["line"]) for i in range(k["w0"], k["w1"] + 1)]
    return out


def anchor_word(piece, line_words):
    """Pin a graphic to its spoken word: a number first, then the first shared content word."""
    nums = numbers_guard.screen_numbers(piece)
    if nums:
        led = numbers_guard.spoken_ledger(line_words)
        for n in nums:
            for s in led:
                if abs(s["value"] - n["value"]) < 1e-9:
                    return line_words[next(i for i, w in enumerate(line_words)
                                           if w["start"] == s["start"])]["ref"]
    # the place in the line where the most of the graphic's words match in order (last wins on ties)
    pt = [t for w in piece.split() for t in align.pieces(w)]
    lt = [(t, i) for i, w in enumerate(line_words) for t in align.pieces(w["w"])]
    best, best_i = 0, None
    for start in range(len(lt)):
        k = n = 0
        for t, _ in lt[start:start + len(pt) + 2]:
            if k < len(pt) and t == pt[k]:
                n += 1
                k += 1
        if n and n >= best and lt[start][0] == pt[0]:
            best, best_i = n, lt[start][1]
    if best_i is not None:
        return line_words[best_i]["ref"]
    toks = [t for t in pt if t not in STOP]
    for w in line_words:
        if align.norm(w["w"]) in toks:
            return w["ref"]
    return line_words[0]["ref"] if line_words else None


def faces_in(src, t0, t1):
    p = os.path.join(ROOT, src["dir"], "faces.json")
    f = [x for x in load(p) if t0 <= x["t"] <= t1] if os.path.exists(p) else []
    n = max(1, int((t1 - t0) * 2))  # faces sampled at 2 fps
    return f, len(f) / n


def build(ad_dir, target=(35, 50)):
    script = load(os.path.join(ad_dir, "script.json"))
    al = load(os.path.join(ad_dir, "alignment.json"))
    cuts = load(os.path.join(ad_dir, "cuts.json"))
    srcs = {s["key"]: align.load_source(os.path.join(ROOT, s["dir"])) for s in al["sources"]}
    ad = os.path.basename(ad_dir.rstrip("/"))
    words = kept_words(cuts, srcs)
    parts, titles, graphics, emphasis = [], [], [], []
    line_parts = {}
    # alternates (e.g. CTA options) become body:A (the script's own line) / body:<alt id> groups
    alts = [x for x in al["lines"] if x["kind"] == "alt"]
    replaced = {a["replaces"] for a in alts}
    beats = [x for x in al["lines"] if x["kind"] == "beat"]
    order = {x["id"]: i for i, x in enumerate(beats)}

    def group_of(ln):
        if ln["kind"] == "alt":
            return f"body:{ln['id']}"
        return "body:A" if (ln["beat"] or "").upper() in replaced else "shared"
    for a in alts:
        base = next((b for b in beats if (b["beat"] or "").upper() == a["replaces"]), None)
        order[a["id"]] = order[base["id"]] + 0.5 if base else len(order)
    for k in sorted(cuts["keeps"], key=lambda k: (order.get(k["line"], 99), k["in"])):
        ln = next(x for x in al["lines"] if x["id"] == k["line"])
        pid = f"{k['line']}_{sum(1 for p in parts if p['id'].startswith(k['line'] + '_')) + 1}"
        parts.append({"id": pid, "group": group_of(ln), "beat": ln["beat"] or ln["kind"], "say": ln["say"],
                      "src": k["src"], "in": k["in"], "out": k["out"], "reframe": {"mode": "center"}})
        line_parts.setdefault(k["line"], []).append(pid)
    variants = [{"id": "A", "body": "A"}] + [{"id": a["id"], "body": a["id"]} for a in alts if a["id"] in line_parts]
    if not alts:
        variants = [{"id": "main"}]
    for ln in al["lines"]:
        if not ln["on_screen"] or ln["id"] not in line_parts:
            continue
        lw = [w for w in words if w["line"] == ln["id"]]
        end_anchor = {"part": line_parts[ln["id"]][-1], "edge": "end"}
        if ln["on_screen_kind"] == "title":
            c = ln["chosen"]
            fs, rate = faces_in(srcs[c["src"]], c["start"], c["end"])
            y = 28.0
            if fs:
                cy = sum(f["cy_pct"] for f in fs) / len(fs)
                fh = sum(f["h_pct"] for f in fs) / len(fs)
                y = round(cy - 0.9 * fh, 1)  # title block sits on the top of the head
            titles.append({"id": f"t-{ln['id']}", "text": ln["on_screen"], "style": "headline",
                           "at": {"part": line_parts[ln["id"]][0], "edge": "start"}, "until": end_anchor,
                           "y_pct": y, "anim": {"in": "pop", "out": "cut"}})
            continue
        pieces = [p.strip() for p in ln["on_screen"].split(" / ") if p.strip()]
        anchors = [anchor_word(p, lw) for p in pieces]
        for i, (p, a) in enumerate(zip(pieces, anchors)):
            until = {"word": anchors[i + 1]} if i + 1 < len(pieces) and anchors[i + 1] else end_anchor
            y = 15 if ln["beat"] and ln["beat"].upper() == "HOOK" else 58
            graphics.append({"id": f"g-{ln['id']}-{i + 1}", "kind": "text", "text": p, "style": "graphic",
                             "at": {"word": a}, "until": until,
                             "box": {"x_pct": 8, "y_pct": y, "w_pct": 84}, "anim": {"in": "pop", "out": "cut"}})
            toks = {align.norm(t) for t in p.split()} - STOP
            emphasis += [w["ref"] for w in lw if align.norm(w["w"]) in toks]
    plan = {
        "schema": "dlt-plan/0.1", "id": ad, "ad_type": "own", "client": None, "platform": "meta",
        "status": "draft", "lock": None,
        "script": {"path": script["source"], "parsed": os.path.relpath(os.path.join(ad_dir, "script.json"), ROOT)},
        "sources": {s["key"]: {"path": s["raw"], "ingest": s["dir"], "role": "talking_head"} for s in al["sources"]},
        "output": {"master": {"w": 1440, "h": 2560}, "delivery": {"w": 1080, "h": 1920}, "fps": 30,
                   "max_duration_s": None, "target_duration_s": list(target)},
        "parts": parts,
        "variants": variants,
        "layers": {"titles": titles, "graphics": graphics,
                   "captions": {"style": "auto", "emphasis": sorted(set(emphasis)), "hide": [],
                                "fixes": [{"word": f"{k}:{i}", "text": w["w"], "heard_by": w["heard_by"]}
                                          for k, sr in srcs.items() for i, w in enumerate(sr["words"]) if w.get("fixed")]}},
        "audio": {"loudness_lufs": None, "music": None},
    }
    plan["checks"] = lock_checks(plan, script, al, words, srcs, target)
    jsonschema.Draft202012Validator(load(os.path.join(ROOT, "plans", "plan.schema.json"))).validate(plan)
    return plan


def in_variant(p, v):
    g = p["group"]
    return g in ("shared", "cta") or g == f"hook:{v.get('hook')}" or g == f"body:{v.get('body')}"


def lock_checks(plan, script, al, words, srcs, target):
    c = {}
    s = al["summary"]
    c["no_missing_lines"] = {"status": "fail" if s["missing"] else "pass",
                             "notes": [f"{ln['id']}: {f}" for ln in al["lines"] for f in ln["flags"] if f.startswith("MISSING")]}
    partial = [f"{ln['id']}: {f}" for ln in al["lines"] for f in ln["flags"] if f.startswith("PARTIAL")]
    c["no_unresolved_off_script"] = {"status": "fail" if partial else "pass", "notes": partial}
    ledger = numbers_guard.spoken_ledger(words)
    blocks, warns = [], []
    for item in plan["layers"]["titles"] + plan["layers"]["graphics"]:
        r = numbers_guard.check(item["text"], ledger)
        blocks += [f"{item['text']!r}: {b}" for b in r["block"]]
        warns += [f"{item['text']!r}: {w}" for w in r["warn"]]
    c["numbers_rule"] = {"status": "fail" if blocks else "warn" if warns else "pass", "notes": blocks + warns}
    mm = [f"{ln['id']}: {f}" for ln in al["lines"] for f in ln["flags"] if f.startswith("NUMBER")]
    c["say_numbers_match_transcript"] = {"status": "fail" if mm else "pass", "notes": mm}
    notes, ok = [], True
    for v in plan["variants"]:
        dur = round(sum(p["out"] - p["in"] for p in plan["parts"] if in_variant(p, v)), 2)
        ok &= target[0] <= dur <= target[1]
        notes.append(f"variant {v['id']}: {dur}s (target {target[0]}-{target[1]}s, Andy rule 2)")
    c["duration_in_range"] = {"status": "pass" if ok else "fail", "notes": notes}
    sp = []
    for t in plan["layers"]["titles"]:
        p = next(x for x in plan["parts"] if x["id"] == t["at"]["part"])
        _, rate = faces_in(srcs[p["src"]], p["in"], p["out"])
        if rate < 0.8:
            sp.append(f"{t['id']}: face in {rate:.0%} of samples")
    c["speaker_present_for_titles"] = {"status": "fail" if sp else "pass", "notes": sp}
    c["script_lock_items"] = {"status": "pending", "notes": script["lock_items"]}
    c["meta_policy_items"] = {"status": "pending", "notes": script["meta_scan"]}
    notes = [f"{ln['id']}: wording {d}" for ln in al["lines"] if ln["chosen"] for d in ln["chosen"].get("wording", [])]
    groups = {}
    for f in plan["layers"]["captions"]["fixes"]:
        k, i = f["word"].split(":")
        w = srcs[k]["words"][int(i)]
        groups.setdefault((k, w["was"], f["heard_by"]), []).append(f["text"])
    notes += [f"{k}: heard \"{was}\" -> \"{' '.join(t)}\" ({hb})" for (k, was, hb), t in groups.items()]
    pending = any(hb.startswith("PENDING") for (_, _, hb) in groups)
    c["caption_word_fixes"] = {"status": "pending" if pending else "pass", "notes": notes}
    if script.get("draft"):
        c["script_is_final"] = {"status": "pending", "notes": [f"{script['source']} is a DRAFT reconstructed from the takes"]}
    return c


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    target = (35, 50)
    if "--target" in args:
        i = args.index("--target")
        target = (float(args[i + 1]), float(args[i + 2]))
    plan = build(args[0], target)
    out = os.path.join(args[0], "plan.json")
    with open(out, "w") as f:
        json.dump(plan, f, indent=1)
    print(f"wrote {os.path.relpath(out, ROOT)}  ({len(plan['parts'])} parts, {len(plan['layers']['titles'])} titles, "
          f"{len(plan['layers']['graphics'])} graphics)  schema OK")
    for k, v in plan["checks"].items():
        print(f"  [{v['status'].upper():7s}] {k}")
        for n in v.get("notes", []):
            print(f"            {n[:130]}")
