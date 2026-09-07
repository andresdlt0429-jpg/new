---
name: youtube-to-agent
description: Turn a tutorial video into a working skill. Runs the full pipeline — scope the window, watch it locally with /watch, write an independent pass one, get a second read from Gemini, reconcile both into a confidence-labeled spec, then build the skill with skill-creator and run it once for real.
argument-hint: "<video-url> [what you want out of it]"
allowed-tools: Bash, Read, Write, Glob, AskUserQuestion, Skill
user-invocable: true
---

# /youtube-to-agent

One command for the whole video-to-skill pipeline. Run by hand it takes about
twenty minutes while you remember the steps, and about four once it's a habit
— this skill closes that gap.

It also hard-encodes the two rules that are easiest to skip under time
pressure, because skipping them is what makes the output untrustworthy:

1. **Write your own pass before reading Gemini's.** Reading the second
   opinion first doesn't give you two readings, it gives you one reading and
   an echo.
2. **Run the finished skill once, for real, before trusting it.** A skill
   built from a video and never executed is a summary of a video wearing a
   skill's clothes.

Full background and rationale: `VIDEO_PIPELINE.md` at the repo root.

## Step 1 — Scope to a window, not a video

Before either read, decide which part of the video you actually need, and
pass **the same window to both readers**. A dense pass over the six minutes
that mattered beats a sparse scan of forty.

Both readers degrade on length the same way — a fixed budget spread thinner.
Past ~10 minutes `/watch` prints a sparse-scan warning; treat that warning as
a signal to narrow, not as something to note and move past.

If the user gave a timestamp range or named a section, use it. If they gave a
long video and no range, use `AskUserQuestion` to ask which part they need
before burning a pass on the whole thing — unless the video is short (under
~10 minutes), in which case just take the whole thing.

**Watch the unit difference — this is the easiest mistake in the pipeline:**

| Reader | Flag format | Example for 12:30 → 18:00 |
|---|---|---|
| `/watch` | `SS`, `MM:SS`, or `HH:MM:SS` | `--start 12:30 --end 18:00` |
| `gemini_review.py` | seconds only | `--start 750 --end 1080` |

Convert once, up front, and use the same window for both. Mismatched windows
produce two readings of different content, which makes step 4's labels
meaningless.

Also pick `--detail` on purpose rather than letting it default — see the
benchmark table in `VIDEO_PIPELINE.md`. `--resolution 1024` roughly
quadruples image tokens per frame, so pair it with a narrow window or
`--detail efficient`, never with `balanced` across a full video.

## Step 2 — Claude watches

Invoke the `watch` skill on the scoped window. It downloads locally, extracts
frames, and gets a transcript (native captions, or an offline faster-whisper
pass if captions are missing — no cloud calls, no API key).

```
/watch "<url>" --start <MM:SS> --end <MM:SS>
```

Read every frame path it prints. Note the working directory it reports at the
end — step 4 needs the frames still on disk to settle conflicts, so **do not
clean it up until the pipeline finishes.**

## Step 3 — Write pass one (before Gemini, no exceptions)

With the frames and transcript in context and **before running step 4 or
reading anything Gemini produced**, write your own structured read to
`notes/watch-pass.md`:

1. **Overview** — what the video builds or covers
2. **Prerequisites / setup** — what's needed before the walkthrough starts
3. **Steps** — the build in order
4. **Commands, code & config shown on screen** — transcribed as exactly as
   the frames allow
5. **Decisions & rationale** — anything the video explains the *why* of
6. **Uncertain / ambiguous moments** — cuts, unclear audio, anything that
   might be a mistake in the video itself

Include the `/watch` working directory path so step 4 can find the frames.

### Keep the nouns, throw away the numbers

This applies to your pass and to Gemini's, and it's the single highest-value
habit in the pipeline:

- **Names are evidence.** The tool on screen, the error text in the output,
  the exact command string, the phrase the presenter used — vision models are
  genuinely good at these, and so is a frame you actually looked at.
- **Numbers are decoration.** Timestamps, durations, counts, percentages.
  Vision models have no internal clock and no axes to measure against, so
  their numbers are reconstructions rather than readings, routinely off by
  whole seconds even when the structure around them is right. Don't build a
  spec that depends on one.

**The archetype trap:** a vision model reports what this *kind* of video
usually contains, not what this specific one did. Ask about a tutorial and
you get back the shape of tutorials. The distinctive step — the one that is
the actual reason this video was worth saving — is exactly what gets
normalized away into the average. Write down the specific thing, especially
when it seems too small to matter.

## Step 4 — Gemini watches, then reconcile

Only now, with `notes/watch-pass.md` already written:

```bash
python3 scripts/gemini_review.py "<url>" \
  --samples 2 --start <seconds> --end <seconds> \
  --focus "<the specific thing in that window>" \
  > notes/gemini-pass.md
```

Requires `pip install google-genai` and a `GEMINI_API_KEY` in the
environment. If the key is missing, tell the user how to get one
(https://aistudio.google.com/apikey) and stop — never ask them to paste a key
into the conversation, and never write one to a file in this repo.

Then reconcile both reads into one spec:

```
/reconcile-video-reads notes/watch-pass.md notes/gemini-pass.md notes/spec.md
```

**The practical rule:** if the two readings disagree about nearly everything,
the window was too wide. Narrow it and re-run both passes rather than trying
to referee the mess.

**The fluency tell:** when a line in the reconciled spec reads smoother and
more complete than the lines around it, it usually came from training data
rather than from the video. Check that line first.

## Step 5 — Build the skill

Hand `notes/spec.md` to the `skill-creator` skill. Treat the labels as real
constraints, and say so explicitly when invoking it:

- `CONFIRMED` lines — both readings agree; build on them.
- `SINGLE SOURCE` lines — unverified; implement them so they **fail loudly**
  rather than assuming they're right.
- `OPEN QUESTIONS` — not implemented at all. Ask the user about these rather
  than guessing.

## Step 6 — Run it once, for real, and fix what breaks

Not optional, and this is where most people stop. Pick a real input — not a
toy one — run the new skill end to end, and fix what breaks. It usually takes
one round. The first real run is what turns a summary into a tool.

Then keep `notes/spec.md`, `notes/watch-pass.md`, and `notes/gemini-pass.md`
in the new skill's own folder rather than deleting them. Every skill built
this way arrives with its own provenance attached: in three months, when the
skill misbehaves, you can read back to the exact line and see whether it was
`CONFIRMED` or `SINGLE SOURCE` — which tells you immediately whether the bug
is yours or the tutorial's.

Only after this step is done should you clean up the `/watch` working
directory.

## Credit

The `/watch` skill is open source under MIT, built by Brad Bonanno at
https://github.com/bradautomates/claude-video, standing on `yt-dlp` and
`ffmpeg`. `skill-creator` is Anthropic's, in `anthropics/skills`. This
pipeline is a workflow built on top of them, not a replacement for any of
them.
