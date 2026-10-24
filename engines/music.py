"""Phase 8: synthesized music bed + soft effects, settle-in and fade-out (SOP section 5 stand-in).

Everything is made here with numpy (free, local, no samples or licences) and written as one
stereo track that lines up sample-for-sample with the ad's timeline (speech + logo card). The
renderer mixes it under the cleaned speech; QA reads the same track, so every level is measured.

  bed        soft pad (chords from sop-defaults music_sop_5), sub bass, quiet plucked arpeggio
  level      LU relative to the speech target: gaps_lu between words, under_words_lu while a word
             is spoken (ducked from the word timings, no guessing from audio), card_lu on the logo card
  silence    none in silent_beats (brief: hook flat, nothing competing with the guarantee)
  settle-in  the bed fades in over settle_in_s right after the hook
  fade-out   the bed fades to zero over fade_out_s, ending on the last sample of the logo card
  effects    whoosh (slab title rise), tick (each tick mark), pop (CTA button); none in silent beats

Numbers live in spec/sop-defaults.json music_sop_5 (placeholder until the SOP).
"""
import json
import os
import re
import subprocess
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "spec", "sop-defaults.json")) as f:
    _SOP = json.load(f)
M = _SOP["music_sop_5"]
AUD = _SOP["audio_sop_5"]
SR = 48000
CTRL = 1000  # control rate (Hz) for the gain curve


def db(x):
    return 20 * np.log10(np.maximum(x, 1e-12))


def lin(d):
    return 10 ** (np.asarray(d, np.float64) / 20)


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def lowpass(x, fc, order=4):
    """Zero-phase low-pass in the frequency domain (scipy is not installed)."""
    f = np.fft.rfftfreq(x.shape[0], 1 / SR)
    h = 1 / np.sqrt(1 + (f / fc) ** (2 * order))
    return np.fft.irfft(np.fft.rfft(x, axis=0) * (h[:, None] if x.ndim == 2 else h), n=x.shape[0], axis=0)


def bandpass(x, lo, hi):
    f = np.fft.rfftfreq(x.shape[0], 1 / SR)
    h = 1 / np.sqrt(1 + (f / hi) ** 8) * (1 - 1 / np.sqrt(1 + (f / lo) ** 8))
    return np.fft.irfft(np.fft.rfft(x) * h, n=x.shape[0])


def raised(x):
    """0..1 -> 0..1 raised-cosine ramp."""
    return 0.5 - 0.5 * np.cos(np.pi * np.clip(x, 0, 1))


# ---------- bed ----------

