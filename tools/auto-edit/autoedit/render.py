"""Plan in, finished ad out.

  build    sections -> working copies (SOP-11a/e) -> transcripts -> proofread -> cut pieces (cut.py) -> b-roll windows
  pass A   pieces -> punch / settle / push-in picture (base.mov) + levelled voice (voice.wav)
  align    re-hear the voice track so every caption sits on the cut's real audio
  analyse  person matte: face boxes (titles land under the chin) and, when a title sits behind him, a matte video
  draw     titles, captions, end card -> behind.ass / front.ass
  pass B   composite + music bed + sfx -> raw -> -14 LUFS master (BT.709, CRF 14 slow film) -> two-pass phone copy
"""
import difflib
import json
from pathlib import Path

import numpy as np

from . import config, cut, fonts, looks, media, sound, textutil, transcribe
from .lock import render_lock
from .plan import load, slug

BT709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------------------------------------
def build(plan):
    prof = plan["_profile"]
    W, H = prof["size"]
    root = Path(plan["_root"])
    work = root / "work" / slug(plan["name"])
    work.mkdir(parents=True, exist_ok=True)
    model = prof["whisper"]
    pieces, words_out, reports, broll_cues = [], [], [], []
    t0 = 0.0
    for si, sec in enumerate(plan["sections"]):
        src = Path(sec["src"])
        if not src.exists():
            raise FileNotFoundError(f"section {si}: {src} (pull it first: python -m autoedit fetch ...)")
        info = media.probe(src)
        rng = None
        if info["duration"] > 90 and sec.get("from") is not None:
            lo = max(0.0, sec["from"] - 3)
            rng = (lo, (sec.get("to", info["duration"]) + 3 + (sec.get("tail") or 0)) - lo)
        copy, off = media.working_copy(src, work, *(rng or (None, None)))
        log(f"  section {si + 1}: {src.name} -> {Path(copy).name}")
        ws = [[s + off, e + off, w] for s, e, w in transcribe.words(copy, model, work)]
        ws = textutil.apply_fixes(ws, plan.get("fix", {}))
        lo_t, hi_t = sec.get("from"), sec.get("to")
        if sec.get("keep"):
            a = textutil.find_phrase(ws, sec["keep"][0], after=sec.get("after"))
            b = textutil.find_phrase(ws, sec["keep"][1], after=ws[a[0]][0] if a else sec.get("after"))
            if not a or not b:
                raise ValueError(f"section {si}: keep {sec['keep']} not heard in {src.name}")
            lo_t, hi_t = ws[a[0]][0], ws[b[1] - 1][1]
        lo_t = ws[0][0] if lo_t is None else lo_t
        hi_t = ws[-1][1] if hi_t is None else hi_t
        audio = media.audio16k(copy)
        # everything below runs on the working copy's clock
        cw = [[s - off, e - off, w] for s, e, w in ws]
        sec_c = dict(sec, **{"from": lo_t - off, "to": hi_t - off})
        relisten = (lambda a_, b_, _c=copy: transcribe.relisten(_c, a_, b_, model)) if plan.get("relisten", True) else None
        drops = [d for d in plan.get("drop", []) if d.get("section", si) == si]
        ps, kept, rep = cut.section_pieces(sec_c, cw, audio, prof, plan.get("protect", []), drops, relisten)
        rep["section"] = si
        rep["src"] = src.name
        reports.append(rep)
        cinfo = media.probe(copy)
        for k, p in enumerate(ps):
            pieces.append(dict(src=str(copy), section=si, a=float(p["a"]), b=float(p["b"]), t0=t0, gain=rep["gain_db"],
                               denoise=rep["denoise"], fy=sec.get("focus_y", 0.4), first=k == 0,
                               copy_w=cinfo["width"], copy_h=cinfo["height"]))
            t0 += p["b"] - p["a"]
        def to_out(t, _si=si):
            """Source time -> output time; a time inside a cut lands where the next kept piece starts."""
            mine = [p for p in pieces if p["section"] == _si]
            for p in mine:
                if p["a"] - 1e-6 <= t <= p["b"] + 1e-6:
                    return p["t0"] + t - p["a"]
            nxt = [p for p in mine if p["a"] > t]
            if nxt:
                return nxt[0]["t0"]
            return mine[-1]["t0"] + mine[-1]["b"] - mine[-1]["a"] if mine else None
        for w in kept:
            s, e = to_out(w[0]), to_out(w[1])
            if s is not None:
                words_out.append([s, max(e, s + 0.05), w[2], si])
        for b in sec.get("broll", []):
            r = textutil.find_phrase(kept, b["say"], b.get("nth", 0))
            if not r:
                broll_cues.append(dict(b, skipped="say not heard"))
                continue
            i = r[0] - (1 if b.get("vo") and r[0] > 0 else 0)     # vo b-roll starts one word before its term
            st = to_out(kept[i][0])
            broll_cues.append(dict(b, start=st, end=st + b.get("dur", 2.2)))
    total = t0
    # a target length (plan "duration"): the held tail after the last word gives or takes the difference,
    # never leaving less than 0.9 s after his last word (the end card has to be readable)
    if plan.get("duration") and pieces:
        last = pieces[-1]
        last_word = max(w[1] for w in words_out)
        delta = plan["duration"] - total
        floor = (last_word - last["t0"] + last["a"]) + 0.9
        new_b = max(floor, last["b"] + delta)
        total += new_b - last["b"]
        last["b"] = new_b
        if abs(total - plan["duration"]) > 0.05:
            log(f"  ! target {plan['duration']} s not reachable by the tail alone: {total:.2f} s (drop a line or a pause)")
    # zooms: agency punches on every sentence; client/breakout alternate on cuts inside a take
    cyc = prof["zoom_cycle"]
    k = 0
    for i, p in enumerate(pieces):
        if prof["punch"] == "sentence":
            p["z"] = cyc[i % len(cyc)]
        else:
            k = 0 if p["first"] else k + 1
            p["z"] = cyc[k % len(cyc)]
        p["new_shot"] = p["first"]
    return dict(plan=plan, prof=prof, W=W, H=H, work=str(work), pieces=pieces, total=total, words=words_out,
                reports=reports, broll=[b for b in broll_cues if "start" in b], broll_skipped=[b for b in broll_cues if "skipped" in b])


