"""python -m autoedit <command>

  init   <clips folder or file> --kind agency|client|breakout --name "DLT · Agency · Exterior · r1"
         Transcribe the clips and draft one plan per hook in plans/ (README: names set the roles; one hook = one ad).
  render <plan.json> [--plan-only] [--still SECONDS] [--no-phone] [--no-qa]
         Cut, design, mix and export the master + phone copy, then run QA.
  qa     <plan.json>       Re-run QA on the last render of a plan.
  fetch  <drive link or file id> <dest>   Pull footage from Google Drive (yt-dlp 1080p when installed, SOP-11a §3).
"""
import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fetch(ref, dest):
    import re
    m = re.search(r"/d/([\w-]{20,})|id=([\w-]{20,})|^([\w-]{20,})$", ref)
    if not m:
        raise SystemExit("give a Drive link or file id")
    fid = next(g for g in m.groups() if g)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("yt-dlp"):
        r = subprocess.run(["yt-dlp", "-f", "137+140", "--merge-output-format", "mp4", "-o", str(dest),
                            f"https://drive.google.com/file/d/{fid}/view"])
        if r.returncode == 0:
            return dest
        print("  no 1080p stream yet; downloading the original")
    url = f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
    urllib.request.urlretrieve(url, dest)
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(prog="autoedit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("init")
    a.add_argument("src")
    a.add_argument("--kind", default="agency", choices=["agency", "client", "breakout"])
    a.add_argument("--name", required=True)
    r = sub.add_parser("render")
    r.add_argument("plan")
    r.add_argument("--plan-only", action="store_true")
    r.add_argument("--still", type=float)
    r.add_argument("--no-phone", action="store_true")
    r.add_argument("--no-qa", action="store_true")
    q = sub.add_parser("qa")
    q.add_argument("plan")
    f = sub.add_parser("fetch")
    f.add_argument("ref")
    f.add_argument("dest")
    args = ap.parse_args(argv)

    if args.cmd == "init":
        from .plan import init
        for p in init(args.src, args.kind, args.name, ROOT):
            print(f"  plan: {p}")
        print("  next: open each plan, sharpen the titles (the plan pass), then: python -m autoedit render <plan>")
    elif args.cmd == "render":
        from .render import render
        render(args.plan, plan_only=args.plan_only, still=args.still, phone=not args.no_phone, qa=not args.no_qa)
    elif args.cmd == "qa":
        from .plan import load, slug
        from . import qa
        plan = load(args.plan)
        tl = Path(plan["_root"]) / "work" / slug(plan["name"]) / "timeline.json"
        ed = json.loads(tl.read_text())
        ed["plan"] = plan
        sys.exit(1 if qa.run(ed) else 0)
    elif args.cmd == "fetch":
        print(f"  saved {fetch(args.ref, args.dest)}")


if __name__ == "__main__":
    main()
