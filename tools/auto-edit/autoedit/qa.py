"""QA before anything is sent (NOTES "before delivery"; SOP-11b §4; SOP-11 §9 self-check; SOP-11e §4; SOP-3D §7;
SOP-11c T1 edge flags, T2 retakes, T5 phone page). Writes QA.md, a contact sheet, a phone page and proof stills.

Lines that must read clean before an ad goes out: loudness -14, peak <= -1, caption sync median under 20 ms,
repeats none, cues skipped none, bt709 x3, edges flagged none, titles clear of the face.
"""
import difflib
import re
import subprocess
from pathlib import Path

import numpy as np

from . import config, cut, media, textutil, transcribe


def _row(ok, check, rule, value):
    mark = {True: "pass", False: "FAIL", None: "check"}[ok]
    return f"| {check} | {rule} | {value} | {mark} |"


def edge_flags(voice, pieces, ed):
    """SOP-11c T1 on the voice track: a cut inside a word, a leftover fragment, an edge under 30 ms from sound."""
    a = media.audio16k(voice)
    ts, env = cut.rms_track(a, 0, len(a) / media.SR)
    thr = np.full(len(env), cut.threshold(env))
    for lo, hi, g in section_gates(ed):
        thr[(ts >= lo) & (ts < hi)] = g
    loud = env > thr
    flags = []
    t = 0.0
    for p in pieces[1:]:
        t = p["t0"]
        i = int(round(t / cut.HOP))
        win = loud[max(0, i - 15):i + 15]
        near = [abs(k - i) * cut.HOP for k in range(max(0, i - 15), min(len(loud), i + 15)) if loud[k]]
        if win.sum() >= 15 and near and min(near) <= 0.08:
            flags.append((round(t, 2), "cut inside a word"))
        elif near and min(near) < 0.03:
            flags.append((round(t, 2), "edge under 30 ms from sound"))
        # leftover fragment: < 0.35 s of sound right after the cut, then 0.2 s+ of silence
        j = i
        while j < len(loud) and loud[j]:
            j += 1
        if 0 < (j - i) * cut.HOP < 0.35 and j + 20 < len(loud) and not loud[j:j + 20].any() and loud[i]:
            flags.append((round(t, 2), f"fragment {(j - i) * cut.HOP:.2f} s after the cut"))
    return flags


def section_gates(ed):
    """Each take's silence gate on the voice track: the cutter's own threshold (measured with the take's margins, as
    SOP-11c T4 needs some silence in the window) shifted by the take's T3 gain."""
    gates = {}
    for r in ed.get("reports", []):
        gates[r["section"]] = r["threshold_db"] + r["gain_db"]
    spans = {}
    for p in ed["pieces"]:
        lo, hi = spans.get(p["section"], (p["t0"], p["t0"]))
        spans[p["section"]] = (min(lo, p["t0"]), max(hi, p["t0"] + p["b"] - p["a"]))
    return [(lo, hi, 10 ** (gates.get(sec, -60) / 20)) for sec, (lo, hi) in spans.items()]


def long_pauses(voice, limit, until, ed):
    """Dead air on the voice track, each take judged by its own gate."""
    a = media.audio16k(voice)
    runs = []
    for lo, hi, thr in section_gates(ed):
        ts, env = cut.rms_track(a, lo, hi)
        run = None
        for t, e in zip(ts, env):
            if e < thr and run is None:
                run = t
            if e >= thr and run is not None:
                if t - run > limit and run > 0.3 and t < until:
                    runs.append((round(run, 2), round(t - run, 2)))
                run = None
    return sorted(runs)


