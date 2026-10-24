"""Policy scan (stand-in for SOP 6.2): scan what an ad says and shows against spec/policy-rules.json.

  python3 engines/policy_scan.py --write-policy            regenerate spec/QA-policy.md from the rules
  python3 engines/policy_scan.py plans/<ad>/plan.json      scan every variant of a plan
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_P = os.path.join(ROOT, "spec", "policy-rules.json")
with open(RULES_P) as f:
    RULES = json.load(f)


def norm_text(s):
    return re.sub(r"\s+", " ", s.replace("’", "'")).strip().lower()


def scan_text(text, where):
    """-> [{rule, severity, where, match, text}]"""
    t = norm_text(text)
    out = []
    for r in RULES["rules"]:
        if where not in r["where"]:
            continue
        for pat in r["patterns"]:
            m = re.search(pat, t)
            if m:
                out.append({"rule": r["id"], "severity": r["severity"], "where": where,
                            "match": m.group(0).strip(), "text": text, "why": r["why"], "instead": r["instead"]})
                break
    return out


def variant_texts(plan, variant):
    """What a variant says (kept words, joined per part) and shows (titles, graphics, tick items)."""
    srcs = {k: align.load_source(os.path.join(ROOT, v["ingest"])) for k, v in plan["sources"].items()}

    def wanted(p):
        g = p["group"]
        return g in ("shared", "cta") or g == f"hook:{variant.get('hook')}" or g == f"body:{variant.get('body')}"
    parts = [p for p in plan["parts"] if wanted(p)]
    spoken = []
    for p in parts:
        ws = [w["w"] for w in srcs[p["src"]]["words"] if p["in"] - 0.001 <= w["start"] < p["out"]]
        spoken.append((p["id"], " ".join(ws)))
    shown = []
    lines_in = {p["id"].split("_")[0] for p in parts}
    for item in plan["layers"]["titles"] + plan["layers"]["graphics"]:
        if item["id"].split("-")[1] not in lines_in:
            continue
        text = item.get("text") or " / ".join(i["text"] for i in item.get("items", []))
        shown.append((item["id"], text))
    return spoken, shown


def scan_plan(plan):
    out = {}
    for v in plan["variants"]:
        spoken, shown = variant_texts(plan, v)
        found = []
        # spoken: scan the whole variant as one text too, so phrases across a cut are caught
        found += [dict(f, ref="whole spoken text") for f in scan_text(" ".join(t for _, t in spoken), "spoken")]
        for ref, t in shown:
            found += [dict(f, ref=ref) for f in scan_text(t, "on_screen")]
        seen, uniq = set(), []
        for f in found:
            k = (f["rule"], f["where"], f["match"])
            if k not in seen:
                seen.add(k)
                uniq.append(f)
        out[v["id"]] = uniq
    return out


def write_policy():
    sev = RULES["severity"]
    lines = ["# QA policy", "",
             "**Generated from `spec/policy-rules.json` by `python3 engines/policy_scan.py --write-policy`. Do not edit by hand.**", "",
             "Stand-in for SOP 6.2 until `spec/SOP-DLT-1-v2-video-standard.md` arrives. The QA gate "
             "(`engines/qa_gate.py`) runs every rule below on what each ad variant says (kept words) and shows "
             "(titles, graphics, tick items).", "",
             "Sources: " + "; ".join(f"**{k}**: {v}" for k, v in RULES["_sources"].items()), "",
             "## Severity", "", "| Level | Meaning |", "| :-- | :-- |"]
    lines += [f"| {k} | {v} |" for k, v in sev.items()]
    lines += ["", "## Automatic rules", "", "| Rule | Level | Checks | Why | Say instead | Source |",
              "| :-- | :-- | :-- | :-- | :-- | :-- |"]
    for r in RULES["rules"]:
        lines.append(f"| `{r['id']}` | {r['severity']} | {', '.join(r['where'])} | {r['why']} | {r['instead']} | {r['source']} |")
    lines += ["", "Rule 4 (every on-screen number was spoken) and profanity in the kept audio are also checked "
              "by the renderer's QA on the final video.", "",
              "## Manual checks (Andy or a reviewer ticks these)", ""]
    lines += [f"- [ ] `{m['id']}`: {m['check']} ({m['source']})" for m in RULES["manual"]]
    lines += ["", "## Accepting a HOLD", "",
              "A HOLD item ships only when Andy accepts it in writing. The gate reads "
              "`plans/<ad>/acceptances.json`:", "", "```json",
              '[{"item": "jobs_guarantee", "variant": "A", "by": "Andy", "date": "YYYY-MM-DD", '
              '"words": "his exact words"}]', "```", "",
              "The same words go into `spec/my-standards.md` as a dated row.", ""]
    p = os.path.join(ROOT, "spec", "QA-policy.md")
    with open(p, "w") as f:
        f.write("\n".join(lines))
    return p


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "--write-policy":
        print("wrote", os.path.relpath(write_policy(), ROOT))
    else:
        with open(a[0]) as f:
            res = scan_plan(json.load(f))
        for v, fs in res.items():
            print(f"variant {v}: {len(fs)} findings")
            for x in fs:
                print(f"  [{x['severity']:5s}] {x['rule']:20s} {x['where']:9s} \"{x['match']}\"  ({x['ref']})")
