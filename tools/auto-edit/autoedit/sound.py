"""Our own music bed and soft sound effects, synthesized, so no licensed track ever lands in an ad
(NOTES "Sound": our own beat, never the song; SOP-11c: Music = local bed only).

Styles: "soul" (neo-soul electric piano, README bed_mt.py) and "trap" (half-time ~72 bpm, plucked loop, minor-7 pads,
sliding 808, rolling hats; NOTES). Key and tempo vary per ad from its name (NOTES: vary the key and bpm per ad).
"""
import hashlib
import wave

import numpy as np

SR = 48000


def write(path, x):
    x = np.clip(x, -1, 1)
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    w = wave.open(str(path), "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((x * 32767).astype("<i2").tobytes())
    w.close()


def lp(x, fc):
    """One-pole low-pass as a convolution with its impulse response, cut where it falls under 1e-5."""
    a = np.exp(-2 * np.pi * fc / SR)
    n = int(np.log(1e-5) / np.log(a)) + 1
    h = (1 - a) * a ** np.arange(n)
    return np.convolve(x, h)[: len(x)]


def hz(m):
    return 440 * 2 ** ((m - 69) / 12)


def _t(d):
    return np.arange(int(SR * d)) / SR


def epiano(f, d, v):
    t = _t(d)
    env = np.exp(-t * 1.6) * (1 - np.exp(-t * 300))
    tone = np.sin(2 * np.pi * f * t + 0.6 * np.exp(-t * 3) * np.sin(2 * np.pi * f * t))
    bell = 0.25 * np.exp(-t * 9) * np.sin(2 * np.pi * f * 4.01 * t)
    return v * env * (1 + 0.12 * np.sin(2 * np.pi * 4.5 * t)) * (tone + bell)


def pluck(f, d, v):
    t = _t(d)
    return v * np.exp(-t * 7) * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t) * np.exp(-t * 12))


def pad(f, d, v):
    t = _t(d)
    env = np.minimum(1, t / 0.4) * np.minimum(1, (d - t) / 0.4)
    return v * env * (np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * f * 1.003 * t))


def kick(d=0.45):
    t = _t(d)
    return np.sin(2 * np.pi * np.cumsum(45 + 75 * np.exp(-t * 28)) / SR) * np.exp(-t * 7)


def snare(rng, d=0.25):
    t = _t(d)
    n = rng.standard_normal(len(t))
    n = n - lp(n, 1200)
    return 0.6 * n * np.exp(-t * 22) + 0.4 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30)


def hat(rng, d=0.05):
    t = _t(d)
    n = rng.standard_normal(len(t))
    return (n - lp(n, 7000)) * np.exp(-t * 90)


def bass808(f0, f1, d):
    t = _t(d)
    f = f1 + (f0 - f1) * np.exp(-t * 18)        # slide into the note
    return np.tanh(1.6 * np.sin(2 * np.pi * np.cumsum(f) / SR)) * np.exp(-t * 1.4) * np.minimum(1, t * 80)


def add(buf, x, at, g=1.0):
    i = int(at * SR)
    j = min(len(buf), i + len(x))
    if 0 <= i < len(buf):
        buf[i:j] += g * x[: j - i]


