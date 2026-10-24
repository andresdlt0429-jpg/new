"""Plan files: one JSON per ad. The first pass is rules; the plan pass is where the ad gets good (README).

{
  "name": "DLT · Agency · Exterior cleaners · H1 · r1",   SOP-11 §7 title: <Shop> · <Service> · <$price | NP> · H<n> · r<n>
  "kind": "agency" | "client" | "breakout",
  "brand": {"name", "wordmark", "tagline", "color", "logo"},
  "card": {"shop", "owner", "area", "price", "no_price", "services_off": [...]},   client brief card (SOP-11 §3)
  "sections": [{"src", "from", "to" | "keep": [first words, last words], "after", "focus_y", "tail",
                "broll": [{"src", "say", "nth", "dur", "ss", "focus_x", "vo"}]}],
  "fix": {"misheard phrase": "what he meant"},  "drop": [{"say", "nth"}],  "protect": ["real repeat"],
  "titles": [{"style", "say", "nth", "lines", "at", "hold", "y", "behind"}],
  "offer": {"lines": [...], "say": [...]},  "cta": {"say", "lines"},  "end_card": true,
  "music": {"style", "db", "key", "bpm"},  "layout": {profile overrides}
}
"""
import json
import re
from pathlib import Path

from . import config, textutil

VIDEO = {".mov", ".mp4", ".m4v", ".mkv", ".webm"}


def root_of(plan_path):
    """The tool's root: the folder holding plans/ (plans may sit in subfolders of plans/)."""
    p = Path(plan_path).resolve()
    for d in p.parents:
        if d.name == "plans":
            return d.parent
    return p.parent


def load(plan_path):
    plan = json.loads(Path(plan_path).read_text())
    if plan.get("kind") not in config.PROFILES:
        raise ValueError(f"kind must be one of {sorted(config.PROFILES)}")
    root = root_of(plan_path)
    for s in plan["sections"]:
        s["src"] = str((root / s["src"]).resolve()) if not Path(s["src"]).is_absolute() else s["src"]
        for b in s.get("broll", []):
            b["src"] = str((root / b["src"]).resolve()) if not Path(b["src"]).is_absolute() else b["src"]
    prof = dict(config.PROFILES[plan["kind"]])
    prof.update(plan.get("layout", {}))
    if "music" in plan:
        prof["music"] = {**prof["music"], **plan["music"]}
    plan["_profile"] = prof
    plan["_root"] = str(root)
    plan["fix"] = {**config.DEFAULT_FIX, **plan.get("fix", {})}
    plan.setdefault("titles", [])
    plan.setdefault("brand", {})
    plan.setdefault("card", {})
    return plan


def slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:80] or "ad"


def role_of(name):
    n = name.lower()
    if n.startswith("hook"):
        return "hook"
    if "cta" in n:
        return "cta"
    if "broll" in n or "b-roll" in n:
        return "broll"
    return "body"


