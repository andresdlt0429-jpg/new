# Video-to-skill pipeline

Turn a tutorial video into a working Claude Code skill, with two independent
readings of the video cross-checked against each other before anything gets
built. Three pieces live in this repo; the last step happens in your own
Claude Code session.

```
video URL
   │
   ▼
1. Scope the video
   Name the section you actually need (--start/--end) and pick --detail
   on purpose (see "Choosing --detail" below) — a window beats a whole video.
   │
   ▼
2. Claude watches — /watch (.claude/skills/watch)
   Downloads locally, reads frames + a real transcript (native captions,
   or local offline faster-whisper if missing) into its own context.
   No cloud calls by default.
   │
   ▼
3. You write pass one — BEFORE looking at Gemini
   Claude writes its own structured read of what it just saw, from its
   own context only, to notes/watch-pass.md. Do this before step 4 —
   reading Gemini's take first would anchor pass one to it.
   │
   ▼
4. Gemini watches — scripts/gemini_review.py
   Same URL, sent straight to Gemini — Google fetches and watches the
   video on their own side, nothing downloads here. Two samples so a
   guess is visible as a guess. → notes/gemini-pass.md
   │
   ▼
5. Reconcile — /reconcile-video-reads (.claude/commands)
   notes/watch-pass.md + notes/gemini-pass.md → notes/spec.md
   One spec, every claim labeled CONFIRMED / SINGLE SOURCE / CONFLICT,
   plus an open-questions list.
   │
   ▼
6. Build it, then run it once
   Hand notes/spec.md to skill-creator (built into Claude Code) — it
   interviews you and writes the skill. Then run the new skill once on
   a real input and fix what breaks. This step is not optional.
```

## 1. Scope the video

Decide which part of the video you actually need **before either pass**, and
give the same window to both readers:

```bash
# Claude pass, focused
/watch "<url>" --start 12:30 --end 18:00 --resolution 1024

# Gemini pass, same window — note the units differ
python3 scripts/gemini_review.py "<url>" --start 750 --end 1080 \
  --focus "<the specific thing in that window>"
```

`/watch` takes `SS` / `MM:SS` / `HH:MM:SS`; `gemini_review.py` takes plain
seconds (750 = 12:30, 1080 = 18:00). Convert once up front — mismatched
windows mean the two readers are reading different content, which makes the
labels in step 5 meaningless.

Both readers degrade on length the same way: a fixed budget spread thinner.
Past about ten minutes `/watch` prints a sparse-scan warning, and a sparse
pass over forty minutes tells you far less than a dense pass over the six
that mattered. Focused mode gets denser per-second budgets (capped at 2 fps)
and filters the transcript to the same range automatically, so the two
streams stay aligned.

**The practical rule:** if the two readings disagree about nearly everything
in step 5, the window was too wide. Narrow it and run both again rather than
trying to referee the mess.

Don't let `--detail` default either — pick it on purpose. See
["Choosing --detail"](#choosing---detail-a-real-benchmark) below for real
numbers.

> **Worth 30 seconds:** watch the video yourself at 2x first with a finger on
> the spacebar and write down two timestamps. The pipeline replaces the note
> taking, not the deciding — and every step after this one gets cheaper and
> more accurate.

## 2. Claude watches — `/watch`

```bash
/watch "<video-url>" what does this build?
```

Downloads the video, extracts frames, and gets a transcript from native
captions (free) or — only if captions are missing — a local, offline
`faster-whisper` pass (`pip install faster-whisper` once; no API key, no
cloud call). Cloud Whisper (Groq/OpenAI) is disabled by default in this
project — see `.claude/skills/watch/SKILL.md` for why.

This step happens interactively, inside your Claude Code session — Claude
reads the extracted frames and transcript into its own context via the
`Read` tool. There's no file to redirect to yet; that's step 3.

## 3. Write pass one — before you look at Gemini

With the frames and transcript still in context from step 2, have Claude
write its own structured understanding of the video to `notes/watch-pass.md`
— **before** running or reading anything from Gemini. Looking at Gemini's
read first would anchor pass one to it instead of it being an independent
reading.

A reasonable structure (adapt to what the video actually covers):

1. **Overview** — what the video builds or covers, in a few sentences
2. **Prerequisites / setup** — anything needed before the walkthrough starts
3. **Steps** — the build/walkthrough in order
4. **Commands, code & config shown on screen** — transcribed as exactly as
   the frames allow
5. **Decisions & rationale** — anything the video explains the *why* of
6. **Uncertain / ambiguous moments** — cuts, unclear audio, anything that
   might be a mistake in the video itself

Note the `/watch` working directory (printed at the end of its report) or
key frame paths somewhere in `notes/watch-pass.md` if you're not deleting it
right away — `/reconcile-video-reads` re-checks the actual frames to settle
any `CONFLICT` with Gemini's read, and can only do that if the frames (or a
path to them) are still findable.

