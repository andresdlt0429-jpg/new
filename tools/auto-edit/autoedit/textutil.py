"""Word-level text rules: proofreading fixes, finding a spoken phrase, the figure-was-said rule, repeats and retakes."""
import re

NUM = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
       "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15", "twenty": "20",
       "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "hundred": "100", "thousand": "1000"}
STOP = set("a an the and or but so to of in on at for with is are was were be been it its this that you your we our "
           "i me my he she they them their just like um uh okay yeah".split())


def norm(w):
    return re.sub(r"[^\w$%]", "", w.lower())


def tokens(text):
    return [norm(t) for t in text.split() if norm(t)]


def digits_of(text):
    """Every number a text holds, from digits or number words ("four days" -> {"4"}, "$20K" -> {"20"})."""
    out = set(re.findall(r"\d+", text.replace(",", "")))
    for t in tokens(text):
        if t in NUM:
            out.add(NUM[t])
    return out


def figure_said(lines, said):
    """README rule: a figure on screen must have been said. True when every number in the title is in the words."""
    shown = set()
    for l in lines:
        shown |= digits_of(l)
    return shown <= digits_of(said), sorted(shown - digits_of(said))


def apply_fixes(words, fixes):
    """`fix` / `fix_phrases` (README, SOP-11d §8.4): a misheard word or phrase and what he meant.
    words: [[start, end, word, ...extra], ...]; a phrase fix merges its words into one, keeping trailing punctuation."""
    if not fixes:
        return words
    rules = sorted(((tokens(k), v) for k, v in fixes.items()), key=lambda kv: -len(kv[0]))
    out, i = [], 0
    while i < len(words):
        for ks, v in rules:
            n = len(ks)
            if n and [norm(w[2]) for w in words[i:i + n]] == ks:
                vs = v.split()
                if len(vs) == n:     # word for word: keep each word and its own timing
                    for w, nv in zip(words[i:i + n], vs):
                        punct = re.search(r"[,.?!]+$", w[2])
                        out.append([w[0], w[1], nv + (punct.group(0) if punct and not re.search(r"[,.?!]$", nv) else "")]
                                   + list(w[3:]))
                    i += n
                    break
                last = words[i + n - 1]
                punct = re.search(r"[,.?!]+$", last[2])
                out.append([words[i][0], last[1], v + (punct.group(0) if punct and not re.search(r"[,.?!]$", v) else "")]
                           + list(words[i][3:]))
                i += n
                break
        else:
            out.append(list(words[i]))
            i += 1
    return out


def find_phrase(words, phrase, nth=0, after=None):
    """Index range [i, j) of the nth occurrence of a spoken phrase (nth=-1 = the last time)."""
    ks = [stem(t) for t in tokens(phrase)]       # stems: "cleaner" finds "cleaners" across transcript passes
    hits = [i for i in range(len(words) - len(ks) + 1)
            if [stem(norm(w[2])) for w in words[i:i + len(ks)]] == ks and (after is None or words[i][0] >= after - 1e-6)]
    if not hits:
        return None
    i = hits[nth] if -len(hits) <= nth < len(hits) else None
    return None if i is None else (i, i + len(ks))


def repeats(toks, max_n=6, protect=()):
    """NOTES: drop any repeated phrase of 1 to 6 words. Returns [(i, n)]: toks[i:i+n] said again right after.
    A sentence end like "this. This" is not a stutter; a protected line is a real repeat (SOP-11d §8.2)."""
    prot = [tokens(p) for p in protect]
    found, i = [], 0
    while i < len(toks):
        hit = None
        for n in range(max_n, 0, -1):
            a, b = toks[i:i + n], toks[i + n:i + 2 * n]
            if len(b) == n and a == b and all(a):
                window = toks[max(0, i - 6):i + 2 * n + 6]
                if any(_contains(window, p) for p in prot if p):
                    continue
                hit = n
                break
        if hit:
            found.append((i, hit))
            i += hit
        else:
            i += 1
    return found


def _contains(seq, sub):
    return any(seq[k:k + len(sub)] == sub for k in range(len(seq) - len(sub) + 1))


def stem(w):
    for suf in ("ing", "ed", "es", "s", "ly"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def retakes(words, pause=0.85, look=8, span=75.0):
    """SOP-11c T2 (report-only): split at pauses of 0.85 s+, compare each stretch with the next 8 within 75 s on its
    first 18 non-stopword stems. Jaccard 0.45+ = retake, 0.34 = likely, 0.24 = review."""
    stretches, cur = [], []
    for k, w in enumerate(words):
        if cur and w[0] - words[k - 1][1] >= pause:
            stretches.append(cur)
            cur = []
        cur.append(w)
    if cur:
        stretches.append(cur)
    def bag(s):
        return set([stem(norm(w[2])) for w in s if norm(w[2]) not in STOP][:18])
    out = []
    for a in range(len(stretches)):
        A = bag(stretches[a])
        for b in range(a + 1, min(len(stretches), a + 1 + look)):
            if stretches[b][0][0] - stretches[a][0][0] > span:
                break
            B = bag(stretches[b])
            if len(A) < 3 or len(B) < 3:
                continue
            j = len(A & B) / len(A | B)
            if j >= 0.24:
                verdict = "retake" if j >= 0.45 else "likely" if j >= 0.34 else "review"
                out.append(dict(first=stretches[a][0][0], second=stretches[b][0][0], jaccard=round(j, 2), verdict=verdict,
                                text=" ".join(w[2] for w in stretches[a][:10])))
    return out


CLAIM = re.compile(r"\d|\b(" + "|".join(NUM) + r")\b|guarantee|percent|%|\$|days?|jobs?|leads?|results?|x\b", re.I)


def claims(words, width=8):
    """Spoken lines carrying a figure or a promise, listed for the proof bank (README; SOP-3D §0; SOP-11 §9 item 5)."""
    out, last = [], -99
    for i, w in enumerate(words):
        if CLAIM.search(w[2]) and (digits_of(w[2]) or re.search(r"guarantee|percent|%", w[2], re.I)) and i - last > width:
            lo, hi = max(0, i - 4), min(len(words), i + width)
            out.append((words[lo][0], " ".join(x[2] for x in words[lo:hi])))
            last = i
    return out
