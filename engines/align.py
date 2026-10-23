"""Phase 5: match script SAY lines to the takes -> takes per line + MISSING / OFF-SCRIPT flags.

  python3 engines/align.py plans/<ad>/script.json work/<clip1>/ work/<clip2>/ ... [-o alignment.json]

Each SAY line is found in every source with local alignment (Smith-Waterman on words),
so a misheard word or two still matches. Every occurrence is a take; the chosen take
follows sop-defaults.json cutting_sop_5.retake_policy (last complete take).

Flags per line:
  MISSING          no take covers >= partial_min_coverage of the line
  PARTIAL          best take covers less than match_min_coverage: wording differs from the script
  NUMBER_MISMATCH  numbers in SAY differ from numbers heard in the chosen take (the 255 vs 250 case)
OFF-SCRIPT: every stretch of speech in a source that is no take of any line
  (cue phrases like "okay go" are labeled cue; everything else off_script).
"""
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numbers_guard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "spec", "sop-defaults.json")) as f:
    CUT = json.load(f)["cutting_sop_5"]

WORDNUM = {**numbers_guard.UNITS, **numbers_guard.TEENS, **numbers_guard.TENS}
MATCH, NUM_SLOT, MISMATCH, GAP = 3, 1, -1, -1  # a number vs a different number still lines up


CONTRACTIONS = {"youre": "you are", "were": None, "theyre": "they are", "im": "i am", "ive": "i have",
                "youve": "you have", "weve": "we have", "ill": "i will", "youll": "you will", "well": None,
                "id": None, "its": "it is", "heres": "here is", "thats": "that is", "whats": "what is",
                "theres": "there is", "lets": "let us", "dont": "do not", "cant": "can not", "wont": "will not",
                "isnt": "is not", "arent": "are not", "didnt": "did not", "doesnt": "does not"}


def pieces(word):
    """One spoken/written word -> normalized tokens: split hyphens/slashes, expand contractions
    (only when written with an apostrophe, so "were"/"well" stay words)."""
    out = []
    for part in re.split(r"[-/–—]", word):
        had_apos = "'" in part or "’" in part
        t = norm(part)
        if not t:
            continue
        exp = CONTRACTIONS.get(t) if had_apos else None
        out += exp.split() if exp else [t]
    return out


def tokenize_words(words):
    """[(token, word_index)] for a word list."""
    return [(t, i) for i, w in enumerate(words) for t in pieces(w["w"])]


def norm(w):
    t = re.sub(r"[^a-z0-9']", "", w.lower().replace("’", "'"))
    t = t.replace("'", "")
    if t in WORDNUM:
        return str(WORDNUM[t])
    return t


def same(a, b):
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def pair_score(a, b, masked):
    if masked:
        return MISMATCH
    if same(a, b):
        return MATCH
    if a.isdigit() and b.isdigit():
        return NUM_SLOT  # aligned, not counted as a match: NUMBER_MISMATCH reports it
    return MISMATCH


def smith_waterman(s, t, mask):
    """Best local alignment of script tokens s inside transcript tokens t (masked tokens
    cannot match). Returns (score, t_start, t_end_exclusive, matched_script_tokens)."""
    n, m = len(s), len(t)
    H = [[0] * (m + 1) for _ in range(n + 1)]
    best = (0, 0, 0)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = H[i - 1][j - 1] + pair_score(s[i - 1], t[j - 1], mask[j - 1])
            H[i][j] = max(0, diag, H[i - 1][j] + GAP, H[i][j - 1] + GAP)
            if H[i][j] > best[0]:
                best = (H[i][j], i, j)
    score, i, j = best
    if score == 0:
        return 0, 0, 0, 0
    j_end, matched = j, 0
    while i > 0 and j > 0 and H[i][j] > 0:
        ps = pair_score(s[i - 1], t[j - 1], mask[j - 1])
        if H[i][j] == H[i - 1][j - 1] + ps:
            matched += 1 if ps == MATCH else 0
            i, j = i - 1, j - 1
        elif H[i][j] == H[i - 1][j] + GAP:
            i -= 1
        else:
            j -= 1
    return score, j, j_end, matched


def load_source(d):
    d = d.rstrip("/")
    with open(os.path.join(d, "words.json")) as f:
        words = json.load(f)
    with open(os.path.join(d, "ingest.json")) as f:
        ing = json.load(f)
    key = re.sub(r"[^a-z0-9_]", "_", os.path.basename(d).lower())
    return {"key": key, "dir": os.path.relpath(d, ROOT) + "/", "raw": ing["source"],
            "proxy": ing["working_copy"]["path"], "duration": ing["source_info"]["duration"],
            "words": words, "toks": tokenize_words(words)}


def find_takes(line_tokens, src):
    """All occurrences of one line in one source, best first, masking each found span."""
    toks = [t for t, _ in src["toks"]]
    takes, mask = [], [False] * len(toks)
    while True:
        score, j0, j1, matched = smith_waterman(line_tokens, toks, mask)
        cov = matched / max(1, len(line_tokens))
        if cov < CUT["partial_min_coverage"]:
            break
        w = src["words"]
        w0, w1 = src["toks"][j0][1], src["toks"][j1 - 1][1]
        takes.append({"src": src["key"], "w0": w0, "w1": w1, "start": w[w0]["start"], "end": w[w1]["end"],
                      "coverage": round(cov, 2), "heard": " ".join(x["w"] for x in w[w0:w1 + 1])})
        for k, (_, wi) in enumerate(src["toks"]):
            if w0 <= wi <= w1:
                mask[k] = True
    return takes


