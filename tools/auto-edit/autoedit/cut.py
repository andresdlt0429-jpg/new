"""The cut: voice edges, dead air, stutters and repeats, sentence punches. Pure numpy on 16 kHz audio.

NOTES "Cuts and stutters": voice_edges (cut where the voice actually stops), relisten (stretched words), drop repeats.
README: dead air over 0.4 s (agency), punch on each new sentence. SOP-11b rule 13: pauses up to 0.6 s kept (client).
SOP-11c T3/T4: per-take gain, denoise only a noisy take, silence threshold read from the file.
"""
import re

import numpy as np

from . import config, textutil
from .media import SR

HOP, WIN = 0.01, 0.03


def rms_track(a, t0, t1):
    """10 ms hops of RMS amplitude from t0 to t1 (seconds into a)."""
    ts = np.arange(max(0.0, t0), max(t0, min(t1, len(a) / SR - WIN)), HOP)
    half = int(WIN / 2 * SR)
    idx = (ts * SR).astype(int)
    out = np.array([np.sqrt(np.mean(a[max(0, i - half):i + half] ** 2) + 1e-12) for i in idx])
    return ts, out


def threshold(env):
    """SOP-11c T4 (the rig's tighten()): max(3x the 30th-percentile RMS, 12% of the segment peak), per file.
    The peak is the 99th percentile so one clap or pop cannot raise the gate."""
    return max(3 * np.percentile(env, 30), 0.12 * np.percentile(env, 99))


def db(x):
    return 20 * np.log10(np.maximum(x, 1e-9))


def take_gain(env, thr, target_db=-20.0):
    """SOP-11c T3: per-take speech gain, +/-8 dB max, so a hook filmed close matches a body filmed across the room.
    Denoise only when the take's noise floor sits above -50 dBFS."""
    speech = env[env > thr]
    level = db(np.sqrt(np.mean(speech ** 2))) if len(speech) else target_db
    floor = db(np.percentile(env, 10))
    return float(np.clip(target_db - level, -8, 8)), bool(floor > -50), float(floor)


def section_pieces(sec, words, audio, prof, protect=(), drops=(), relisten_fn=None):
    """Cut one section (a take trimmed to [from, to]) into pieces on the source clock.
    words: the take's proofread words on the source clock. Returns (pieces, report)."""
    rep = dict(cut=[], relistened=[], retakes=[], stretched=[])
    lo, hi = sec["from"], sec["to"]
    # plan times come from one transcript, the working copy is heard again: allow a word of slack either side
    ws = [list(w) for w in words if lo - 0.2 <= w[0] <= hi + 0.05 and w[1] >= lo - 0.05]
    if not ws:
        raise ValueError(f"no words between {lo} and {hi} in {sec['src']}")

    # relisten: a word longer than 0.9 s hides a restart (NOTES)
    if relisten_fn is not None:
        out = []
        for w in ws:
            if w[1] - w[0] > config.STRETCHED_WORD:
                heard = relisten_fn(w[0], w[1])
                rep["stretched"].append((round(w[0], 2), w[2], " ".join(h[2] for h in heard)))
                if len(heard) > 1:
                    rep["relistened"].append((round(w[0], 2), w[2], " ".join(h[2] for h in heard)))
                    out += [[max(w[0], h[0]), min(w[1], h[1]), h[2]] for h in heard]
                    continue
            out.append(w)
        ws = out

    # drops: plan phrases, then every repeated 1-6 word phrase (the first saying goes)
    spans = []
    for d in drops:
        r = textutil.find_phrase(ws, d["say"], d.get("nth", 0))
        if r:
            i, j = r
            end = ws[j][0] - 0.02 if j < len(ws) else ws[j - 1][1]
            spans.append((ws[i][0] - 0.02, end))
            rep["cut"].append((round(ws[i][0], 2), " ".join(w[2] for w in ws[i:j]), "drop"))
    toks = [textutil.norm(w[2]) for w in ws]
    for i, n in textutil.repeats(toks, config.REPEAT_MAX_N, protect):
        # "this. This" across a sentence end is not a stutter (SOP-11b QA 4)
        if re.search(r"[.?!]$", ws[i + n - 1][2]):
            continue
        spans.append((ws[i][0] - 0.02, ws[i + n][0] - 0.02))
        rep["cut"].append((round(ws[i][0], 2), " ".join(w[2] for w in ws[i:i + n]), "repeat"))
    rep["retakes"] = textutil.retakes(ws)

    # voice edges and dead air, read from the audio (whisper stretches words over pauses)
    ts, env = rms_track(audio, lo - 1.5, hi + 1.5)
    thr = threshold(env)
    loud = lambda t: env[min(len(env) - 1, max(0, int(round((t - ts[0]) / HOP))))] > thr
    t = ws[0][0]
    while t > ws[0][0] - 0.2 and loud(t - HOP):
        t -= HOP
    start = t - 0.06
    t = ws[-1][1]
    while t < ws[-1][1] + 0.25 and loud(t):
        t += HOP
    end = t + (0.10 if sec.get("tail") is None else sec["tail"])
    run = None
    for x in np.arange(start + 0.05, ws[-1][1], HOP):
        q = not loud(x)
        if q and run is None:
            run = x
        if not q and run is not None:
            if x - run > prof["max_pause"]:
                spans.append((run + prof["keep_pad"], x - prof["keep_pad"]))
            run = None

    # sentence punches (agency) / alternate punches happen at cut points; add sentence ends as zero-length cuts
    if prof["punch"] == "sentence":
        for i, w in enumerate(ws[:-1]):
            if re.search(r"[.?!]$", w[2]):
                mid = (w[1] + ws[i + 1][0]) / 2
                if not any(a - 0.3 <= mid <= b + 0.3 for a, b in spans):
                    spans.append((mid, mid))

    # intervals = [start, end] minus spans
    spans = sorted((max(start, a), min(end, b)) for a, b in spans if b >= a)
    pieces, cur = [], start
    for a, b in spans:
        if a > cur:
            pieces.append([cur, a])
        cur = max(cur, b)
    if end > cur:
        pieces.append([cur, end])
    # a piece holding no word is lead-in noise or a breath sliver: drop it
    mids = [(w[0] + w[1]) / 2 for w in ws if not any(a <= (w[0] + w[1]) / 2 <= b for a, b in spans if b > a)]
    pieces = [p for p in pieces if any(p[0] <= m <= p[1] for m in mids) or p is pieces[-1] and p[1] - p[0] > 0.3]
    gain, denoise, floor = take_gain(env, thr)
    rep.update(threshold_db=round(float(db(thr)), 1), gain_db=round(gain, 1), denoise=denoise, floor_db=round(floor, 1))
    # a word stays when any of it plays (whisper stretches words into the pauses we cut)
    kept = [w for w in ws if any(min(w[1], p[1]) - max(w[0], p[0]) >= 0.04 for p in pieces)]
    return [dict(a=p[0], b=p[1]) for p in pieces], kept, rep