## 4. Gemini watches — independent second read

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

## 5. Reconcile the two reads

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

## 6. Build the skill, then run it once for real

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

## Choosing `--detail`, a real benchmark

Real numbers from a 49m8s YouTube video at 1280x720 with English auto
captions — a long, mostly static screen recording, which is the case that
stresses the frame cap hardest. Your own numbers will vary with video
length and motion, but the shape holds:

| Mode | Frames | Extraction time | Image tokens | Use it when |
|---|---|---|---|---|
| `transcript` | 0 | ~4.5s | 0 | Someone is talking, not typing |
| `efficient` | 50 | ~0.5s | ~9.8k | First look at a long screen recording |
| `balanced` | 100 | ~20.9s | ~19.7k | Default, when you need to read the screen |
| `token-burner` | 116 | ~21.0s | ~22.8k | High-motion video where 100 frames misses cuts |

Token cost is dominated by frames — every frame is an image. `transcript`
mode skips the video download entirely when captions exist, so a talking-head
tutorial costs a few seconds and zero image tokens. `efficient` reconstructs
keyframes only, which makes it roughly 40x faster than the scene modes, and
on low-motion footage can return *more* frames than `balanced` — "efficient"
means fast extraction, not fewer frames.

`--resolution 1024` roughly quadruples image tokens per frame — pair it with
`efficient` or a narrow `--start`/`--end` window rather than `balanced`
across a full video. Reading a terminal is worth paying the extra resolution
for; reading a terminal for 49 minutes straight is not.

## Once it's a habit: `/youtube-to-agent`

Once you've run these steps by hand a couple of times, the whole pipeline is
one command — `.claude/skills/youtube-to-agent/SKILL.md` wraps all six steps
so the next video is a single line:

```
/youtube-to-agent "<video-url>" what I want out of it
```

Besides saving the twenty-minutes-while-you-remember-the-steps overhead, the
skill hard-encodes the two rules that are easiest to skip under time
pressure: writing your own pass before reading Gemini's, and running the
finished skill once before trusting it.

## Reading the output: nouns vs. numbers

Both readers are good at **naming** things — the tool on screen, the error
text, the exact command string, the phrase the presenter used. Neither is
good at **numbers**: vision models have no internal clock and no axes to
measure against, so timestamps, durations and counts are reconstructions
rather than readings, routinely off by whole seconds even when the structure
around them is right. Treat names as evidence and numbers as decoration.

Two related traps worth knowing by name:

- **The archetype trap** — a model reports what this *kind* of video usually
  contains rather than what this specific one did. The distinctive step, the
  actual reason you saved the video, is exactly what gets normalized away
  into the average. This is why step 5 labels single-source claims instead of
  merging everything into one confident document.
- **The fluency tell** — when a line in the spec reads smoother and more
  complete than the lines around it, it usually came from training data
  rather than from the video. Check that one first.

## Security notes

- Neither `GEMINI_API_KEY` nor a Groq/OpenAI Whisper key is ever written to
  this repo, logged, or committed — all of them are supplied by you, via
  your own shell environment or `~/.config/watch/.env` (which is
  `.gitignore`d and created at `0600`).
- `/watch` sends nothing to any cloud API by default in this project (see
  `.claude/skills/watch/SKILL.md`).
- `scripts/gemini_review.py` sends only the video *URL* to Google — never
  the video file itself.

## Credit and provenance

- The `/watch` skill is open source under MIT, built by Brad Bonanno at
  [github.com/bradautomates/claude-video](https://github.com/bradautomates/claude-video),
  vendored into `.claude/skills/watch/` here (with its LICENSE) because
  `/plugin` isn't available in this environment. It stands on `yt-dlp` and
  `ffmpeg`.
- `skill-creator` is Anthropic's, in `anthropics/skills`.
- The `--detail` benchmark table is a measured run against a single 49-minute
  720p screen recording, published in the claude-video repository. The Gemini
  token figure quoted elsewhere comes from a real 90-second run at 1 fps on a
  free-tier key. Your videos will differ — treat both as shape, not as
  guarantees.
- Everything else here (`scripts/gemini_review.py`,
  `/reconcile-video-reads`, `/youtube-to-agent`, this document) is workflow
  built on top of those tools, not a replacement for any of them.