def synth_bed(seconds, seed=0):
    rng = np.random.default_rng(seed)
    n = int(round(seconds * SR))
    t = np.arange(n) / SR
    beat = 60 / M["bpm"]
    chord_s = 4 * beat * M["bars_per_chord"]
    chords = M["chords_midi"]
    out = np.zeros((n, 2))
    xf = 0.8  # chord crossfade, seconds
    k = 0
    while k * chord_s < seconds + xf:
        a, b = k * chord_s - xf / 2, (k + 1) * chord_s + xf / 2
        i0, i1 = max(0, int(a * SR)), min(n, int(b * SR))
        if i1 <= i0:
            k += 1
            continue
        tt = t[i0:i1]
        env = raised((tt - a) / xf) * raised((b - tt) / xf)
        ch = chords[k % len(chords)]
        pad = np.zeros((i1 - i0, 2))
        for m in ch:
            f0 = midi_hz(m)
            for side, cents in ((0, -5), (1, 5)):  # detuned pair, one per side: width without phasing
                f = f0 * 2 ** (cents / 1200)
                for h in range(1, 6):
                    pad[:, side] += np.sin(2 * np.pi * f * h * tt + rng.uniform(0, 2 * np.pi)) / h ** 1.6
        bass = np.sin(2 * np.pi * midi_hz(ch[0] - 12) * tt)
        pulse = 0.65 + 0.35 * np.exp(-((tt - a) % beat) / 0.25)  # soft breathing on the beat
        out[i0:i1] += (pad * 0.22 + (bass * pulse * 0.30)[:, None]) * env[:, None]
        k += 1
    # quiet plucked arpeggio on 8th notes, an octave up, alternating sides
    step = beat / 2
    j = 0
    while j * step < seconds:
        s = j * step
        ch = chords[int(s // chord_s) % len(chords)]
        m = ch[[0, 1, 2, 1][j % 4]] + 12
        i0, i1 = int(s * SR), min(n, int((s + 0.9) * SR))
        tt = t[i0:i1] - s
        note = (np.sin(2 * np.pi * midi_hz(m) * tt) + 0.25 * np.sin(4 * np.pi * midi_hz(m) * tt)) * np.exp(-tt / 0.22)
        note *= np.minimum(1, tt / 0.004)
        pan = 0.35 if j % 2 else 0.65
        out[i0:i1, 0] += note * 0.10 * (1 - pan)
        out[i0:i1, 1] += note * 0.10 * pan
        j += 1
    out = lowpass(out, 2400)  # warm, keeps clear of the speech presence band
    return out / max(1e-9, np.abs(out).max()) * 0.5


# ---------- effects ----------

def sfx(kind, seed=0):
    rng = np.random.default_rng(seed)
    if kind == "whoosh":
        d = 0.45
        t = np.arange(int(d * SR)) / SR
        noise = rng.standard_normal(t.size)
        y = np.zeros_like(t)
        # three noise bands peaking one after another: low -> high reads as a soft upward sweep
        for (lo, hi), c in (((250, 800), 0.18), ((800, 2200), 0.28), ((2200, 6000), 0.36)):
            y += bandpass(noise, lo, hi) * np.exp(-((t - c) / 0.09) ** 2)
        y *= raised(t / 0.05) * raised((d - t) / 0.08)
    elif kind == "tick":
        d = 0.07
        t = np.arange(int(d * SR)) / SR
        y = (np.sin(2 * np.pi * 1800 * t) + 0.4 * np.sin(2 * np.pi * 3600 * t)) * np.exp(-t / 0.012)
        y *= np.minimum(1, t / 0.0015)
    elif kind == "pop":
        d = 0.16
        t = np.arange(int(d * SR)) / SR
        f = 620 * np.exp(-t / 0.08) + 300  # pitch glides down
        y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.045) * np.minimum(1, t / 0.003)
    else:
        raise ValueError(kind)
    return y / np.abs(y).max() * lin(M["sfx"]["peak_dbfs"])


# ---------- timeline ----------

def windows(segs, beats):
    out = []
    for s in segs:
        if s.get("beat") in beats:
            if out and abs(out[-1][1] - s["t0"]) < 1e-3:
                out[-1][1] = s["t1"]
            else:
                out.append([s["t0"], s["t1"]])
    return [tuple(w) for w in out]


def inside(t, wins):
    return any(a - 1e-6 <= t < b for a, b in wins)


def gain_curve(total, length, words, silent, hook_end):
    """Per-control-sample linear gain (relative to speech target) -> (gain, level_lu, gate)."""
    nc = int(np.ceil(length * CTRL)) + 1
    tc = np.arange(nc) / CTRL
    level = np.full(nc, M["gaps_lu"], np.float64)
    pa, pb = M["word_pad_s"]
    for w0, w1 in words:
        level[int(max(0, w0 - pa) * CTRL):int((w1 + pb) * CTRL) + 1] = M["under_words_lu"]
    level[tc >= total] = M["card_lu"]
    # duck fast, recover slowly (one-pole in dB, attack when going down)
    a_dn = np.exp(-1 / (M["duck_attack_s"] * CTRL))
    a_up = np.exp(-1 / (M["duck_release_s"] * CTRL))
    sm = np.empty(nc)
    v = level[0]
    for i, x in enumerate(level):
        a = a_dn if x < v else a_up
        v = a * v + (1 - a) * x
        sm[i] = v
    gate = np.ones(nc)
    gate[tc < hook_end] = 0
    gate *= np.where(tc >= hook_end, raised((tc - hook_end) / M["settle_in_s"]), 0)
    for a, b in silent:
        if a < 1e-6:
            continue  # the hook at t=0 is handled by the settle-in
        down = raised((a - tc) / M["into_silence_fade_s"])
        up = raised((tc - b) / M["out_of_silence_fade_s"])
        g = np.where(tc < a, down, np.where(tc < b, 0, up))
        gate *= g
    gate *= raised((length - tc) / M["fade_out_s"])
    gate[-1] = 0
    return lin(sm) * gate, sm, gate


def build(segs, srcs, titles, graphics, total, card_s, path, seed=0):
    """Write the bed + effects track (WAV, 48 kHz stereo, float->int16) and return a report."""
    length = total + card_s
    n = int(round(length * SR))
    words = []
    for s in segs:
        for w in srcs[s["src"]]["words"]:
            if s["in"] - 0.001 <= w["start"] < s["out"]:
                words.append((s["t0"] + w["start"] - s["in"], s["t0"] + min(w["end"], s["out"]) - s["in"]))
    silent = windows(segs, M["silent_beats"])
    hook = windows(segs, ["HOOK"])
    hook_end = hook[0][1] if hook and hook[0][0] < 1e-3 else 0.0

    bed = synth_bed(length, seed)
    raw_lufs = lufs(bed)
    # bed at 0 LU = the speech target loudness; the curve then places it gaps_lu / under_words_lu below
    norm = lin(AUD["loudness_lufs"] - raw_lufs)
    g, level, gate = gain_curve(total, length, words, silent, hook_end)
    gs = np.interp(np.arange(n) / SR, np.arange(g.size) / CTRL, g)
    music = bed[:n] * norm * gs[:, None]

    fx = np.zeros(n)
    hits = []
    want = M["sfx"]["by_kind"] if M["sfx"]["enabled"] else {}

    def add(kind, t, ref):
        if kind is None:
            return
        if inside(t, silent):
            hits.append({"ref": ref, "kind": kind, "t": round(t, 3), "skipped": "silent beat"})
            return
        y = sfx(kind, seed + len(hits))
        i = int(round(t * SR))
        j = min(n, i + y.size)
        fx[i:j] += y[:j - i]
        hits.append({"ref": ref, "kind": kind, "t": round(t, 3)})
    for x in titles:
        add(want.get("title"), x["ts"], x["id"])
    for x in graphics:
        k = x.get("component", "text")
        if k == "ticks":
            a0 = round(x["ts"] * 30) / 30
            for it in x.get("items") or []:
                add(want.get("ticks"), a0 + it["t"], f"{x['id']}:{it['text']}")
        else:
            add(want.get(k), x["ts"], x["id"])
    track = music + fx[:, None]
    write_wav(path, track)
    return {"path": path, "seconds": round(length, 3), "raw_bed_lufs": raw_lufs,
            "silent_windows": [[round(a, 3), round(b, 3)] for a, b in silent], "hook_end": round(hook_end, 3),
            "words": words, "sfx": hits, "level_lu": {"gaps": M["gaps_lu"], "under_words": M["under_words_lu"],
                                                       "card": M["card_lu"]},
            "curve": {"t": (np.arange(g.size) / CTRL)[::20].round(3).tolist(),
                      "gain_db": np.round(db(g[::20]), 1).tolist()}}


# ---------- io + measuring ----------

def write_wav(path, x):
    x = np.clip(x, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def read_audio(path, start=None, dur=None):
    """Decode any file's audio to float32 stereo at 48 kHz."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2)


def lufs(x):
    """Integrated loudness (EBU R128) of a numpy signal, via ffmpeg's ebur128."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-f", "f32le", "-ar", str(SR), "-ac", "2",
                        "-i", "-", "-af", "ebur128", "-f", "null", "-"],
                       input=np.ascontiguousarray(x, np.float32).tobytes(), capture_output=True)
    err = p.stderr.decode()
    m = re.search(r"I:\s+(-?[\d.]+) LUFS", err[err.rfind("Summary:"):])
    return float(m.group(1)) if m else None


