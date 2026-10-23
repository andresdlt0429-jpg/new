"""Phase 4 cutter: chosen takes -> tight keep list + the "cut on the floor" list.

  python3 engines/cutter.py plans/<ad>/alignment.json [-o plans/<ad>/cuts.json] [--only SRC]

Rules come from spec/sop-defaults.json (placeholders for SOP 5 and 2.6):
  - cut just outside the first/last word (pad_before_s / pad_after_s), never into a
    neighbouring word, then slide each cut to the quietest 10 ms within snap_window_s
  - a gap between words longer than max_gap_s is closed down to max_gap_s
  - PROTECTED pauses (script Delivery: Pause before "X", stand still, slowest moment)
    are never closed below protected min_s, nor left longer than max_s
  - filler words are cut when they stand alone between gaps
Everything not kept is listed on the floor with a reason.
"""
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align  # noqa: E402
import media  # noqa: E402

ROOT = media.ROOT
with open(os.path.join(ROOT, "spec", "sop-defaults.json")) as f:
    SOP = json.load(f)
CUT, PP = SOP["cutting_sop_5"], SOP["protected_pauses_sop_2_6"]
FRAME = 0.01  # RMS window for quiet-point snapping


def rms_track(proxy):
    a = media.load_audio_16k(os.path.join(ROOT, proxy))
    n = int(16000 * FRAME)
    a = a[: len(a) // n * n].reshape(-1, n)
    return np.sqrt((a ** 2).mean(axis=1) + 1e-12)


def quietest(rms, t, lo, hi):
    """Quietest 10 ms frame time within [t - snap, t + snap], clamped to [lo, hi]."""
    w = CUT["snap_window_s"]
    a, b = max(lo, t - w), min(hi, t + w)
    if b <= a:
        return round(min(max(t, lo), hi), 3)
    i0, i1 = int(a / FRAME), max(int(a / FRAME) + 1, int(b / FRAME))
    seg = rms[i0:i1]
    if not len(seg):
        return round(t, 3)
    return round((i0 + int(np.argmin(seg))) * FRAME + FRAME / 2, 3)


def protected_word_idx(words, w0, w1, pauses):
    """Word indexes (inside the take) that have a protected pause BEFORE them."""
    out = {}
    toks = [align.norm(w["w"]) for w in words[w0:w1 + 1]]
    for p in pauses:
        if not p.get("before"):
            out[w0] = p["why"]  # slowest moment: breathing room before the line
            continue
        target = [align.norm(x) for x in p["before"].split() if align.norm(x)]
        for k in range(len(toks) - len(target) + 1):
            if toks[k:k + len(target)] == target:
                out[w0 + k] = p["why"]
    return out


def cut_take(line, src, rms):
    words, dur = src["words"], src["duration"]
    c = line["chosen"]
    w0, w1 = c["w0"], c["w1"]
    prot = protected_word_idx(words, w0, w1, line["protected_pauses"])
    fillers = set(CUT["filler_words"])
    keep_idx = [k for k in range(w0, w1 + 1) if align.norm(words[k]["w"]) not in fillers]
    floor, keeps, notes = [], [], []
    for k in range(w0, w1 + 1):
        if k not in keep_idx:
            floor.append({"src": src["key"], "start": words[k]["start"], "end": words[k]["end"],
                          "reason": "filler word", "text": words[k]["w"]})
    # group kept words into runs split at gaps > max_gap (unless protected)
    runs, cur = [], [keep_idx[0]]
    for a, b in zip(keep_idx, keep_idx[1:]):
        gap = words[b]["start"] - words[a]["end"]
        limit = PP["max_s"] if b in prot else CUT["max_gap_s"]
        if gap > limit:
            runs.append(cur)
            cur = [b]
        else:
            cur.append(b)
        if b in prot and gap < PP["min_s"]:
            notes.append(f"protected pause before \"{words[b]['w']}\" is only {gap:.2f}s in the take "
                         f"(SOP min {PP['min_s']}s); kept as recorded, not stretched")
    runs.append(cur)
    for r_i, run in enumerate(runs):
        first, last = run[0], run[-1]
        prev_end = words[first - 1]["end"] if first > 0 else 0.0
        next_start = words[last + 1]["start"] if last + 1 < len(words) else dur
        pad_b = CUT["pad_before_s"]
        if first in prot and r_i > 0:
            # protected: the silence before this run is kept at min..max length
            gap = words[first]["start"] - words[runs[r_i - 1][-1]]["end"]
            pad_b = min(max(gap - CUT["pad_after_s"], PP["min_s"] - CUT["pad_after_s"]), PP["max_s"])
        elif first in prot:
            pad_b = min(words[first]["start"] - prev_end, PP["min_s"])
        t_in = quietest(rms, words[first]["start"] - pad_b, prev_end, words[first]["start"])
        t_out = quietest(rms, words[last]["end"] + CUT["pad_after_s"], words[last]["end"], next_start)
        keeps.append({"src": src["key"], "in": t_in, "out": t_out, "w0": first, "w1": last,
                      "protected_before": prot.get(first), "text": " ".join(words[k]["w"] for k in run)})
    return keeps, floor, notes


def floor_list(src, keeps, alignment):
    """Everything in the source that is not inside a keep, labelled."""
    dur, words = src["duration"], src["words"]
    spans = sorted((k["in"], k["out"]) for k in keeps if k["src"] == src["key"])
    holes, t = [], 0.0
    for a, b in spans:
        if a > t + 0.005:
            holes.append((t, a))
        t = max(t, b)
    if dur > t + 0.005:
        holes.append((t, dur))
    retakes = [(tk["start"], tk["end"], ln["id"]) for ln in alignment["lines"] for tk in ln["takes"]
               if tk["src"] == src["key"] and tk is not ln["chosen"] and
               not (ln["chosen"] and tk["start"] == ln["chosen"]["start"] and tk["src"] == ln["chosen"]["src"])]
    offs = [(o["start"], o["end"], o["kind"]) for o in alignment["off_script"] if o["src"] == src["key"]]
    # words that form a whole cue phrase ("ready set go"), not just share a word with one
    toks = [align.norm(w["w"]) for w in words]
    cue_idx = set()
    for c in CUT["cue_phrases"]:
        ct = [align.norm(t) for t in c.split()]
        for i in range(len(toks) - len(ct) + 1):
            if toks[i:i + len(ct)] == ct:
                cue_idx.update(range(i, i + len(ct)))
    pos = {id(w): i for i, w in enumerate(words)}

    def label(w):
        mid = (w["start"] + w["end"]) / 2
        for st, e, kind in offs:
            if st <= mid <= e:
                if kind == "cue" and pos[id(w)] not in cue_idx:
                    return "false start"  # said between cues, not a cue itself
                return "cue chatter" if kind == "cue" else "OFF-SCRIPT"
        for st, e, lid in retakes:
            if st <= mid <= e:
                return f"retake of {lid}"
        if align.norm(w["w"]) in set(CUT["filler_words"]):
            return "filler word"
        return "dead air"

    out = []
    for a, b in holes:
        inside = [w for w in words if w["start"] < b - 0.01 and w["end"] > a + 0.01]
        # split the hole into labelled runs of words, with silences between them
        pieces, t = [], a
        for w in inside:
            lab = label(w)
            ws, we = max(a, w["start"]), min(b, w["end"])
            if ws - t > CUT["max_gap_s"]:
                pieces.append([t, ws, "silence", []])
            elif pieces and pieces[-1][2] == lab:
                pieces[-1][1] = we
                pieces[-1][3].append(w["w"])
                t = we
                continue
            if pieces and pieces[-1][2] == lab:
                pieces[-1][1] = we
                pieces[-1][3].append(w["w"])
            else:
                pieces.append([pieces[-1][1] if pieces else t, we, lab, [w["w"]]])
            t = we
        if b - t > 0.005:
            if pieces and b - t <= CUT["max_gap_s"]:
                pieces[-1][1] = b
            else:
                pieces.append([t, b, "silence", []])
        for ps, pe, lab, txt in pieces:
            if lab == "silence":
                lab = "lead-in silence" if ps <= 0.005 else "tail" if pe >= dur - 0.01 else "dead air"
            out.append({"src": src["key"], "start": round(ps, 2), "end": round(pe, 2),
                        "reason": lab, "text": " ".join(txt)})
    return out


def run(alignment, only=None):
    srcs = {s["key"]: align.load_source(os.path.join(ROOT, s["dir"])) for s in alignment["sources"]}
    keeps, floor, notes = [], [], []
    rms_cache = {}
    for ln in alignment["lines"]:
        if not ln["chosen"] or (only and ln["chosen"]["src"] != only):
            continue
        s = srcs[ln["chosen"]["src"]]
        if s["key"] not in rms_cache:
            rms_cache[s["key"]] = rms_track(s["proxy"])
        k, fl, nt = cut_take(ln, s, rms_cache[s["key"]])
        for x in k:
            x["line"] = ln["id"]
        keeps += k
        floor += fl
        notes += [f"{ln['id']}: {n}" for n in nt]
    for key, s in srcs.items():
        if only and key != only:
            continue
        floor += floor_list(s, keeps, alignment)
    floor.sort(key=lambda f: (f["src"], f["start"]))
    kept = sum(k["out"] - k["in"] for k in keeps)
    total = sum(s["duration"] for k, s in srcs.items() if not only or k == only)
    return {"keeps": keeps, "floor": floor, "notes": notes,
            "summary": {"source_s": round(total, 2), "kept_s": round(kept, 2),
                        "floor_s": round(total - kept, 2), "keeps": len(keeps)}}


def report(c):
    print("KEEP (in output order):")
    t = 0.0
    for k in c["keeps"]:
        d = k["out"] - k["in"]
        tag = f"  [protected pause before: {k['protected_before']}]" if k["protected_before"] else ""
        print(f"  {t:6.2f}s +{d:5.2f}s  {k['src']} {k['in']:6.2f}-{k['out']:6.2f}  \"{k['text'][:70]}\"{tag}")
        t += d
    print("\nCUT ON THE FLOOR:")
    by = {}
    for f in c["floor"]:
        print(f"  {f['src']} {f['start']:6.2f}-{f['end']:6.2f}  {f['end'] - f['start']:5.2f}s  "
              f"{f['reason']:16s} \"{f['text'][:60]}\"")
        r = re.sub(r" of B\d+", "", f["reason"])
        by[r] = by.get(r, 0) + f["end"] - f["start"]
    print("\nfloor by reason: " + ", ".join(f"{k} {v:.1f}s" for k, v in sorted(by.items(), key=lambda x: -x[1])))
    for n in c["notes"]:
        print(f"NOTE {n}")
    print(f"summary: {c['summary']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    out = only = None
    if "-o" in args:
        out = args[args.index("-o") + 1]
    if "--only" in args:
        only = args[args.index("--only") + 1]
    if not args:
        sys.exit(__doc__)
    with open(args[0]) as f:
        a = json.load(f)
    c = run(a, only)
    if out:
        with open(out, "w") as f:
            json.dump(c, f, indent=1)
    report(c)
