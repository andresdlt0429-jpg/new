---
name: edit-ad
description: Edit raw talking-head footage into finished ads with the tools/auto-edit agent, cut to ECL's editing SOPs (SOP-11, 11a-e, SOP-3D, the auto-edit README, the client-ad review notes). Use when asked to edit, cut, re-cut or caption a video ad, an agency ad, a client ad or a 16:9 breakout video, to pull ad footage from Drive, or to QA a finished cut.
---

# Edit an ad

You are the editor. The rules are code in `tools/auto-edit` (every rule and its source: `tools/auto-edit/RULES.md`).
Your job is the part code can't do: pick the right takes, write the titles, and look at every frame before it ships.

If `tools/auto-edit/sops/` holds the SOP files, read the ones the job touches before you start: 05b for a client ad,
05d for a breakout, the auto-edit README for agency ads, 05e for any export question, CLIENT-AD-NOTES for the
latest review calls. **The newest dated ruling wins** (10/19 final over 10/18 over 10/16). They are internal: never commit
them, never paste them anywhere public.

## Who we're editing for

The viewer is an owner-operator of a home-service business (exterior, window and pressure cleaners; detailers).
They're on a phone, thumb on the screen, usually **sound off**. They decide in 1.5 seconds. So:

- **The hook carries the ad.** The first title lands inside 1.5 s and names *them* ("EXTERIOR CLEANERS"), with
  the pain in their words in the gold script ("stuck at $20K a month?").
- **Captions carry the message.** Every spoken word is captioned, in sync. A muted viewer gets the whole ad.
- **Clean beats loud.** The standard: "not overdone but clean, appealing and professional." One idea on screen at a time,
  nothing over the face, nothing under Meta's buttons.
- **Proof is sacred.** A figure on screen must have been said, and claims are the owner's. Never invent a number,
  a review count or a result.

## Pick the kind

| The video | `kind` | Look |
|---|---|---|
| Our own ad (DLT/ECL selling to business owners) | `agency` | gold 3D titles, word-by-word thin captions, logo + CTA end card |
| An ad for a client's shop | `client` | SOP-11b: white caps + gold script, brand callout behind the head, bare 3D offer, name tags |
| A 16:9 explainer or breakout | `breakout` | SOP-11d: red/black slabs, tick lists, stat cards, red-pill captions |

## Workflow

1. **Footage.** Find it (Drive search: the folder name, `mimeType contains 'video/'`), then
   `python -m autoedit fetch <id> raw/<name>.mov`. Name clips by role: `hook1`, `hook2`, `body1`, `cta1`, `broll*`.
   One hook = one ad; one body, swap the hooks (SOP-11 §7). Intake (SOP-11 §4): vertical, audible, one hook per
   file, a body and an ending; if not, say which reject code applies instead of cutting.
2. **Read the script** if there is one (Drive/Docs). It tells you the hook, the offer wording and which claims are
   locked ("15 jobs", "4 days").
3. **Draft.** `python -m autoedit init raw/ --kind <kind> --name "<Shop> · <Service> · <$price|NP>"`.
4. **The plan pass** (the edit that makes an ad good, README). Open each `plans/*.json` and:
   - Pick takes: for a line said many times, take the cleanest full take; the last "one more to seal it" take is
     often the keeper, but not if it drops a word the script needs. Set `from`/`to` (or `keep`).
   - **Hook title** (`combo`, `at: 0.08`): WHITE CAPS = who it's for (2-3 words); script = their pain or the
     outcome, in his words. Max ~22 characters per line.
   - **Two or three titles on the words the ad turns on**: `stack` for a count + unit ("LIVE IN / 4 DAYS"),
     `number` for a spoken `$` or `%`. Put them on the payoff line, never on a throwaway line.
   - **CTA**: `{"say": "tap", "lines": ["TAP", "GET OFFER"]}` on his call to action, whatever words he uses.
   - **Fixes**: every misheard word in the transcript (`fix`), every real repeat that must survive (`protect`),
     every phrase to drop (`drop`).
   - Client ads: the shop's brand colour, callout area and offer lines from the brief card; price only if he says it.
   Then `--plan-only`: every cue must resolve, nothing skipped, the cut list reads right.
5. **Render.** `python -m autoedit render plans/<ad>.json`. One render at a time.
6. **Look before you trust QA.** Read `QA.md`, then *look* at `contact_sheet_phone.jpg` and every `proof_*.jpg`
   (Read the images). QA measures; your eyes catch what it can't: a title that reads badly, a caption that
   mangles a name, an odd crop, the wrong take. Fix the plan, re-render, look again.
7. **Gate.** Ship only when `QA.md` reads PASS (or every FAIL is explained to the user) and the frames look right.
   Listen at every "Heard differently" time: it is either a QA mishearing or a real mis-caption.
8. **Deliver.** Send the phone copy for viewing and the master for upload, plus `QA.md`. Say which claims on screen
   the owner must confirm are true. **Never upload, post, share or launch**: that's the owner's call. Paid tools
   (Higgsfield, AI Producer credits) need a price check and the user's OK first (SOP-11c §3).

## When a rule changes

A new ruling from the SOP owner or the user becomes: a row in `RULES.md` with their words and date, a default in
`autoedit/config.py` or the look in `autoedit/looks.py`, and a test in `tests/` when it can break silently. Run
`python -m unittest discover -s tests` before committing.

## Reporting back

Plain language, short. Lead with the result (link the files), then what you chose and why, then what needs the
user: claims to confirm, missing footage (b-roll, logo), takes you were unsure of.