def video_segments(ed):
    """Talk pieces with b-roll windows cut out of their picture (his voice runs on). SOP-11b rule 10: no 2-3 frames of
    his face between two cutaways (min_face 0.5 s); NOTES: never a b-roll shot after the CTA."""
    wins = sorted((b["start"], b["end"], b) for b in ed["broll"])
    merged = []
    for s, e, b in wins:
        if merged and s - merged[-1][1] < 0.5:
            merged[-1][1] = max(merged[-1][1], s)
        merged.append([s, min(e, ed["total"]), b])
    segs = []
    for p in ed["pieces"]:
        a0, a1 = p["t0"], p["t0"] + p["b"] - p["a"]
        cur = a0
        for s, e, b in merged:
            if e <= cur or s >= a1:
                continue
            if s > cur:
                segs.append(dict(kind="talk", piece=p, t0=cur, dur=s - cur))
            cur = max(cur, min(e, a1))
        if a1 > cur + 1e-3:
            segs.append(dict(kind="talk", piece=p, t0=cur, dur=a1 - cur))
    for s, e, b in merged:
        segs.append(dict(kind="broll", cue=b, t0=s, dur=e - s))
    segs.sort(key=lambda x: x["t0"])
    prev = None
    for s in segs:
        s["new_shot"] = prev is None or prev["kind"] != s["kind"] or (s["kind"] == "talk" and s["piece"]["first"]
                                                                      and abs(s["t0"] - s["piece"]["t0"]) < 1e-3)
        prev = s
    return segs


# ---------------------------------------------------------------------------------------------------------------------
# pass A: picture + voice
# ---------------------------------------------------------------------------------------------------------------------
def zoom_expr(base, settle, pushes):
    t = "(on/%d)" % config.FPS
    z = f"{base}"
    if settle:
        a, d = settle
        z += f"*(1+{a}*pow(max(0,1-{t}/{d}),2))"
    for loc, amt in pushes:
        x = f"min(1,max(0,({t}-{loc:.3f})/0.35))"
        z += f"*(1+{amt}*{x}*{x}*(3-2*{x}))"
    return z


