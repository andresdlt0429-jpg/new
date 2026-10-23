"""Rule 4: never put a number on screen that was not spoken in that video.

Builds a ledger of every spoken number from Whisper words, and checks any
on-screen text against it. Both sides go through the same parser, so
"$100K", "$100,000", "a hundred thousand dollars" and "100 thousand bucks"
all become (100000, "money").

CLI:  python3 engines/numbers_guard.py work/<clip>/numbers.json "ON SCREEN TEXT" ...
      exit 0 = all numbers spoken, exit 1 = BLOCK
"""
import json
import re
import sys

UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}
SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "k": 1e3, "grand": 1e3}
MONEY_AFTER = {"dollar", "dollars", "bucks", "buck"}
PERCENT_AFTER = {"percent", "per cent", "%"}
SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}
DIGIT_RE = re.compile(r"^([+\-]?)(\$?)(\d[\d,]*(?:\.\d+)?)(k|m|b|x|st|nd|rd|th)?(%?)$", re.I)

# Below this Whisper word probability, a spoken number must be confirmed by ear.
LOW_CONFIDENCE = 0.70


def tokenize(words):
    """words: [{"w", "start", "end", "p"}] -> sub-tokens split on hyphens and slashes."""
    out = []
    for i, wd in enumerate(words):
        raw = wd["w"].strip()
        parts = []
        for part in re.split(r"[-/–—]", raw):
            t = part.strip().strip("\"'“”‘’()[]{}!?;:+~#*").lower()
            t = t.rstrip(".,") if not re.search(r"\d\.\d", t) else t.rstrip(",")
            if t:
                parts.append(t)
        # "five-star", "fifteen-minute": the number starts a new value, it never
        # continues the number before it ("two hundred and fifty five-star" = 250, 5).
        hyphen_noun = len(parts) > 1 and _kind(parts[-1]) is None and not DIGIT_RE.match(parts[-1])
        for n, t in enumerate(parts):
            out.append({"t": t, "i": i, "start": wd.get("start"), "end": wd.get("end"),
                        "p": wd.get("p", 1.0), "fresh": hyphen_noun and n == 0})
    return out


def _digit(tok):
    m = DIGIT_RE.match(tok["t"])
    if not m:
        return None
    _, dollar, num, suf, pct = m.groups()
    val = float(num.replace(",", ""))
    suf = (suf or "").lower()
    if suf in SUFFIX:
        val *= SUFFIX[suf]
    unit = "money" if dollar else "percent" if pct else None
    return val, unit


def _kind(t):
    if t in UNITS:
        return "unit"
    if t in TEENS:
        return "teen"
    if t in TENS:
        return "tens"
    if t == "hundred":
        return "hundred"
    if t in SCALES:
        return "scale"
    return None


def parse_numbers(tokens):
    """Scan sub-tokens and return [{value, unit, text, start, end, p_min, word_idx}]."""
    found, n, k = [], len(tokens), 0
    while k < n:
        tok = tokens[k]
        dig = _digit(tok)
        kind = _kind(tok["t"])
        # "a hundred", "a thousand"
        starts_with_a = tok["t"] == "a" and k + 1 < n and tokens[k + 1]["t"] in ("hundred", "thousand", "million")
        if dig is None and kind in (None, "scale") and not starts_with_a:
            k += 1
            continue
        span = [tok]
        unit = None
        total, current, last = 0.0, 0.0, None
        j = k
        if starts_with_a:
            current, last, j = 1.0, "unit", k + 1
        while j < n:
            t = tokens[j]["t"]
            if j != k and tokens[j].get("fresh"):
                break
            d = _digit(tokens[j]) if j == k else None
            kd = _kind(t)
            if d is not None and last is None:
                current, unit, last = d[0], d[1], "digit"
            elif kd == "unit" and last in (None, "tens", "hundred", "scale", "and"):
                current += UNITS[t]
                last = "unit"
            elif kd == "teen" and last in (None, "hundred", "scale", "and"):
                current += TEENS[t]
                last = "teen"
            elif kd == "tens" and last in (None, "hundred", "scale", "and"):
                current += TENS[t]
                last = "tens"
            elif kd == "hundred" and last in ("unit", "teen", "tens", "digit") and current < 100:
                current *= 100
                last = "hundred"
            elif kd == "scale" and last in ("unit", "teen", "tens", "hundred", "digit"):
                total += current * SCALES[t]
                current, last = 0.0, "scale"
                if t == "grand":  # "20 grand" is always money in speech
                    unit = "money"
            elif t == "and" and last in ("hundred", "scale") and j + 1 < n and _kind(tokens[j + 1]["t"]) in ("unit", "teen", "tens"):
                last = "and"
            else:
                break
            if j != k:
                span.append(tokens[j])
            j += 1
        value = total + current
        # Unit words right after the number.
        if j < n and tokens[j]["t"] in MONEY_AFTER:
            unit = "money"
            span.append(tokens[j])
            j += 1
        elif j < n and tokens[j]["t"] in PERCENT_AFTER:
            unit = "percent"
            span.append(tokens[j])
            j += 1
        elif j + 1 < n and tokens[j]["t"] == "per" and tokens[j + 1]["t"] == "cent":
            unit = "percent"
            span += tokens[j:j + 2]
            j += 2
        # A "$" sitting alone in front of a spelled number.
        if k > 0 and tokens[k - 1]["t"] == "$":
            unit = "money"
        found.append({"value": value, "unit": unit,
                      "text": " ".join(s["t"] for s in span),
                      "start": span[0]["start"], "end": span[-1]["end"],
                      "p_min": round(min(s["p"] for s in span), 3),
                      "word_idx": sorted({s["i"] for s in span})})
        k = max(j, k + 1)
    return found