def rms_db(x):
    return round(float(db(np.sqrt(np.mean(np.square(x)))) if x.size else -120.0), 1)


def qa(report, final_path):
    """Measure the bed against the final mix: silent where it must be, low under words, ends at zero."""
    bed = read_audio(report["path"])
    mix = read_audio(final_path)
    n = min(len(bed), len(mix))
    bed, mix = bed[:n], mix[:n]

    def cut(x, wins):
        idx = np.zeros(n, bool)
        for a, b in wins:
            idx[int(a * SR):int(b * SR)] = True
        return x[idx]
    silent_rms = rms_db(cut(bed, report["silent_windows"]))
    # speech level from the final mix in word windows (the bed is >= 18 dB lower there, so it adds < 0.1 dB)
    w = report["words"]
    below = rms_db(cut(mix, w)) - rms_db(cut(bed, w))
    gaps = []
    for (a0, a1), (b0, b1) in zip(w, w[1:]):
        if b0 - a1 > 0.5 and not inside(a1 + 0.3, [tuple(x) for x in report["silent_windows"]]):
            gaps.append((a1 + 0.3, b0 - 0.1))
    gap_rms = rms_db(cut(bed, gaps)) if gaps else None
    tail = mix[-int(0.05 * SR):]
    tail_peak = round(float(db(np.abs(tail).max())), 1) if tail.size else -120.0
    fx = [h for h in report["sfx"] if "skipped" not in h]
    fx_bad = [h for h in fx if inside(h["t"], [tuple(x) for x in report["silent_windows"]])]
    checks = {
        "music_silent_in_hook_and_guarantee": {
            "status": "pass" if silent_rms <= -90 else "fail",
            "value": f"bed {silent_rms} dBFS RMS in {report['silent_windows']} (need silence)"},
        "music_low_under_speech": {
            "status": "pass" if below >= M["min_below_speech_db"] else "fail",
            "value": f"bed {below:.1f} dB below speech while words are spoken (RMS; need >= {M['min_below_speech_db']})"
                     + (f"; bed in pauses {gap_rms} dBFS RMS vs speech {rms_db(cut(mix, w))}" if gap_rms is not None else "")},
        "fade_out_to_silence": {
            "status": "pass" if tail_peak <= -60 else "fail",
            "value": f"last 50 ms of the final audio peak {tail_peak} dBFS (need <= -60)"},
        "effects_soft_and_placed": {
            "status": "pass" if not fx_bad else "fail",
            "value": f"{len(fx)} effects at peak {M['sfx']['peak_dbfs']} dBFS, none in the hook or guarantee"
                     + (f"; {len(report['sfx']) - len(fx)} skipped (silent beat)" if len(report["sfx"]) > len(fx) else "")
                     if not fx_bad else f"effects inside a silent beat: {fx_bad}"},
    }
    return checks, {"bed_below_speech_db": round(below, 1), "silent_rms_dbfs": silent_rms,
                    "gap_rms_dbfs": gap_rms, "tail_peak_dbfs": tail_peak, "sfx": len(fx)}