def draft_titles(kind, words, hook_words):
    """README auto-planning: combo from the hook's opening clause, number from a spoken $ or %, stack from a count +
    unit. The plan pass (you or Claude) then sharpens them."""
    titles = []
    if hook_words and kind in ("agency", "client"):
        clause, rest = [], []
        for w in hook_words:
            (rest if clause and re.search(r"[,.?!]$", clause[-1][2]) or len(clause) >= 6 else clause).append(w)
            if len(rest) >= 6:
                break
        caps = " ".join(re.sub(r"[,.?!]$", "", w[2]) for w in clause).upper()
        script = " ".join(w[2] for w in rest).rstrip(",.") if rest else ""
        titles.append({"style": "combo", "say": " ".join(w[2] for w in clause[:3]), "lines": [caps] + ([script] if script else []),
                       "at": 0.08})
    seen = set()
    for i, w in enumerate(words):
        t = textutil.norm(w[2])
        if ("$" in w[2] or t.endswith("%") or (i + 1 < len(words) and textutil.norm(words[i + 1][2]) in ("percent", "grand", "thousand"))) \
                and t not in seen:
            seen.add(t)
            fig = w[2].strip(",.?!")
            nxt = textutil.norm(words[i + 1][2]) if i + 1 < len(words) else ""
            if nxt in ("grand", "thousand"):
                fig = f"${fig.strip('$')}K"
            titles.append({"style": "number", "say": " ".join(x[2] for x in words[i:i + 2]), "lines": [fig.upper(), " ".join(x[2] for x in words[i + 2:i + 5]).strip(",.")]})
        elif (t.isdigit() or t in textutil.NUM) and i + 1 < len(words) and t not in seen and kind != "breakout":
            unit = textutil.norm(words[i + 1][2])
            if unit in ("days", "jobs", "leads", "weeks", "hours", "months", "calls", "clients", "shops", "spots"):
                seen.add(t)
                num = textutil.NUM.get(t, t)
                prev = [x[2].strip(",.").upper() for x in words[max(0, i - 3):i] if textutil.norm(x[2]) not in textutil.STOP][-2:]
                titles.append({"style": "stack", "say": f"{w[2]} {words[i + 1][2]}",
                               "lines": [" ".join(prev) or "IN", f"{num} {unit.upper()}"]})
    return titles


def init(src, kind, name, root, whisper=None):
    """Draft one plan per hook from a folder of clips (README: names set the roles; one hook = one ad;
    SOP-11 §7 rule 2: one body, change the hooks out)."""
    from . import media, transcribe
    src = Path(src)
    clips = sorted([src] if src.is_file() else [p for p in src.iterdir() if p.suffix.lower() in VIDEO], key=lambda p: p.name.lower())
    if not clips:
        raise SystemExit(f"no video clips in {src}")
    root = Path(root)
    work = root / "work" / slug(name)
    model = whisper or config.PROFILES[kind]["whisper"]
    tx = {}
    for c in clips:
        if role_of(c.stem) == "broll":
            continue
        copy, off = media.working_copy(c, work)
        tx[c] = [[s + off, e + off, w] for s, e, w in transcribe.words(copy, model, work)]
        print(f"  heard {c.name}: {len(tx[c])} words")
    hooks = [c for c in clips if role_of(c.stem) == "hook"] or [None]
    body = [c for c in clips if role_of(c.stem) == "body"]
    cta = [c for c in clips if role_of(c.stem) == "cta"]
    broll = [c for c in clips if role_of(c.stem) == "broll"]
    plans = []
    for n, h in enumerate(hooks, 1):
        order = ([h] if h else []) + body + cta
        sections = []
        for c in order:
            ws = tx[c]
            if not ws:
                continue
            sections.append({"src": str(c.resolve().relative_to(root.resolve())) if c.resolve().is_relative_to(root.resolve()) else str(c),
                             "from": ws[0][0], "to": ws[-1][1], "focus_y": 0.4})
        if cta and sections:
            sections[-1]["tail"] = config.PROFILES[kind]["tail"]
        all_words = [w for c in order for w in tx[c]]
        plan = {"name": f"{name} · H{n} · r1", "kind": kind,
                "brand": {"name": "", "wordmark": "", "tagline": "", "color": "#2E8BFF", "logo": None},
                "card": {"shop": "", "owner": "", "area": "", "price": None, "no_price": False, "services_off": []},
                "sections": sections, "fix": {}, "drop": [], "protect": [],
                "titles": draft_titles(kind, all_words, tx[h] if h else all_words[:12]),
                "music": dict(config.PROFILES[kind]["music"]), "layout": {}}
        last = [w for w in tx[order[-1]]] if order else []
        for k, w in enumerate(last):
            if textutil.norm(w[2]) in ("tap", "click"):
                plan["cta"] = {"say": w[2].strip(",."), "lines": ["TAP", "GET OFFER"]}
                break
        if kind == "agency":
            plan["end_card"] = True
        if broll:
            plan["_broll_available"] = [str(b) for b in broll]
        out = root / "plans" / f"{slug(name)}-h{n}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, indent=2))
        plans.append(out)
    return plans