def spoken_ledger(words):
    """Ledger of every number spoken, with timing and Whisper confidence."""
    ledger = parse_numbers(tokenize(words))
    for item in ledger:
        item["confirm_by_ear"] = item["p_min"] < LOW_CONFIDENCE
    return ledger


def screen_numbers(text):
    return parse_numbers(tokenize([{"w": w} for w in text.split()]))


def unreadable(text):
    """Fail closed: screen words that contain a digit the parser could not read."""
    words = text.split()
    read = {i for n in screen_numbers(text) for i in n["word_idx"]}
    return [w for i, w in enumerate(words) if re.search(r"\d", w) and i not in read]


def check(text, ledger):
    """Return {"ok", "block", "warn"}: block = number not spoken; warn = spoken but
    unit missing in speech or low confidence."""
    result = {"text": text, "ok": [], "block": [], "warn": []}
    for w in unreadable(text):
        result["block"].append(f"{w}: contains a number the guard cannot read, needs a manual check")
    for num in screen_numbers(text):
        same = [s for s in ledger if abs(s["value"] - num["value"]) < 1e-9]
        exact = [s for s in same if s["unit"] == num["unit"]]
        loose = [s for s in same if s["unit"] is None or num["unit"] is None]
        label = f"{num['text']} = {fmt(num)}"
        if exact or loose:
            hit = (exact or loose)[0]
            where = f"spoken at {hit['start']:.2f}s as \"{hit['text']}\"" if hit.get("start") is not None else "spoken"
            if not exact:
                result["warn"].append(f"{label}: value {where}, but unit differs ({hit['unit'] or 'none'} vs {num['unit'] or 'none'})")
            elif hit.get("confirm_by_ear"):
                result["warn"].append(f"{label}: {where}, low confidence {hit['p_min']}, confirm by ear")
            else:
                result["ok"].append(f"{label}: {where}")
        elif same:
            result["block"].append(f"{label}: spoken only as {same[0]['unit'] or 'plain number'} \"{same[0]['text']}\", not {num['unit']}")
        else:
            result["block"].append(f"{label}: NOT SPOKEN in this video")
    return result


def fmt(num):
    v = int(num["value"]) if num["value"] == int(num["value"]) else num["value"]
    return f"${v:,}" if num["unit"] == "money" else f"{v}%" if num["unit"] == "percent" else f"{v:,}"


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    with open(argv[1]) as f:
        ledger = json.load(f)
    blocked = False
    for text in argv[2:]:
        r = check(text, ledger)
        status = "BLOCK" if r["block"] else "WARN" if r["warn"] else "PASS"
        blocked |= bool(r["block"])
        print(f"[{status}] {text}")
        for k in ("block", "warn", "ok"):
            for line in r[k]:
                print(f"    {k:5s} {line}")
    sys.exit(1 if blocked else 0)


if __name__ == "__main__":
    main(sys.argv)
