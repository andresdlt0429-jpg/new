"""Phase 5: read a script table from spec/scripts/*.md -> plans/<ad>/script.json.

  python3 engines/script_parser.py spec/scripts/<script>.md [plans/<ad>/script.json]

Understands Andy's table shapes (column names vary between scripts):
  TIME | LINE | ON SCREEN                      (Tower one-pager: beat inside the TIME cell)
  Time | Beat | Say | On screen | Delivery      (Starting Out body)
  #    | Hook type | Say | Show | Text          (hook alternates)
  TIME | BEAT | SAY | ON SCREEN | SHOW | DELIVERY

Also pulls:
  protected pauses   Delivery cells like: Pause before "this is for you."  (SOP 2.6)
  lock items         bullets under "Lock before this runs" / "Confirm before filming" /
                     "Flag before you shoot"  (SOP 2.1)
  meta scan items    bullets under a "Meta policy" heading
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "spec", "sop-defaults.json")) as f:
    SOP = json.load(f)

LOCK_HEADINGS = re.compile(r"lock before this runs|confirm before filming|flag before you shoot|before you launch", re.I)
META_HEADINGS = re.compile(r"meta.*polic|policy scan", re.I)
TIME_RE = re.compile(r"(\d+:\d{2})(?:\s*[–-]\s*(\d+:\d{2}))?")
COLS = [  # (field, pattern) in priority order; first match wins per header cell
    ("id", re.compile(r"^#$")),
    ("time", re.compile(r"^time$", re.I)),
    ("beat", re.compile(r"^beat$", re.I)),
    ("say", re.compile(r"^(say|line|spoken.*)$", re.I)),
    ("on_screen", re.compile(r"^(on[ -]?screen|text|overlay)$", re.I)),
    ("show_text", re.compile(r"show\s*\+\s*on[- ]screen", re.I)),
    ("show", re.compile(r"^(show|visual.*)$", re.I)),
    ("delivery", re.compile(r"^delivery$", re.I)),
    ("hook_type", re.compile(r"^hook type", re.I)),
]


def clean(cell):
    c = cell.strip()
    c = re.sub(r"\\([*#_\[\]\\])", r"\1", c)   # Drive export escapes
    c = c.replace("**", "").strip()
    return c


def unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"“" and s[-1] in "\"”":
        s = s[1:-1]
    return s.strip()


def overlay_text(cell):
    """ON SCREEN cells mix direction and text. 'Overlay: X' or 'TITLE BEHIND HEAD: X' wins;
    otherwise a mostly-uppercase cell is the text; otherwise it is direction only."""
    m = re.search(r"(?:overlay|text on screen)\s*:\s*(.+)$", cell, re.I)
    if m:
        return unquote(m.group(1)), "graphic"
    m = re.search(r"title behind head\s*:\s*(.+)$", cell, re.I)
    if m:
        return unquote(m.group(1)), "title"
    letters = [ch for ch in cell if ch.isalpha()]
    if letters and sum(ch.isupper() for ch in letters) / len(letters) > 0.8:
        return cell, "graphic"
    return None, None


def protected_pauses(delivery):
    out = []
    for m in re.finditer(r"pause before\s*[\"“](.+?)[\"”]", delivery or "", re.I):
        out.append({"before": m.group(1).rstrip(".").strip(), "why": m.group(0)})
    if re.search(r"slowest moment|stand still", delivery or "", re.I):
        out.append({"before": None, "why": "slowest moment / stand still: keep breathing room before this line"})
    return out


def tables(lines):
    """Yield (heading, header_cells, rows) for every markdown table."""
    heading, i = None, 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("#"):
            heading = ln.lstrip("#").strip().replace("**", "")
        if ln.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [clean(c) for c in ln.strip().strip("|").split("|")]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([clean(c) for c in lines[j].strip().strip("|").split("|")])
                j += 1
            yield heading, header, rows
            i = j
            continue
        i += 1


def bullets_under(lines, pattern):
    items, on = [], False
    for ln in lines:
        if ln.startswith("#"):
            on = bool(pattern.search(ln))
            continue
        if on:
            m = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", ln)
            if m:
                items.append(clean(m.group(1)))
    return items


def parse(path):
    with open(path) as f:
        text = f.read()
    lines = text.splitlines()
    title = next((ln.lstrip("#").strip().replace("**", "") for ln in lines if ln.startswith("# ")), os.path.basename(path))
    beats, hooks, alternates, problems = [], [], [], []
    for heading, header, rows in tables(lines):
        fields = {}
        for k, h in enumerate(header):
            for name, pat in COLS:
                if pat.search(h) and name not in fields:
                    fields[name] = k
                    break
        if "say" not in fields:
            continue  # not a script table (policy table, changelog, ...)
        is_hooks = "id" in fields and "time" not in fields
        alt_of = None
        m = re.search(r"\b([A-Za-z+-]+)\s+alternates?\b", heading or "", re.I)
        if is_hooks and m and m.group(1).lower() != "hook":
            alt_of = m.group(1).upper()  # "CTA alternates" or "GUARANTEE+CTA alternates": beats they replace
        for r in rows:
            def get(name):
                k = fields.get(name)
                return r[k] if k is not None and k < len(r) else ""
            say = unquote(get("say"))
            if not say:
                continue
            on_screen, kind = overlay_text(get("on_screen") or get("text") or "")
            if not on_screen and get("show_text"):
                on_screen, kind = overlay_text(get("show_text"))
            item = {"say": say, "on_screen": on_screen, "on_screen_kind": kind,
                    "show": get("show") or None, "delivery": get("delivery") or None,
                    "protected_pauses": protected_pauses(get("delivery"))}
            if alt_of:
                reps = alt_of.split("+")
                item.update({"id": f"{reps[-1][0]}{clean(get('id'))}", "beat": reps[-1], "replaces": reps})
                alternates.append(item)
            elif is_hooks:
                item.update({"id": f"H{clean(get('id'))}", "hook_type": get("hook_type") or None})
                hooks.append(item)
            else:
                tcell = get("time")
                m = TIME_RE.search(tcell)
                beat = get("beat") or re.sub(TIME_RE, "", tcell).strip(" |") or None
                item.update({"id": f"B{len(beats) + 1}", "time": m.group(1) if m else None,
                             "time_end": m.group(2) if m and m.group(2) else None, "beat": beat})
                if not m:
                    problems.append(f"row without a TIME: {say[:40]}")
                beats.append(item)
    if not beats and not hooks:
        problems.append("no script table found (needs a SAY / LINE column)")
    return {
        "source": os.path.relpath(path, ROOT), "title": title,
        "draft": bool(re.search(r"\bDRAFT\b", title)),
        "beats": beats, "hooks": hooks, "alternates": alternates,
        "lock_items": bullets_under(lines, LOCK_HEADINGS),
        "meta_scan": bullets_under(lines, META_HEADINGS),
        "problems": problems,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    s = parse(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else None
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            json.dump(s, f, indent=1)
    print(f"{s['title']}  ({len(s['beats'])} beats, {len(s['hooks'])} hooks, "
          f"{len(s['lock_items'])} lock items, {len(s['meta_scan'])} meta items)")
    for b in s["beats"]:
        pp = "; ".join(p["why"] for p in b["protected_pauses"])
        print(f"  {b['time'] or '?':>5} {b['beat'] or '':14s} SAY: {b['say'][:70]}")
        if b["on_screen"]:
            print(f"        {'':14s} ON SCREEN ({b['on_screen_kind']}): {b['on_screen']}")
        if pp:
            print(f"        {'':14s} PROTECTED: {pp}")
    for a in s["alternates"]:
        print(f"  {a['id']:>5} {a['beat'] + ' alt':14s} SAY: {a['say'][:70]}  | {a['on_screen'] or ''}")
    for h in s["hooks"]:
        print(f"  {h['id']:>5} {'HOOK':14s} SAY: {h['say'][:70]}  | {h['on_screen'] or ''}")
    for x in s["lock_items"]:
        print(f"  LOCK: {x}")
    for x in s["meta_scan"]:
        print(f"  META: {x[:110]}")
    for x in s["problems"]:
        print(f"  PROBLEM: {x}")
