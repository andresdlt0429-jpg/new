# Video-to-skill pipeline

Turn a tutorial video into a working Claude Code skill, with two independent
readings of the video cross-checked against each other before anything gets
built. Three pieces live in this repo; the last step happens in your own
Claude Code session.

```
video URL
   │
   ├──► /watch  ──────────────────────────► notes/watch-pass.md
   │    (.claude/skills/watch)                (frames + real transcript,
   │    downloads locally, reads frames         local — no cloud calls
   │    + captions/local Whisper transcript      by default)
   │
   └──► scripts/gemini_review.py ──────────► notes/gemini-pass.md
        (sends only the URL — Google           (an independent read —
         fetches + watches it on their side)     no download, no upload)

              notes/watch-pass.md
              notes/gemini-pass.md
                        │
                        ▼
              /reconcile-video-reads
              (.claude/commands)
                        │
                        ▼
                  notes/spec.md
        (one spec, every claim labeled
         CONFIRMED / SINGLE SOURCE / CONFLICT,
         plus an open-questions list)
                        │
                        ▼
              skill-creator  (built into Claude Code)
              hand it notes/spec.md, it interviews
              you and writes a new skill
                        │
                        ▼
              run the new skill once on a real
              input and fix whatever breaks
```

## 1. Read the video locally — `/watch`

```bash
/watch "<video-url>" what does this build?
```

Downloads the video, extracts frames, and gets a transcript from native
captions (free) or — only if captions are missing — a local, offline
`faster-whisper` pass (`pip install faster-whisper` once; no API key, no
cloud call). Cloud Whisper (Groq/OpenAI) is disabled by default in this
project — see `.claude/skills/watch/SKILL.md` for why.

To save the report to a file for reconciliation later:

```bash
python3 .claude/skills/watch/scripts/watch.py "<video-url>" > notes/watch-pass.md
```

## 2. Read the same video independently — Gemini

```bash
pip install google-genai
export GEMINI_API_KEY=...   # https://aistudio.google.com/apikey — your own key,
                             # kept in your shell only, never committed

python3 scripts/gemini_review.py "<video-url>" \
  --samples 2 --end 600 \
  --focus "the whole build, or the specific part you care about" \
  > notes/gemini-pass.md
```

Nothing downloads here — the URL goes to Gemini directly and Google fetches
the video on their own side. `--samples 2` isn't redundancy, it's a
measurement: a claim that shows up in both samples is worth trusting, one
that shows up in only one is more likely the model guessing. See
`scripts/gemini_review.py`'s module docstring for the model-chain fallback
and `--fps` details.

## 3. Reconcile the two reads

```
/reconcile-video-reads notes/watch-pass.md notes/gemini-pass.md notes/spec.md
```

Merges both readings into one spec, claim by claim:

- **CONFIRMED** — both readings agree, safe to build on
- **SINGLE SOURCE** — only one reading saw it, flagged to verify
- **CONFLICT** — they disagree; resolved by reading the actual video frames
  rather than splitting the difference

Ends with an **open questions** list — worth keeping even after the build
ships, since it's a record of what the video never actually covered.

## 4. Build the skill, then run it once for real

Hand `notes/spec.md` to `skill-creator` (Anthropic's built-in skill for
writing skills). Treat the labels as real: build `CONFIRMED` lines in with
confidence, treat `SINGLE SOURCE` lines as unverified, and don't implement
anything under `OPEN QUESTIONS` — ask instead of guessing.

Then **run the new skill once on a real input and fix what breaks.** This
isn't optional — a skill built from a video and never executed is a summary
of a video wearing a skill's clothes. The first real run is what turns it
into a tool, and it usually takes one round of fixes.

Keep `notes/spec.md` (and `notes/watch-pass.md` / `notes/gemini-pass.md`) in
the new skill's own folder rather than deleting them — they're the
provenance for every line in the skill, useful the next time it misbehaves
and you need to check whether the bug is yours or the tutorial's.

## Security notes

- Neither `GEMINI_API_KEY` nor a Groq/OpenAI Whisper key is ever written to
  this repo, logged, or committed — all of them are supplied by you, via
  your own shell environment or `~/.config/watch/.env` (which is
  `.gitignore`d and created at `0600`).
- `/watch` sends nothing to any cloud API by default in this project (see
  `.claude/skills/watch/SKILL.md`).
- `scripts/gemini_review.py` sends only the video *URL* to Google — never
  the video file itself.