def word_diff(say_tokens, heard_tokens):
    sm = difflib.SequenceMatcher(None, say_tokens, heard_tokens)
    out = []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op != "equal":
            out.append(f"{op}: script \"{' '.join(say_tokens[a0:a1])}\" / heard \"{' '.join(heard_tokens[b0:b1])}\"")
    return out


def align(script, sources):
    lines = ([dict(b, kind="beat") for b in script["beats"]] + [dict(h, kind="hook") for h in script["hooks"]] +
             [dict(a, kind="alt") for a in script.get("alternates", [])])
    used = {s["key"]: [None] * len(s["words"]) for s in sources}
    out_lines = []
    for ln in lines:
        toks = [t for w in ln["say"].split() for t in pieces(w)]
        takes = sorted((t for s in sources for t in find_takes(toks, s)),
                       key=lambda t: (t["src"], t["start"]))
        complete = [t for t in takes if t["coverage"] >= CUT["match_min_coverage"]]
        flags, chosen = [], None
        if complete:
            # last complete take = the latest one in the source that holds the most complete takes
            chosen = complete[-1] if CUT["retake_policy"] == "last_complete" else max(complete, key=lambda t: t["coverage"])
        elif takes:
            chosen = max(takes, key=lambda t: t["coverage"])
            flags.append(f"PARTIAL: best take covers {chosen['coverage']:.0%} of the line")
        else:
            flags.append("MISSING: no take of this line in any source")
        if chosen:
            heard_toks = [t for w in chosen["heard"].split() for t in pieces(w)]
            diff = word_diff(toks, heard_toks)
            if diff:
                chosen["wording"] = diff
            said = sorted((n["value"], n["unit"]) for n in numbers_guard.spoken_ledger(
                [{"w": w, "p": 1.0, "start": 0, "end": 0} for w in ln["say"].split()]))
            src = next(s for s in sources if s["key"] == chosen["src"])
            heard = sorted((n["value"], n["unit"]) for n in numbers_guard.spoken_ledger(
                src["words"][chosen["w0"]:chosen["w1"] + 1]))
            if [v for v, _ in said] != [v for v, _ in heard]:
                flags.append(f"NUMBER_MISMATCH: script says {said}, take has {heard}")
        for t in takes:
            role = "chosen" if t is chosen else "retake"
            for k in range(t["w0"], t["w1"] + 1):
                if used[t["src"]][k] is None or role == "chosen":
                    used[t["src"]][k] = (ln["id"], role)
        out_lines.append({"id": ln["id"], "kind": ln["kind"], "beat": ln.get("beat"), "say": ln["say"],
                          "replaces": ln.get("replaces"),
                          "on_screen": ln.get("on_screen"), "on_screen_kind": ln.get("on_screen_kind"),
                          "protected_pauses": ln.get("protected_pauses", []),
                          "chosen": chosen, "takes": takes, "flags": flags})
    off = []
    cues = [c.split() for c in CUT["cue_phrases"]]
    for s in sources:
        k, words = 0, s["words"]
        while k < len(words):
            if used[s["key"]][k] is not None:
                k += 1
                continue
            j = k
            while j + 1 < len(words) and used[s["key"]][j + 1] is None:
                j += 1
            toks = [t for w in words[k:j + 1] for t in pieces(w["w"])]
            kind = "cue" if any(" ".join(c) in " ".join(toks) for c in cues) else "off_script"
            off.append({"src": s["key"], "w0": k, "w1": j, "start": words[k]["start"], "end": words[j]["end"],
                        "text": " ".join(w["w"] for w in words[k:j + 1]), "kind": kind})
            k = j + 1
    return {"script": script["source"], "sources": [{k: s[k] for k in ("key", "dir", "raw", "proxy", "duration")}
                                                     for s in sources],
            "lines": out_lines, "off_script": off,
            "summary": {"lines": len(out_lines),
                        "missing": sum(any(f.startswith("MISSING") for f in ln["flags"]) for ln in out_lines),
                        "partial": sum(any(f.startswith("PARTIAL") for f in ln["flags"]) for ln in out_lines),
                        "number_mismatch": sum(any(f.startswith("NUMBER") for f in ln["flags"]) for ln in out_lines),
                        "off_script_spans": sum(o["kind"] == "off_script" for o in off),
                        "cue_spans": sum(o["kind"] == "cue" for o in off)}}


def report(a):
    for ln in a["lines"]:
        c = ln["chosen"]
        print(f"[{ln['id']}] {ln['beat'] or ln['kind']}: \"{ln['say'][:70]}\"")
        if c:
            print(f"    chosen {c['src']} {c['start']:.2f}-{c['end']:.2f}s  coverage {c['coverage']:.0%}  "
                  f"({len(ln['takes'])} take{'s' if len(ln['takes']) != 1 else ''})")
            for d in c.get("wording", []):
                print(f"    wording {d}")
        for f in ln["flags"]:
            print(f"    FLAG {f}")
    print("OFF-SCRIPT / cues:")
    for o in a["off_script"]:
        print(f"    {o['kind']:10s} {o['src']} {o['start']:.2f}-{o['end']:.2f}s  \"{o['text'][:80]}\"")
    print(f"summary: {a['summary']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    out = None
    if "-o" in args:
        out = args[args.index("-o") + 1]
        args = args[:args.index("-o")] + args[args.index("-o") + 2:]
    if len(args) < 2:
        sys.exit(__doc__)
    with open(args[0]) as f:
        script = json.load(f)
    a = align(script, [load_source(d) for d in args[1:]])
    if out:
        with open(out, "w") as f:
            json.dump(a, f, indent=1)
    report(a)