def bed(length, style="soul", seed="", key=None, bpm=None):
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    rng = np.random.default_rng(h % 2 ** 32)
    root = key if key is not None else [0, 2, 3, 5, 7, 8, 10][h % 7]          # transpose by the ad's name
    n = int(SR * length) + SR
    keys, drums, low = np.zeros(n), np.zeros(n), np.zeros(n)
    if style == "trap":
        bpm = bpm or 70 + h % 6
        beat = 60 / bpm
        bar = 4 * beat
        prog = [[57, 60, 64, 67], [53, 57, 60, 64], [55, 58, 62, 65], [52, 55, 59, 62]]   # Am7 Fmaj7 Gm7 Em7
        t, b = 0.0, 0
        while t < length:
            ch = [m + root for m in prog[b % 4]]
            for m in ch:
                add(keys, pad(hz(m), bar, 0.05), t)
            for s in range(8):                                   # hazy plucked loop
                add(keys, pluck(hz(ch[(s * 3) % 4] + 12), beat, 0.09), t + s * beat / 2)
            add(low, bass808(hz(ch[0] - 12) * 1.5, hz(ch[0] - 24), bar * 0.95), t, 0.5)
            add(drums, kick(), t, 0.8)
            add(drums, kick(), t + 2.5 * beat, 0.6)
            add(drums, snare(rng), t + 2 * beat, 0.35)
            for s in range(16):                                  # hats, rolling into the bar end
                add(drums, hat(rng), t + s * beat / 4, 0.12 if s % 2 else 0.18)
            for s in range(6):
                add(drums, hat(rng, 0.03), t + 3.5 * beat + s * beat / 12, 0.08)
            t += bar
            b += 1
        mix = lp(keys, 4000) + drums + lp(low, 300)
    else:
        bpm = bpm or 80 + h % 6
        beat = 60 / bpm
        bar = 4 * beat
        prog = [[55, 58, 62, 65, 69], [48, 58, 62, 64, 69], [53, 57, 60, 64, 67], [50, 57, 60, 64, 65]]   # ii V I vi
        roots = [43, 36, 41, 38]
        t, b = 0.0, 0
        while t < length:
            ch = [m + root for m in prog[b % 4]]
            for k, m in enumerate(ch):
                add(keys, epiano(hz(m), bar * 1.05, 0.16), t + 0.012 * k)
            add(keys, epiano(hz(ch[-1] + 12), beat * 1.5, 0.05), t + 2.5 * beat)
            t_ = _t(bar * 0.9)
            add(low, np.sin(2 * np.pi * hz(roots[b % 4] + root) * t_) * np.minimum(1, t_ * 60) * np.exp(-t_ * 1.2), t, 0.55)
            for q in range(4):
                if q == 0 or (q == 2 and b % 2):
                    add(drums, kick(), t + q * beat, 0.8)
                if q in (1, 3):
                    add(drums, snare(rng), t + q * beat, 0.32)
                add(drums, hat(rng), t + q * beat, 0.22)
                add(drums, hat(rng), t + (q + 0.58) * beat, 0.16)
            t += bar
            b += 1
        mix = lp(keys, 3500) + drums + lp(low, 400)
    mix = mix[: int(SR * length)]
    fi, fo = int(0.25 * SR), int(1.2 * SR)
    mix[:fi] *= np.linspace(0, 1, fi)
    mix[-fo:] *= np.linspace(1, 0, fo)
    st = np.stack([mix, np.concatenate([np.zeros(int(0.011 * SR)), mix])[: len(mix)]], 1)
    return st / (np.max(np.abs(st)) + 1e-9) * 0.7


def sfx(kind, seed=1):
    """Soft matched effects (SOP-11b rule 12): hit under the first title, pop under tags, shimmer on the offer,
    tap on TAP / GET OFFER, whoosh on a transition (README depth: each move gets a synthesized whoosh)."""
    rng = np.random.default_rng(seed)
    if kind == "hit":
        t = _t(0.7)
        body = np.sin(2 * np.pi * np.cumsum(40 + 50 * np.exp(-t * 12)) / SR) * np.exp(-t * 5)
        return 0.8 * (body + 0.6 * lp(rng.standard_normal(len(t)), 900) * np.exp(-t * 18))
    if kind == "pop":
        t = _t(0.18)
        return 0.7 * np.sin(2 * np.pi * np.cumsum(520 + 900 * np.exp(-t * 60)) / SR) * np.exp(-t * 32)
    if kind == "tap":
        t = _t(0.16)
        return 0.7 * (np.sin(2 * np.pi * 1150 * t) + 0.5 * np.sin(2 * np.pi * 2380 * t)) * np.exp(-t * 55)
    if kind == "shimmer":
        t = _t(0.9)
        x = sum(np.sin(2 * np.pi * f * t) * np.exp(-t * (3 + i)) for i, f in enumerate((1760, 2217, 2637, 3520)))
        return 0.18 * x * np.minimum(1, t * 30)
    if kind == "whoosh":
        t = _t(0.45)
        n = rng.standard_normal(len(t))
        env = np.sin(np.pi * t / 0.45) ** 2
        return 0.35 * (n - lp(n, 600 + 3000 * t / 0.45)) * env
    raise ValueError(kind)
