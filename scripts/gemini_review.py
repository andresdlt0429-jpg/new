#!/usr/bin/env python3
"""Send a YouTube (or other Gemini-supported) video URL straight to Gemini.

Companion to the /watch skill in .claude/skills/watch, not a replacement for
it: /watch downloads the video locally (frames + transcript, no cloud calls
by default). This script sends nothing but the URL — Gemini fetches the
video on Google's own side, so nothing is downloaded or uploaded here. Run
both and compare: they're independent readings of the same video, and
agreement between them is more trustworthy than either alone.

Requires:
  pip install google-genai
  export GEMINI_API_KEY=...   (get one at https://aistudio.google.com/apikey
                                — keep it in your shell/environment, never
                                commit it to a file or paste it into chat)

Usage:
  python3 scripts/gemini_review.py "<video-url>" \
      --samples 2 --end 600 \
      --focus "the whole build, or the specific part you care about" \
      > notes/gemini-pass.md
"""
from __future__ import annotations

import argparse
import os
import sys
import time

try:
    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors
except ImportError:
    print(
        "google-genai is not installed. Install with: pip install google-genai",
        file=sys.stderr,
    )
    raise SystemExit(2)


# Free-tier keys will happily list access to the pro models and then return a
# 429 (RESOURCE_EXHAUSTED) the moment you actually call one. Falling forward
# through the flash models — cheapest and most available first — means a
# quota miss on one model doesn't kill the whole run.
MODEL_CHAIN = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

DEFAULT_PROMPT_TEMPLATE = """You are reviewing a tutorial video. Watch it and answer clearly and \
specifically — cite timestamps where useful. Focus on: {focus}

Give a structured summary: what the video builds, the key steps in order, \
and any commands, code, or configuration shown on screen. Call out anything \
ambiguous, or that looks like a mistake in the video."""


def _offset(seconds: float | None) -> str | None:
    return f"{seconds:.0f}s" if seconds is not None else None


def build_contents(
    url: str,
    prompt: str,
    start: float | None,
    end: float | None,
    fps: float | None = None,
) -> types.Content:
    video_metadata = None
    if start is not None or end is not None or fps is not None:
        video_metadata = types.VideoMetadata(
            start_offset=_offset(start if start is not None else 0.0),
            end_offset=_offset(end),
            fps=fps,
        )
    return types.Content(
        parts=[
            types.Part(
                file_data=types.FileData(file_uri=url),
                video_metadata=video_metadata,
            ),
            types.Part(text=prompt),
        ]
    )


def call_with_fallback(client, contents: types.Content, models: list[str]) -> tuple[str, str]:
    """Try each model in order, falling forward on quota/availability errors.

    Returns (response_text, model_used). Raises SystemExit if every model fails.
    """
    last_exc: Exception | None = None
    for model in models:
        try:
            print(f"[gemini_review] trying {model}…", file=sys.stderr)
            response = client.models.generate_content(model=model, contents=contents)
            text = (response.text or "").strip()
            if text:
                return text, model
            last_exc = RuntimeError(f"{model} returned an empty response")
            print(f"[gemini_review] {model} returned no text — trying next model", file=sys.stderr)
        except genai_errors.ClientError as exc:
            # Covers 429 RESOURCE_EXHAUSTED (quota) and 404 (model unavailable
            # on this key/tier) — both mean "try the next model", not "give up".
            last_exc = exc
            print(f"[gemini_review] {model} failed ({exc}) — trying next model", file=sys.stderr)
            continue

    raise SystemExit(f"All models in the chain failed. Last error: {last_exc}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Send a video URL to Gemini for review — no download, Google fetches it.",
    )
    ap.add_argument("url", help="YouTube (or other Gemini-supported) video URL")
    ap.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Independent samples to take (default 1). This is a measurement, not redundancy: "
             "Gemini is a stochastic witness, so the same prompt on the same video can come back "
             "with a materially different spec each time. A claim that shows up in every sample is "
             "worth building on; one that shows up in only one sample is more likely the model guessing.",
    )
    ap.add_argument("--start", type=float, default=None, help="Clip start, in seconds")
    ap.add_argument("--end", type=float, default=None, help="Clip end, in seconds")
    ap.add_argument(
        "--fps",
        type=float,
        default=None,
        help="Frames per second Gemini samples from the video (0.0-24.0, default 1.0). "
             "1 fps is plenty for someone talking through a build; raise it only if on-screen "
             "commands or fast visual changes are getting missed — it multiplies token cost.",
    )
    ap.add_argument(
        "--focus",
        type=str,
        default="the whole video",
        help="What to focus the review on (default: the whole video)",
    )
    ap.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Override the default review prompt entirely (--focus is ignored if set)",
    )
    ap.add_argument(
        "--models",
        type=str,
        default=None,
        help=f"Comma-separated model chain override (default: {','.join(MODEL_CHAIN)})",
    )
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit(
            "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey, "
            "then `export GEMINI_API_KEY=...` in your shell — never commit it to a file or paste "
            "it into a chat."
        )
    if args.samples < 1:
        raise SystemExit("--samples must be at least 1")
    if args.end is not None and args.start is not None and args.end <= args.start:
        raise SystemExit("--end must be greater than --start")
    if args.fps is not None and not (0.0 <= args.fps <= 24.0):
        raise SystemExit("--fps must be between 0.0 and 24.0")

    client = genai.Client(api_key=api_key)
    prompt = args.prompt or DEFAULT_PROMPT_TEMPLATE.format(focus=args.focus)
    models = [m.strip() for m in args.models.split(",")] if args.models else MODEL_CHAIN
    contents = build_contents(args.url, prompt, args.start, args.end, fps=args.fps)

    print(f"# Gemini review: {args.url}")
    print()
    if args.start is not None or args.end is not None:
        end_label = f"{args.end:.0f}s" if args.end is not None else "end"
        print(f"- **Range:** {args.start or 0:.0f}s – {end_label}")
    print(f"- **Focus:** {args.focus}")
    print(f"- **Samples:** {args.samples}")
    print()

    for i in range(1, args.samples + 1):
        text, model_used = call_with_fallback(client, contents, models)
        print(f"## Sample {i} ({model_used})")
        print()
        print(text)
        print()
        if i < args.samples:
            time.sleep(1)  # small courtesy delay between calls

    if args.samples > 1:
        print("---")
        print(
            "_Compare the samples above: a claim that appears in every sample is well-supported; "
            "one that appears in only one is more likely the model guessing._"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
