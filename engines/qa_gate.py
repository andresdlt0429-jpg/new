"""The QA gate (stand-in for SOP 8): one verdict per variant before anything ships.

  python3 engines/qa_gate.py plans/<ad>/plan.json

Collects, per variant:
  1 lock checks         plan.checks (SOP 2.1 stand-in): fail -> BLOCK, pending -> HOLD
  2 policy scan         spec/policy-rules.json (QA-policy.md): BLOCK / HOLD / NOTE
  3 render QA           out/<ad>/<ad>_<variant>[_DRAFT]_qa.json: any fail -> BLOCK; missing or older
                        than the plan -> BLOCK (re-render)
  4 client card       ad_type client: clients/<id>/card.json codes C01-C10 (BLOCK / HOLD)
  5 delivery state      a DRAFT render can never PASS (it is not locked) -> HOLD
  6 manual checks       QA-policy manual list -> HOLD until accepted
HOLD items clear only through plans/<ad>/acceptances.json (Andy's exact words, dated).

Verdict: BLOCK if anything blocks, else HOLD if anything waits on Andy, else PASS.
Writes out/<ad>/<ad>_<variant>_gate.md and _gate.json.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import client_card  # noqa: E402
import policy_scan  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p) as f:
        return json.load(f)


def accepted(acc, item, variant):
    return next((a for a in acc if a["item"] == item and a.get("variant") in (None, "*", variant)), None)


def gate_variant(plan, plan_path, v, acc, policy):
    ad, vid = plan["id"], v["id"]
    rows = []  # (area, item, status BLOCK/HOLD/NOTE/PASS, detail)

    for k, c in plan.get("checks", {}).items():
        if c["status"] == "fail":
            rows.append(("lock", k, "BLOCK", "; ".join(c.get("notes", []))[:300]))
        elif c["status"] == "pending":
            a = accepted(acc, k, vid)
            rows.append(("lock", k, "PASS" if a else "HOLD",
                         f"accepted by {a['by']} {a['date']}: \"{a['words']}\"" if a else "; ".join(c.get("notes", []))[:300]))
        else:
            rows.append(("lock", k, "PASS", ""))

    if plan.get("ad_type") == "client":  # Phase 10: the client card must be complete and signed off
        card, d = client_card.load(plan["client"])
        for code, sev, msg in client_card.check(card, d):
            a = accepted(acc, code, vid) if sev == "HOLD" else None
            rows.append(("client", code, "PASS" if a else sev,
                         f"accepted by {a['by']} {a['date']}: \"{a['words']}\" | {msg}" if a else msg))

    for f in policy.get(vid, []):
        a = accepted(acc, f["rule"], vid) if f["severity"] == "HOLD" else None
        st = f["severity"] if not a else "PASS"
        detail = f"{f['where']} \"{f['match']}\" ({f['ref']}). {f['why']}"
        if a:
            detail = f"accepted by {a['by']} {a['date']}: \"{a['words']}\" | " + detail
        rows.append(("policy", f["rule"], st, detail))

    qa_p = None
    for tag in ("", "_DRAFT"):
        p = os.path.join(ROOT, "out", ad, f"{ad}_{vid}{tag}_qa.json")
        if os.path.exists(p):
            qa_p = p
            break
    if not qa_p:
        rows.append(("render", "render_qa", "BLOCK", "no render QA for this variant: render it first"))
    else:
        qa = load(qa_p)
        if os.path.getmtime(qa_p) < os.path.getmtime(plan_path):
            rows.append(("render", "render_fresh", "BLOCK", "the plan changed after this render: re-render"))
        for k, c in qa.get("qa", {}).items():
            st = {"pass": "PASS", "n/a": "NOTE"}.get(c["status"], "BLOCK")
            val = c.get("value")
            rows.append(("render", k, st, val if isinstance(val, str) else json.dumps(val)[:300]))
        if qa.get("draft"):
            rows.append(("delivery", "clean_render", "HOLD",
                         "this is a DRAFT render; lock the plan and render without --draft to deliver"))

    for m in policy_scan.RULES["manual"]:
        a = accepted(acc, m["id"], vid)
        rows.append(("manual", m["id"], "PASS" if a else "HOLD",
                     f"accepted by {a['by']} {a['date']}: \"{a['words']}\"" if a else m["check"]))

    verdict = "BLOCK" if any(r[2] == "BLOCK" for r in rows) else "HOLD" if any(r[2] == "HOLD" for r in rows) else "PASS"
    return verdict, rows, qa_p


def write_report(plan, v, verdict, rows, qa_p):
    ad, vid = plan["id"], v["id"]
    out = os.path.join(ROOT, "out", ad)
    os.makedirs(out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    counts = {s: sum(r[2] == s for r in rows) for s in ("BLOCK", "HOLD", "NOTE", "PASS")}
    md = [f"# QA gate: {ad} / variant {vid}", "",
          f"**Verdict: {verdict}**  ({counts['BLOCK']} block, {counts['HOLD']} hold, {counts['NOTE']} note, "
          f"{counts['PASS']} pass)  ·  {stamp}", "",
          f"Render QA: `{os.path.relpath(qa_p, ROOT) if qa_p else 'none'}`  ·  Policy: `spec/QA-policy.md`", ""]
    for status in ("BLOCK", "HOLD", "NOTE", "PASS"):
        sel = [r for r in rows if r[2] == status]
        if not sel:
            continue
        md += [f"## {status} ({len(sel)})", "", "| Area | Item | Detail |", "| :-- | :-- | :-- |"]
        md += [f"| {a} | `{i}` | {str(d).replace('|', '/')} |" for a, i, _, d in sel]
        md.append("")
    p_md = os.path.join(out, f"{ad}_{vid}_gate.md")
    with open(p_md, "w") as f:
        f.write("\n".join(md))
    with open(os.path.join(out, f"{ad}_{vid}_gate.json"), "w") as f:
        json.dump({"ad": ad, "variant": vid, "verdict": verdict, "at": stamp, "counts": counts,
                   "rows": [{"area": a, "item": i, "status": s, "detail": d} for a, i, s, d in rows]}, f, indent=1)
    return p_md, counts


def main(plan_path):
    plan = load(plan_path)
    acc = load(os.path.join(os.path.dirname(plan_path), "acceptances.json"), [])
    policy = policy_scan.scan_plan(plan)
    worst = "PASS"
    for v in plan["variants"]:
        verdict, rows, qa_p = gate_variant(plan, plan_path, v, acc, policy)
        p_md, counts = write_report(plan, v, verdict, rows, qa_p)
        print(f"variant {v['id']}: {verdict}  {counts}  -> {os.path.relpath(p_md, ROOT)}")
        for a, i, s, d in rows:
            if s in ("BLOCK", "HOLD"):
                print(f"   [{s}] {a:8s} {i:28s} {str(d)[:110]}")
        worst = max(worst, verdict, key=["PASS", "HOLD", "BLOCK"].index)
    sys.exit({"PASS": 0, "HOLD": 2, "BLOCK": 1}[worst])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