def pass_a(ed, title_starts):
    prof, W, H, work = ed["prof"], ed["W"], ed["H"], Path(ed["work"])
    segs = video_segments(ed)
    ed["segments"] = []
    inputs, fc = [], []
    for i, s in enumerate(segs):
        if s["kind"] == "talk":
            p = s["piece"]
            ss = p["a"] + (s["t0"] - p["t0"])
            inputs += ["-ss", f"{ss:.3f}", "-t", f"{s['dur']:.3f}", "-i", p["src"]]
            settle = prof["intro"] if i == 0 else ((0.03, 0.3) if s["new_shot"] else None)
            pushes = [(st - s["t0"], prof["title_push"]) for st in title_starts
                      if prof["title_push"] and -0.35 < st - s["t0"] < s["dur"]]
            z = zoom_expr(p["z"], settle, pushes)
            zmax = p["z"] * (1 + (settle[0] if settle else 0)) * (1 + prof["title_push"]) ** len(pushes)
            fx, fy = 0.5, p["fy"]
            cw, chh = p["copy_w"], p["copy_h"]
            pre = ""
            if (cw > chh) != (W > H):        # a landscape take into a 9:16 ad: crop to the frame's shape first
                pre = f"crop=ih*{W}/{H}:ih:(iw-ow)*{fx},"
                cw = chh * W / H
            mag = W * zmax / cw
            ed["segments"].append(dict(kind="talk", t0=s["t0"], dur=s["dur"], src=Path(p["src"]).name, z=round(zmax, 3),
                                       mag=round(mag, 3)))
        else:
            b = s["cue"]
            info = media.probe(b["src"])
            land = info["width"] > info["height"]
            short = 1920 if land and H > W else config.WORK_SHORT    # 9:16 crop of landscape b-roll stays a downscale
            bsrc, boff = _broll_copy(b["src"], work, short) if min(info["width"], info["height"]) > short else (b["src"], 0)
            ss = b.get("ss", 0.0)
            slow = max(1.0, min(1 / 0.6, s["dur"] / max(0.1, info["duration"] - ss)))   # slowed to 0.6x at most
            inputs += ["-ss", f"{ss:.3f}", "-t", f"{s['dur'] / slow + 0.1:.3f}", "-i", str(bsrc)]
            bi = media.probe(bsrc)
            cw, chh = bi["width"], bi["height"]
            fx = b.get("focus_x", 0.5)
            pre = f"setpts={slow:.3f}*PTS,"
            if (cw > chh) != (W > H):
                pre += f"crop=ih*{W}/{H}:ih:(iw-ow)*{fx},"
                cw = chh * W / H
            scale = min(b.get("scale", 1.0), 1.2)                    # SOP-11e rule 2: b-roll scale <= 1.2
            move = b.get("move", "push")
            settle = (0.04, 0.3) if s["new_shot"] else None
            z = zoom_expr(scale, settle, [(0.0, 0.06)] if move == "push" else [])
            zmax = scale * 1.06 * (1 + (settle[0] if settle else 0))
            fy = 0.5
            ed["segments"].append(dict(kind="broll", t0=s["t0"], dur=s["dur"], src=Path(b["src"]).name, z=round(zmax, 3),
                                       mag=round(W * zmax / cw, 3)))
        light = ",eq=brightness=0.05:enable='lt(t,0.1)'" if s["new_shot"] and i > 0 and s["kind"] == "broll" else ""
        fc.append(f"[{i}:v]setpts=PTS-STARTPTS,{pre}zoompan=z='{z}':x='(iw-iw/zoom)*{fx}':y='(ih-ih/zoom)*{fy}':d=1:"
                  f"s={W}x{H}:fps={config.FPS},setsar=1{light},trim=duration={s['dur']:.3f},setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
    n = len(segs)
    fc.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vout]")
    base = work / "base.mov"
    media.ff(*inputs, "-filter_complex", ";".join(fc), "-map", "[vout]", "-c:v", "libx264", "-crf", "12", "-preset",
             "veryfast", "-pix_fmt", "yuv420p", *BT709, "-r", str(config.FPS), "-t", f"{ed['total']:.3f}", base)
    # voice: SOP-11c T3 per-take gain, highpass 80 Hz, denoise only a noisy take; T1 30 ms fade on every cut
    inputs, fc = [], []
    for i, p in enumerate(ed["pieces"]):
        d = p["b"] - p["a"]
        inputs += ["-ss", f"{p['a']:.3f}", "-t", f"{d:.3f}", "-i", p["src"]]
        dn = "afftdn=nf=-50," if p["denoise"] else ""
        fc.append(f"[{i}:a]aresample=48000,highpass=f=80,{dn}volume={p['gain']}dB,"
                  f"acompressor=threshold=-20dB:ratio=3:attack=5:release=120,"
                  f"afade=t=in:d={config.EDGE_FADE},afade=t=out:st={max(0, d - config.EDGE_FADE):.3f}:d={config.EDGE_FADE}[a{i}]")
    n = len(ed["pieces"])
    fc.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[aout]")
    voice = work / "voice.wav"
    media.ff(*inputs, "-filter_complex", ";".join(fc), "-map", "[aout]", "-c:a", "pcm_s16le", voice)
    return base, voice


def _broll_copy(src, work, short):
    out = Path(work) / "proxy" / f"{Path(src).stem}.broll{short}.mov"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        info = media.probe(src)
        scale = f"scale=-2:{short}" if info["width"] > info["height"] else f"scale={short}:-2"
        media.ff("-i", src, "-vf", f"{scale}:flags=lanczos:out_range=tv,fps={config.FPS},format=yuv420p", "-c:v", "libx264",
                 "-crf", str(config.WORK_CRF), "-preset", "veryfast", "-an", out)
    return out, 0.0