def sheets(video, out, total, n=21, cols=7):
    from PIL import Image
    tmp = Path(out) / "_frames"
    tmp.mkdir(parents=True, exist_ok=True)
    times = np.linspace(0.3, max(0.4, total - 0.2), n)
    ims = []
    for k, t in enumerate(times):
        p = tmp / f"f{k:02d}.png"
        media.ff("-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf",
                 "scale=360:-2,drawtext=text='%.1f':x=8:y=8:fontsize=26:fontcolor=yellow:box=1:boxcolor=black@0.5" % t, p)
        ims.append(Image.open(p).convert("RGB"))
    w, h = ims[0].size
    sheet = Image.new("RGB", (cols * w, -(-len(ims) // cols) * h))
    for k, im in enumerate(ims):
        sheet.paste(im, ((k % cols) * w, (k // cols) * h))
    full = Path(out) / "contact_sheet.jpg"
    sheet.save(full, quality=88)
    phone = sheet.resize((sheet.width // 2, sheet.height // 2))   # SOP-11c T5: the half-size page reviews are read on a phone
    phone.save(Path(out) / "contact_sheet_phone.jpg", quality=85)
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()
    return full


def run(ed):
    out = Path(ed["out"])
    master, phone = ed["master"], ed.get("phone")
    kind = ed["kind"] if "kind" in ed else ed["plan"]["kind"]
    prof = config.PROFILES[kind]
    plan = ed.get("plan", {})
    total = ed["total"]
    rows, fails = [], 0

    def add(ok, *a):
        nonlocal fails
        rows.append(_row(ok, *a))
        fails += ok is False

    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                         "stream=width,height,r_frame_rate,profile,color_space,color_primaries,color_transfer", "-of",
                         "default=nw=1", master], capture_output=True, text=True).stdout
    kv = dict(l.split("=", 1) for l in pr.split() if "=" in l)
    W, H = ed["W"], ed["H"]
    add(kv.get("width") == str(W) and kv.get("height") == str(H) and kv.get("r_frame_rate") == "30/1",
        "Size and rate", "SOP-11a rule 1", f"{kv.get('width')}x{kv.get('height')} @ {kv.get('r_frame_rate')}")
    tags = [kv.get("color_space"), kv.get("color_primaries"), kv.get("color_transfer")]
    add(all(t == "bt709" for t in tags), "Colour tags", "SOP-11e rule 5", " / ".join(map(str, tags)))
    add(kv.get("profile") == "High", "H.264 profile", "SOP-11e rule 6", kv.get("profile"))
    i, p = media.loudness(master)
    add(abs(i - config.LUFS) <= 0.5 and p <= config.PEAK_MAX, "Master loudness", "-14 LUFS, peak <= -1 dBFS",
        f"{i:.1f} LUFS, {p:.1f} dBFS")
    mb = Path(master).stat().st_size / 1e6
    add(None, "Master file", "SOP-11e rule 7: the master is what goes to Meta", f"{mb:.0f} MB")
    if phone:
        pmb = Path(phone).stat().st_size / 1e6
        pi, pp = media.loudness(phone)
        add(pmb < 30 and pp <= config.PEAK_MAX, "Phone copy", "two-pass, BT.709, < 30 MB, preview only", f"{pmb:.1f} MB, {pi:.1f} LUFS, {pp:.1f} dBFS")
    mags = [s["mag"] for s in ed.get("segments", [])]
    if mags:
        add(max(mags) <= 1.05, "Magnification", "SOP-11e rule 2: crop x punch x move <= copy / 1080 (no upscale)",
            f"max {max(mags):.2f} (1.00 = one source pixel per output pixel)")

    cues = ed.get("cues", [])
    titles = [c for c in cues if c["style"] != "cta"]
    if kind in ("agency", "client"):
        first = min([c["start"] for c in titles], default=99)
        add(first <= config.HOOK_WINDOW, "Hook title", "README / SOP-3D §0: inside the first 1.5 s", f"{first:.2f} s")
    skipped = ed.get("skipped", [])
    add(not skipped, "Cues skipped", "every cue pinned to words he says",
        "none" if not skipped else "; ".join(f"{s[0]} '{s[1]}': {s[2]}" for s in skipped))
    flags = [f"{c['style']} @ {c['start']:.1f}: {c['flag']}" for c in cues if c.get("flag")]
    add(not flags, "Title placement", "never over the face; inside Meta's safe zone; one at a time",
        "clean" if not flags else "; ".join(flags))
    if ed.get("face_note"):
        add(None, "Face check", "person matte", ed["face_note"])
    if kind == "client":
        heads = [c for c in titles if c["style"] in ("combo", "headline", "number", "callout", "big")]
        tags_ = [c for c in titles if c["style"] == "tag"]
        add(len(heads) <= prof["max_headlines"], "Headlines", "SOP-11b rule 7: at most 3", str(len(heads)))
        add(len(tags_) <= prof["max_tags"], "Tags", "SOP-11b rule 6: at most 4", str(len(tags_)))
        segs = ed.get("segments", [])
        add(not segs or segs[-1]["kind"] == "talk", "Ends on the speaker", "NOTES: no b-roll after the CTA, tail 0.8",
            segs[-1]["kind"] if segs else "-")

    # re-hear the finished file: repeats and caption sync (README "one thing that will bite again")
    a = media.audio16k(master)
    heard = transcribe.words_from_audio(a, ed.get("whisper") or prof["whisper"])
    toks = [textutil.norm(w[2]) for w in heard]
    reps = [" ".join(toks[k:k + 2 * n]) for k, n in textutil.repeats(toks, config.REPEAT_MAX_N, plan.get("protect", []))
            if not re.search(r"[.?!]$", heard[k + n - 1][2])]
    add(not reps, "Repeats heard", "NOTES: must read none", "none" if not reps else "; ".join(reps))
    capw = ed["caption_words"]
    d, j = [], 0
    for w in capw:
        wn = textutil.norm(w[2])
        for k in range(j, len(heard)):
            if textutil.norm(heard[k][2]) == wn and abs(heard[k][0] - w[0]) < 1.0:
                d.append(abs(heard[k][0] - w[0]) * 1000)
                j = k + 1
                break
    lim = config.SYNC_MEDIAN_MS_LANDSCAPE if kind == "breakout" else config.SYNC_MEDIAN_MS
    med = float(np.median(d)) if d else 999
    add(med < lim, "Caption sync", f"median under {lim} ms", f"median {med:.0f} ms, p90 {np.percentile(d, 90) if d else 0:.0f} ms, "
        f"{len(d)}/{len(capw)} words")
    # every word he says on the finished cut has a caption (SOP-3D §7: captions carry the ad for muted viewers)
    heard_fixed = textutil.apply_fixes([list(w) for w in heard], plan.get("fix", {}))
    heard = heard_fixed
    stemmed = lambda ws: [textutil.stem(textutil.norm(w[2])) for w in ws]
    sm = difflib.SequenceMatcher(a=stemmed(heard), b=stemmed(capw), autojunk=False)
    fill = {"um", "uh", "bye", "okay", "ok"}
    missing, differ = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "delete":      # heard on the cut, no caption in that slot at all
            missing += [f"{heard[k][0]:.1f}s '{heard[k][2]}'" for k in range(i1, i2)
                        if textutil.norm(heard[k][2]) not in fill and heard[k][0] < total - 0.3]
        elif op == "replace":   # a caption is there; the QA pass heard it differently (music, accent)
            differ.append(f"{heard[i1][0]:.1f}s heard '{' '.join(w[2] for w in heard[i1:i2])}' / "
                          f"captioned '{' '.join(w[2] for w in capw[j1:j2])}'")
    add(not missing, "Caption coverage", "every spoken word captioned (SOP-3D §7)",
        "complete" if not missing else "; ".join(missing))
    if differ:
        add(None, "Heard differently", "listen at these times: a real mis-caption or a QA mishearing", "; ".join(differ))
    voice = Path(ed["work"]) / "voice.wav"
    last = max(w[1] for w in capw)
    pauses = long_pauses(voice, prof["max_pause"], last, ed)
    add(not pauses, "Dead air", f"no pause over {prof['max_pause']} s", "none" if not pauses else
        ", ".join(f"{t}s ({g}s)" for t, g in pauses))
    ef = edge_flags(voice, ed["pieces"], ed)
    add(not ef, "Edges flagged", "SOP-11c T1", "none" if not ef else "; ".join(f"{t}s {why}" for t, why in ef))

    # self-check line (SOP-11 §8.5) and truth on screen (SOP-11b §2, SOP-11 §9 items 1-5)
    card = plan.get("card", {})
    shown = " ".join(" ".join(c.get("lines", [])) for c in cues) + " " + " ".join(plan.get("offer", {}).get("lines", []))
    said_prices = sorted(set(re.findall(r"\$\d[\d,]*", " ".join(w[2] for w in capw))))
    shown_prices = sorted(set(re.findall(r"\$\d[\d,]*", shown)))
    if card.get("no_price") and kind == "client":
        add(not shown_prices and not said_prices, "No-price account", "SOP-11 §9 item 3: no price anywhere",
            "none shown" if not shown_prices else ", ".join(shown_prices))
    if kind == "client":
        line = (f"price said: {', '.join(said_prices) or '-'} · headline: {', '.join(shown_prices) or '-'} · "
                f"card: {card.get('price') or '-'} · owner: {card.get('owner') or '-'} · area: {card.get('area') or '-'} · "
                f"services off respected: {'check' if card.get('services_off') else 'y'}")
        ok = set(shown_prices) <= set(said_prices) and (not card.get("price") or not shown_prices or card["price"] in shown_prices)
        add(ok, "Self-check", "SOP-11 §8.5: spoken = headline = card", line)

    # nothing but words on screen: no ASS override codes leaking into the text (the 10/24 "\\alpha" escape)
    leaks = []
    for f in ("front.ass", "behind.ass"):
        fp = Path(ed["work"]) / f
        if fp.exists():
            for line in fp.read_text(encoding="utf-8").splitlines():
                if line.startswith("Dialogue:"):
                    text = re.sub(r"\{[^}]*\}", "", line.split(",", 9)[9])
                    if "\\" in text.replace("\\N", "") or "&H" in text or "(\\" in text:
                        leaks.append(text[:40])
    add(not leaks, "Text on screen", "no formatting codes shown as text", "clean" if not leaks else "; ".join(leaks[:3]))
    claims = textutil.claims(capw)
    retakes = [x for r in ed.get("reports", []) for x in r.get("retakes", [])]
    floor = [f"{r['src']} {c[0]}s '{c[1]}' ({c[2]})" for r in ed.get("reports", []) for c in r.get("cut", [])]
    stretched = [f"{r['src']} {x[0]}s '{x[1]}' -> '{x[2]}'" for r in ed.get("reports", []) for x in r.get("relistened", [])]
    sheet = sheets(master, out, total)
    stills = []
    for c in cues:
        t = min(total - 0.1, c["start"] + 0.45)
        sp = out / f"proof_{c['style']}_{t:05.2f}.jpg"
        media.ff("-ss", f"{t:.2f}", "-i", master, "-frames:v", "1", "-vf", "scale=540:-2", sp)
        stills.append(sp.name)

    md = [f"# QA · {ed.get('plan_name') or plan.get('name')}", "",
          f"Kind: **{kind}** · {total:.2f} s · {len(ed['pieces'])} pieces · {len(cues)} titles · "
          f"{'PASS' if fails == 0 else f'{fails} FAIL'}", "",
          "| Check | Rule | Result | |", "|---|---|---|---|", *rows, "",
          "## Cut on the floor", "Check this list: a cut here is the one thing the editor can get wrong (README).", ""]
    md += [f"- {x}" for x in floor] or ["- nothing dropped"]
    md += ["", "## Stretched words re-heard (NOTES relisten)", ""] + ([f"- {x}" for x in stretched] or ["- none"])
    md += ["", "## Retakes (SOP-11c T2, report only)", ""] + (
        [f"- {x['verdict']} (j {x['jaccard']}) at {x['first']:.1f}s and {x['second']:.1f}s: {x['text']}" for x in retakes] or ["- none"])
    md += ["", "## Spoken claims for the proof bank (SOP-3D §0, SOP-11 §9 item 5)",
           "The editor proves a figure was said, not that it is true.", ""] + (
        [f"- {t:.1f}s: {txt}" for t, txt in claims] or ["- none"])
    md += ["", "## Titles", ""] + [f"- {c['start']:.2f}-{c['end']:.2f} {c['style']} `{' / '.join(c.get('lines', []))}`"
                                  f" y={c.get('y_final', '-')} {c.get('flag', '')}" for c in cues]
    md += ["", "## Files", "", f"- master (upload this): `{Path(master).name}`"]
    if phone:
        md.append(f"- phone copy (preview only, never upload): `{Path(phone).name}`")
    md += [f"- contact sheet: `{sheet.name}` and `contact_sheet_phone.jpg`", f"- proof stills: {', '.join(stills)}", "",
           "Nothing here uploads, posts or launches. A finished file going into an ad account is the owner's go."]
    (out / "QA.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:8 + len(rows)]))
    return fails
