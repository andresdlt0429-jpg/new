---
description: Reconcile a /watch read and a Gemini review of the same video into one confidence-labeled spec
argument-hint: <watch-output.md> <gemini-output.md> [output-path, default notes/spec.md]
allowed-tools: Read, Write, Glob
---

You are reconciling two independent reads of the same video into one trustworthy spec.

- **Reading A — local:** `$1` (a pass-one write-up produced from the `/watch` skill: frames it actually saw plus a real transcript, either native captions or a local Whisper pass — grounded in the actual video, but a human never checked it, and it was written before Reading B was consulted to avoid anchoring)
- **Reading B — Gemini:** `$2` (from `scripts/gemini_review.py`: Google's own model watching the same video via URL — an independent read, but Gemini can also fill gaps from training data rather than what's actually on screen)

Read both files in full before writing anything. If `$2` contains multiple `## Sample N` sections (from `--samples > 1`), treat agreement *between those samples* as part of Reading B's own confidence, not as a third independent reading — a claim only both samples agree on counts as one solid Reading B claim; a claim that shows up in just one sample is already suspect before you even compare it to Reading A.

## What to do

Go claim by claim through both readings — what the video builds, the steps in order, specific commands/code/config shown on screen, and anything either reading flagged as unclear or a possible mistake. For each distinct claim, label it with exactly one of:

- **CONFIRMED** — both readings agree. Safe to build on without re-checking.
- **SINGLE SOURCE** — only one reading saw it. Keep it, mark which reading, and flag it for verification before anything load-bears on it.
- **CONFLICT** — the readings disagree. Do not average or split the difference between two guesses. If Reading A references frame paths (or the working directory from the original `/watch` run is still around), go `Read` the relevant frame(s) yourself and resolve the conflict against the actual pixels; say what you saw. If no frames are available (e.g. `/watch` ran at `--detail transcript`, or the frame directory has since been cleaned up), say so plainly and leave it as an open conflict rather than picking one side arbitrarily.

## Output

Write the reconciled spec to `${3:-notes/spec.md}` (create the `notes/` directory if it doesn't already exist) as markdown, structured:

1. **Summary** — a few sentences on what the video builds/covers overall.
2. **Steps / build spec** — the reconciled walkthrough in order, each claim tagged inline or grouped with its `CONFIRMED` / `SINGLE SOURCE` / `CONFLICT` label. This is the part someone would actually follow to redo the build.
3. **Open questions** — a plain list of anything neither reading covered, any unresolved `CONFLICT`, or anything that seems missing given what the spec is trying to build. Keep this list even after the build ships — it's a record of what the tutorial never actually taught, i.e. what's most likely to break later.

Keep the tone plain and specific — this file is meant to be read and acted on, not admired.