def proof_plot(report, final_path, segs, out_png, title):
    """One picture of the mix: final audio level (grey), music bed level (blue), silent beats,
    effects, beats along the top."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    with open(os.path.join(ROOT, "brand.json")) as f:
        blue = json.load(f)["colors"]["primary"]
    bed, mix = read_audio(report["path"]), read_audio(final_path)
    hop = int(0.05 * SR)

    def env(x):
        k = len(x) // hop
        return db(np.sqrt(np.mean(np.square(x[:k * hop]).reshape(k, hop, 2), axis=(1, 2))))
    em, eb = env(mix), env(bed)
    fig, ax = plt.subplots(figsize=(16, 5.2), dpi=110)
    fig.patch.set_facecolor("#121418")
    ax.set_facecolor("#121418")
    ax.fill_between(np.arange(len(em)) * 0.05, -90, np.maximum(em, -90), color="#8a8f98", alpha=0.55,
                    lw=0, label="final mix (speech + bed + effects)")
    ax.plot(np.arange(len(eb)) * 0.05, np.maximum(eb, -90), color=blue, lw=1.8, label="music bed + effects stem")
    for i, (a, b) in enumerate(report["silent_windows"]):
        ax.axvspan(a, b, color="#d0453f", alpha=0.18, lw=0, label="no music, no effects" if i == 0 else None)
    total = segs[-1]["t1"]
    ax.axvspan(total, report["seconds"], color="#F8D898", alpha=0.12, lw=0, label="logo card (bed fades out)")
    for h in report["sfx"]:
        if "skipped" not in h:
            ax.plot([h["t"]], [-8], marker="v", color="#F8D898", ms=7)
            ax.text(h["t"], -4, h["kind"], color="#F8D898", fontsize=7, ha="center")
    prev = None
    for sg in segs:
        if sg.get("beat") != prev:
            ax.axvline(sg["t0"], color="#3a3f48", lw=0.8)
            ax.text(sg["t0"] + 0.1, -86, sg.get("beat") or "", color="#c8ccd4", fontsize=8, rotation=90, va="bottom")
            prev = sg.get("beat")
    ax.set_xlim(0, report["seconds"])
    ax.set_ylim(-90, 2)
    ax.set_xlabel("seconds", color="#c8ccd4")
    ax.set_ylabel("level, dBFS (50 ms RMS)", color="#c8ccd4")
    ax.tick_params(colors="#c8ccd4")
    for sp in ax.spines.values():
        sp.set_color("#3a3f48")
    ax.set_title(title, color="#ebebeb", loc="left", fontsize=13)
    ax.legend(loc="lower right", fontsize=8, facecolor="#1c1f24", edgecolor="#3a3f48", labelcolor="#c8ccd4")
    fig.tight_layout()
    fig.savefig(out_png, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_png