# ---------------------------------------------------------------------------------------------------------------------
# align, analyse
# ---------------------------------------------------------------------------------------------------------------------
def align_captions(ed, voice):
    """Caption times from a transcript of the cut's own voice track; text stays the proofread text."""
    a = media.audio16k(voice)
    heard = transcribe.words_from_audio(a, ed["prof"]["whisper"])
    proof = [list(w) for w in ed["words"]]
    sm = difflib.SequenceMatcher(a=[textutil.norm(w[2]) for w in proof], b=[textutil.norm(w[2]) for w in heard],
                                 autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            proof[blk.a + k][0], proof[blk.a + k][1] = heard[blk.b + k][0], heard[blk.b + k][1]
    # words the cut's audio holds that the take's transcript missed (a clipped first word, a quick "So"): caption them
    fixed = textutil.apply_fixes([list(w) for w in heard], ed["plan"].get("fix", {}))
    fset = {textutil.norm(w[2]) for w in fixed}
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "insert":
            for w in heard[j1:j2]:
                if textutil.norm(w[2]) in fset and textutil.norm(w[2]) not in ("um", "uh"):
                    proof.append([w[0], w[1], w[2], -1])
    proof.sort(key=lambda w: w[0])
    ed["caption_words"] = proof
    ed["heard_cut"] = heard


def analyse(ed, base, times, need_matte):
    root = ed["plan"]["_root"]
    try:
        from . import matte
        seg = matte.Segmenter(root)
    except Exception as e:  # mediapipe missing: titles fall back to their default heights, QA says so
        ed["faces"] = {}
        ed["face_note"] = f"face check skipped ({type(e).__name__}: {e})"
        return None
    ed["faces"] = matte.face_boxes(seg, base, times, media.frame)
    mpath = None
    if need_matte:
        mpath = Path(ed["work"]) / "matte.mov"
        log("  matting the speaker for titles behind him")
        matte.matte_video(seg, base, mpath, (ed["W"], ed["H"]), config.FPS)
    seg.close()
    return mpath


def face_over(ed, a, b):
    """Worst-case face box over [a, b]: the lowest chin and the highest head top."""
    boxes = [v for k, v in ed.get("faces", {}).items() if v and a - 0.3 <= float(k) <= b + 0.3]
    if not boxes:
        return None
    return dict(top=min(x["top"] for x in boxes), chin=max(x["chin"] for x in boxes))


# ---------------------------------------------------------------------------------------------------------------------
# titles
# ---------------------------------------------------------------------------------------------------------------------
DEFAULT_Y = {
    "agency": dict(combo=0.43, stack=0.50, number=0.48, cta=0.525),
    "client": dict(combo=0.42, headline=0.42, number=0.42, tag=0.33, cta=0.30, offer=0.615),
    "breakout": dict(H=0.41, card=0.42, check=0.42),
}
EXTENT = {  # (above y, below y) as multiples of the first line's size
    "combo": (0.45, 1.45), "headline": (0.45, 1.45), "stack": (0.45, 1.6), "number": (0.45, 0.85), "cta": (0.45, 1.85),
}


def schedule(ed):
    """Locate each title on his words, apply the figure-was-said rule, keep one title at a time per lane."""
    plan, kind = ed["plan"], ed["plan"]["kind"]
    words = ed["caption_words"]
    cues, skipped = [], []

    def at(say, nth=0, after=None):
        r = textutil.find_phrase(words, say, nth, after)
        return (words[r[0]][0], words[r[1] - 1][1]) if r else None

    for t in plan["titles"]:
        style = t["style"]
        if style in ("check",):
            items = []
            for it in t["items"]:
                hit = at(it["say"]) if it.get("say") else None
                items.append(dict(it, t=hit[0] if hit else None))
            first = at(t["say"]) if t.get("say") else (items[0]["t"], items[0]["t"])
            if not first or first[0] is None:
                skipped.append((style, t.get("say"), "words not heard"))
                continue
            last = max([i["t"] for i in items if i["t"] is not None] + [first[1]])
            cues.append(dict(t, start=first[0], end=last + t.get("hold", 2.0), items=items, lane="main"))
            continue
        hit = at(t["say"], t.get("nth", 0), t.get("after")) if t.get("say") else None
        if t.get("say") and not hit:
            skipped.append((style, t["say"], "words not heard"))
            continue
        lines = t.get("lines", [])
        start = t.get("at", hit[0] if hit else 0.08)
        end = (hit[1] if hit else start + 1.5) + t.get("hold", 0.45)
        # README rule: a figure on screen must have been said, in the title's words or while it is up
        said = " ".join(w[2] for w in words if (hit and hit[0] - 0.05 <= w[0] <= hit[1] + 0.05) or start - 0.2 <= w[0] <= end)
        ok, missing = textutil.figure_said(lines, said)
        if not ok:
            skipped.append((style, " / ".join(lines), f"figure {', '.join(missing)} not said (README rule)"))
            continue
        lane = "tag" if style == "tag" else "behind" if t.get("behind") or style in ("callout", "big") else "main"
        if kind == "client":
            lane = "tag" if style == "tag" else "main"     # every headline, behind or in front, one at a time
        cues.append(dict(t, start=start, end=max(end, start + 1.2), lane=lane, said=said))
    cta = plan.get("cta")
    if cta:
        hit = at(cta["say"], cta.get("nth", -1))
        if hit:
            cues.append(dict(style="cta", lines=cta.get("lines", ["TAP", "GET OFFER"]), start=hit[0], end=ed["total"],
                             lane="main", say=cta["say"]))
        else:
            skipped.append(("cta", cta["say"], "words not heard"))
    cues.sort(key=lambda c: c["start"])
    for lane in {c["lane"] for c in cues}:
        lc = [c for c in cues if c["lane"] == lane]
        for a, b in zip(lc, lc[1:]):
            if a["end"] > b["start"] - config.TITLE_GAP:
                a["end"] = b["start"] - config.TITLE_GAP
    for c in cues:
        if c["end"] - c["start"] < 0.6:
            c["flag"] = "on screen under 0.6 s"
    ed["cues"], ed["skipped"] = cues, skipped
    return cues


def place(ed, c, size_first):
    """Preferred height, then pushed under the chin (never over the face) and kept inside Meta's safe zone."""
    H, kind = ed["H"], ed["plan"]["kind"]
    y = c.get("y", DEFAULT_Y.get(kind, {}).get(c["style"], 0.45)) * H
    if kind == "breakout":
        return y
    up, down = (e * size_first for e in EXTENT.get(c["style"], (0.6, 1.0)))
    face = face_over(ed, c["start"], min(c["end"], c["start"] + 1.5))
    lo, hi = config.SAFE_TOP * H + up, (1 - config.SAFE_BOTTOM) * H - down
    c["k"] = 1.0
    if face:
        clear = (face["chin"] + config.FACE_CLEAR) * H + up
        if y < clear:
            if clear <= hi + 0.04 * H:
                y = clear
            else:
                above = face["top"] * H - config.FACE_CLEAR * H - down
                if above >= lo:
                    y = above
                else:
                    # shrink the title into the band between his chin and Meta's bottom bar
                    band = (1 - config.SAFE_BOTTOM + 0.02) * H - (face["chin"] + config.FACE_CLEAR) * H
                    k = max(0.55, min(1.0, band / (up + down)))
                    c["k"] = round(k, 2)
                    y = (face["chin"] + config.FACE_CLEAR) * H + up * k
                    if k * (up + down) > band + 1:
                        c["flag"] = "no room clear of the face"
    if y + down * c["k"] > (1 - config.SAFE_BOTTOM + 0.04) * H:
        c["flag"] = c.get("flag") or "below Meta's safe zone"
    c["y_final"] = round(y / H, 3)
    return y


def draw(ed, faces):
    plan, prof, kind = ed["plan"], ed["prof"], ed["plan"]["kind"]
    W, H = ed["W"], ed["H"]
    brand = plan.get("brand", {})
    pal = {k: (tuple(v) if isinstance(v, list) else v) for k, v in brand.get("palette", {}).items()}
    c = looks.Canvas((W, H), faces, prof["glow"], pal)
    color = brand.get("color", "#2E8BFF")
    sfx = []
    first_title = True
    for cue in ed["cues"]:
        st, en, lines, style = cue["start"], cue["end"], cue.get("lines", []), cue["style"]
        if kind == "agency":
            if style == "combo":
                y = place(ed, cue, fonts.fit(faces, "head", lines[0], 150, 0.9 * W))
                looks.agency_combo(c, st, en, y, lines, cue.get('k', 1.0), cue.get('script_font', 'script'))
            elif style == "stack":
                y = place(ed, cue, fonts.fit(faces, "head", lines[0], 220, 0.86 * W))
                looks.agency_stack(c, st, en, y, lines, cue.get('k', 1.0))
            elif style == "number":
                y = place(ed, cue, fonts.fit(faces, "head", lines[0], 300, 0.82 * W))
                looks.agency_number(c, st, en, y, lines, cue.get('k', 1.0))
            elif style == "cta":
                y = place(ed, cue, 150)
                looks.cta(c, st, en, y, lines, k=cue.get('k', 1.0))
            else:
                cue["flag"] = f"style {style} is not an agency style"
                continue
        elif kind == "client":
            if style in ("combo", "headline"):
                y = place(ed, cue, fonts.fit(faces, "head", lines[0], 150, 0.9 * W))
                looks.agency_combo(c, st, en, y, lines, cue.get('k', 1.0), cue.get('script_font', 'script'))
            elif style == "number":
                y = place(ed, cue, fonts.fit(faces, "head", lines[0], 280, 0.8 * W))
                looks.client_number(c, st, en, y, lines)
            elif style in ("callout", "big"):
                face = face_over(ed, st, st + 1.0)
                y_head = cue.get("y_head", 0.045)
                y = ((face["top"] if face else 0.20) + y_head) * H
                y = max(y, config.SAFE_TOP * H + 0.06 * H)
                cue["y_final"] = round(y / H, 3)
                if style == "callout":
                    looks.client_callout(c, st, en, y, lines, cue.get("color", color))
                else:
                    looks.client_big(c, st, en, y, lines)
            elif style == "tag":
                looks.client_tag(c, st, min(en, st + cue.get("dur", 1.25)), cue.get("y", 0.33) * H, lines, color)
            elif style == "cta":
                y = place(ed, cue, 150)
                looks.cta(c, st, en, y, lines, second=pal.get("bright", config.GOLD), k=cue.get('k', 1.0))
            else:
                cue["flag"] = f"style {style} is not a client style"
                continue
        else:
            y = cue.get("y", DEFAULT_Y["breakout"].get(style, 0.42)) * H
            if style == "H":
                looks.bo_headline(c, st, en, y, lines)
            elif style == "card":
                looks.bo_card(c, st, en, y, lines)
            elif style == "check":
                looks.bo_check(c, st, en, y, cue.get("title", ""), cue["items"], cue.get("mode", "check"))
            elif style == "tag":
                looks.bo_tag(c, st, en, lines)
            elif style == "cta":
                looks.cta(c, st, en, 0.42 * H, lines)
            else:
                cue["flag"] = f"style {style} is not a breakout style"
                continue
        kind_sfx = "tap" if style == "cta" else ("hit" if first_title else "pop")
        sfx.append((st, kind_sfx))
        first_title = False
    # client offer (SOP-11b rule 5): from 0.15 s, back on each spoken offer line, held from the close to the end
    offer = plan.get("offer")
    if offer and kind == "client":
        wins = [(0.15, min(ed["total"], 0.15 + offer.get("first_hold", 2.6)))]
        for say in offer.get("say", []):
            r = textutil.find_phrase(ed["caption_words"], say)
            if r:
                wins.append((ed["caption_words"][r[0]][0], ed["caption_words"][r[1] - 1][1] + 1.2))
        cta = next((q for q in ed["cues"] if q["style"] == "cta"), None)
        if cta:
            wins.append((cta["start"], ed["total"]))
        looks.client_offer(c, wins, offer.get("y", 0.615) * H, offer["lines"])
        for w in wins:
            sfx.append((w[0], "shimmer"))
        ed["offer_windows"] = wins
    # end card (agency): his take steps back, the wordmark lands above the CTA (SOP-3D §6.5)
    ed["end_card_at"] = None
    if plan.get("end_card", prof["end_card"]):
        last = max(w[1] for w in ed["caption_words"])
        start = min(last + 0.55, ed["total"] - 0.8)
        looks.end_card(c, start, ed["total"], brand)
        ed["end_card_at"] = start
        sfx.append((start, "pop"))
    # captions
    front = [(q["start"], q["end"]) for q in ed["cues"] if q["lane"] in ("main", "behind")]
    hidden = (lambda a, b: False) if prof["captions_under_titles"] else (
        lambda a, b: any(s - 0.05 < b and a < e + 0.05 for s, e in front) or (ed["end_card_at"] is not None and b > ed["end_card_at"]))

    def cap_y(t):
        y = prof["caption_y"] * H
        if kind == "breakout":
            return y
        face = face_over(ed, t, t + 0.8)
        if face:
            y = max(y, (face["chin"] + config.FACE_CLEAR) * H + 45)
        return min(y, (1 - config.SAFE_BOTTOM) * H - 40)

    looks.captions(c, ed["caption_words"], prof["captions"], cap_y, hidden, end_at=ed["total"])
    work = Path(ed["work"])
    looks.write(c, work / "behind.ass", c.behind)
    looks.write(c, work / "front.ass", c.front + c.caps)
    ed["sfx"] = sfx
    ed["has_behind"] = bool(c.behind)
    return work / "behind.ass", work / "front.ass"


# ---------------------------------------------------------------------------------------------------------------------
# pass B
# ---------------------------------------------------------------------------------------------------------------------
def pass_b(ed, base, voice, mattef, behind, front, faces_dir, still=None):
    prof, plan, W, H, work = ed["prof"], ed["plan"], ed["W"], ed["H"], Path(ed["work"])
    total = ed["total"]
    fd = media.filter_path(faces_dir)
    inputs = ["-i", base]
    fc = []
    v = "[0:v]"
    trim = f"trim=start={still:.3f}:duration=0.04," if still is not None else ""
    fc.append(f"{v}{trim}setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv[v0]")
    v = "[v0]"
    if ed.get("has_behind"):
        fc.append(f"{v}split[bg][fg]")
        fc.append(f"[bg]ass={media.filter_path(behind)}:fontsdir={fd}[bt]")
        if mattef:
            inputs += ["-i", mattef]
            fc.append(f"[1:v]{trim}format=gray[m]")
            fc.append("[fg][m]alphamerge[person]")
            fc.append("[bt][person]overlay=format=auto[comp]")
        else:
            fc.append("[fg]nullsink")
            fc.append("[bt]null[comp]")
        v = "[comp]"
    if ed.get("end_card_at") is not None:
        e = ed["end_card_at"]
        fc.append(f"{v}split[c1][c2]")
        fc.append(f"[c2]trim=start={e:.3f},setpts=PTS-STARTPTS,gblur=sigma=18,eq=brightness=-0.16:saturation=0.8,"
                  f"format=yuva420p,fade=t=in:st=0:d=0.35:alpha=1,setpts=PTS+{e:.3f}/TB[card]")
        fc.append("[c1][card]overlay=eof_action=pass[ec]")
        v = "[ec]"
        logo = plan.get("brand", {}).get("logo")
        if logo:
            lp = logo_for_dark(Path(plan["_root"]) / logo, work, plan["brand"].get("logo_on_dark", True))
            inputs += ["-loop", "1", "-t", f"{total:.3f}", "-i", lp]
            li = inputs.count("-i") - 1
            fc.append(f"[{li}:v]scale={int(W * 0.62)}:-1,format=rgba,fade=t=in:st={e + 0.1:.3f}:d=0.3:alpha=1[logo]")
            fc.append(f"{v}[logo]overlay=(W-w)/2:{int(0.26 * H)}-h/2:enable='gte(t,{e:.3f})'[lg]")
            v = "[lg]"
    tail = ""
    if ed["plan"]["kind"] == "breakout" and still is None:
        tail = f",fade=t=out:st={total - 0.6:.3f}:d=0.6"
    fc.append(f"{v}ass={media.filter_path(front)}:fontsdir={fd}{tail},format=yuv420p[vout]")
    if still is not None:
        out = Path(ed["out"]) / f"still_{still:.2f}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        media.ff(*inputs, "-filter_complex", ";".join(fc), "-map", "[vout]", "-frames:v", "1", out)
        return out
    # sound: voice + our own bed, ducked while he talks, soft sfx; -14 LUFS with a limiter (NOTES "Sound")
    k = inputs.count("-i")
    bed_path = work / "bed.wav"
    music = prof["music"]
    sound.write(bed_path, sound.bed(total + 1, music.get("style", "soul"), plan["name"], music.get("key"), music.get("bpm")))
    inputs += ["-i", voice, "-i", bed_path]
    vi, bi = k, k + 1
    k += 2
    fc.append(f"[{vi}:a]asplit=2[vmix][vkey]")
    fc.append(f"[{bi}:a]atrim=0:{total:.3f},afade=t=out:st={max(0, total - 1.2):.3f}:d=1.2,volume={music.get('db', -16)}dB[bedraw]")
    fc.append("[bedraw][vkey]sidechaincompress=threshold=0.03:ratio=5:attack=15:release=350:makeup=1[bedduck]")
    labels = []
    gains = dict(hit=0.35, pop=0.2, tap=0.35, shimmer=0.3, whoosh=0.25)
    for j, (st, kind) in enumerate(ed["sfx"]):
        p = work / f"sfx_{kind}.wav"
        if not p.exists():
            sound.write(p, sound.sfx(kind))
        inputs += ["-i", p]
        fc.append(f"[{k}:a]volume={gains[kind]},adelay={int(st * 1000)}|{int(st * 1000)}[s{j}]")
        labels.append(f"[s{j}]")
        k += 1
    fade = f",afade=t=out:st={total - 0.6:.3f}:d=0.6" if ed["plan"]["kind"] == "breakout" else ""
    fc.append(f"[vmix][bedduck]{''.join(labels)}amix=inputs={2 + len(labels)}:normalize=0:duration=first,"
              f"atrim=0:{total:.3f}{fade}[aout]")
    raw = work / "raw.mov"
    media.ff(*inputs, "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264",
             "-profile:v", "high", "-preset", "slow", "-tune", "film", "-crf", str(config.MASTER_CRF), "-pix_fmt", "yuv420p",
             *BT709, "-r", str(config.FPS), "-c:a", "pcm_s16le", "-t", f"{total:.3f}", raw)
    return raw


def logo_for_dark(src, work, on_dark=True):
    """The brand logo for the end card: transparent margins trimmed, and, because the card is his blurred and darkened
    take, near-black ink turned white so a black wordmark stays readable (brand colours are kept as they are)."""
    from PIL import Image
    out = Path(work) / "logo_end_card.png"
    im = Image.open(src).convert("RGBA")
    a = np.asarray(im).copy()
    if on_dark:
        rgb = a[..., :3].astype(int)
        dark = (rgb.max(axis=2) < 90) & (a[..., 3] > 0)
        a[dark, :3] = 255
    ys, xs = np.where(a[..., 3] > 10)
    if len(ys):
        a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    Image.fromarray(a).save(out)
    return out


def finish(ed, raw, phone=True):
    """Loudness to -14 LUFS with the true peak under -1 dBFS (measure, gain, limit, re-measure), then the master and
    the two-pass BT.709 phone copy (SOP-11e rules 6-7)."""
    out = Path(ed["out"])
    out.mkdir(parents=True, exist_ok=True)
    name = slug(ed["plan"]["name"])
    master = out / f"{name}.master.mp4"
    i0, _ = media.loudness(raw)
    gain = config.LUFS - i0
    for _ in range(5):
        media.ff("-i", raw, "-c:v", "copy", "-af",
                 f"volume={gain:.2f}dB,aresample=192000,alimiter=limit=0.8:level=false:attack=3:release=60,aresample=48000",
                 "-c:a", "aac", "-b:a", config.MASTER_AUDIO, "-movflags", "+faststart", master)
        i1, p1 = media.loudness(master)
        if abs(i1 - config.LUFS) <= 0.3 and p1 <= config.PEAK_MAX:
            break
        gain += config.LUFS - i1
    ed["master"] = str(master)
    if phone:
        dur = media.probe(master)["duration"]
        kbps = int(config.PHONE_MB * 8 * 1024 / dur) - 160
        ph = out / f"{name}.phone.mp4"
        log_ = Path(ed["work"]) / "x264pass"
        media.ff("-i", master, "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbps}k", "-pass", "1", "-passlogfile", log_,
                 "-pix_fmt", "yuv420p", *BT709, "-an", "-f", "null", "-")
        media.ff("-i", master, "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbps}k", "-pass", "2", "-passlogfile", log_,
                 "-pix_fmt", "yuv420p", *BT709, "-af", "volume=-0.5dB,aresample=192000,alimiter=limit=0.74:level=false,aresample=48000",
                 "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", ph)
        ed["phone"] = str(ph)
    return master


# ---------------------------------------------------------------------------------------------------------------------
def save(ed):
    keep = {k: v for k, v in ed.items() if k not in ("plan", "prof")}
    keep["plan_name"] = ed["plan"]["name"]
    keep["kind"] = ed["plan"]["kind"]
    Path(ed["work"], "timeline.json").write_text(json.dumps(keep, indent=1, default=float))


def render(plan_path, plan_only=False, still=None, phone=True, qa=True):
    plan = load(plan_path)
    log(f"{plan['name']}  [{plan['kind']}]")
    ed = build(plan)
    ed["out"] = str(Path(plan["_root"]) / "out" / slug(plan["name"]))
    faces = fonts.ensure(ed["work"])
    if plan_only:
        ed["caption_words"] = ed["words"]
        schedule(ed)
        print_plan(ed)
        return ed
    with render_lock(plan["_root"], wait=True) if still is None else _null():
        # a first schedule on the proofread words gives the push-in times; the real one runs on the aligned words
        ed["caption_words"] = ed["words"]
        rough = schedule(ed)
        log("  pass A: picture and voice")
        base, voice = pass_a(ed, [c["start"] for c in rough if c["style"] != "cta"] +
                             [c["start"] for c in rough if c["style"] == "cta"])
        log("  aligning captions to the cut's audio")
        align_captions(ed, voice)
        schedule(ed)
        times = sorted({round(x, 2) for x in np.arange(0.05, ed["total"], 0.5)} |
                       {round(c["start"] + 0.25, 2) for c in ed["cues"]})
        need_matte = any(c["lane"] == "behind" or c["style"] in ("callout", "big") for c in ed["cues"])
        log("  reading his face position for every title")
        mattef = analyse(ed, base, times, need_matte)
        behind, front = draw(ed, faces)
        if still is not None:
            p = pass_b(ed, base, voice, mattef, behind, front, Path(faces["head"][0]).parent, still=still)
            log(f"  still: {p}")
            return ed
        log("  pass B: titles, captions, music, mix")
        raw = pass_b(ed, base, voice, mattef, behind, front, Path(faces["head"][0]).parent)
        log("  loudness, master and phone copy")
        finish(ed, raw, phone)
        save(ed)
    if qa:
        from . import qa as qa_mod
        qa_mod.run(ed)
    return ed


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def print_plan(ed):
    print(f"\n  {len(ed['pieces'])} pieces, {ed['total']:.2f} s")
    for p in ed["pieces"]:
        print(f"    {Path(p['src']).name:28s} {p['a']:8.2f}-{p['b']:8.2f} -> {p['t0']:6.2f}  z{p['z']}")
    for r in ed["reports"]:
        for c in r["cut"]:
            print(f"    cut on the floor ({r['src']} {c[0]}): '{c[1]}' [{c[2]}]")
        for x in r["retakes"]:
            print(f"    retake? ({r['src']}) {x['verdict']} j={x['jaccard']} at {x['first']:.1f}/{x['second']:.1f}: {x['text']}")
    print("  titles:")
    for c in ed["cues"]:
        print(f"    {c['start']:6.2f}-{c['end']:6.2f} {c['style']:7s} {' / '.join(c.get('lines', []))}  {c.get('flag', '')}")
    for s in ed["skipped"]:
        print(f"    ! skipped {s[0]} '{s[1]}': {s[2]}")
