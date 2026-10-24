# auto-edit: the editing agent

Raw talking-head clips in, finished ads out, cut to ECL's editing SOPs (SOP-11, 11a-e, SOP-3D, the auto-edit
README and the client-ad review notes). Three kinds of video:

| Kind | Output | Look |
|---|---|---|
| `agency` | 9:16, your own ads (DLT selling to business owners) | gold 3D: combo, stack, number titles; thin word-by-word captions; logo + CTA end card |
| `client` | 9:16, a client's ad | the SOP-11b look: white caps + gold script, brand callout behind the head, bare 3D offer, name tags, clean white captions |
| `breakout` | 16:9 explainer | ECL red/white/black slabs: headline box, stat card, tick list, name tag, red-pill captions |

Every rule and where it lives: [RULES.md](RULES.md).

## Setup (once)

```bash
cd tools/auto-edit
pip install -r requirements.txt      # faster-whisper, numpy, Pillow, fonttools, mediapipe, yt-dlp
# ffmpeg with libass and zscale: Windows `winget install Gyan.FFmpeg`, Mac `brew install ffmpeg`
```

Fonts ship in `fonts/` (Archivo, Montserrat, Poppins, Source Sans 3, Dancing Script; all SIL Open Font License).
The MediaPipe model downloads itself the first time a title needs the person matte.

## Use

```bash
# 1. footage: from Drive (1080p when yt-dlp is installed) or drop files in raw/
python -m autoedit fetch <drive link or file id> raw/hook1.mov

# 2. draft plans: one per hook (hook1, hook2... body... cta), titles drafted by rule
python -m autoedit init raw/ --kind agency --name "DLT · Agency · Exterior cleaners"

# 3. the plan pass: open plans/<name>-h1.json and make it good (sharpen the hook combo, stack the 2-3 words
#    the ad turns on, delete throwaway titles, add fixes for misheard words). Then dry-run:
python -m autoedit render plans/<name>-h1.json --plan-only

# 4. render: master + phone copy + QA.md + contact sheets + proof stills in out/<name>/
python -m autoedit render plans/<name>-h1.json

# a single proof frame at 12.4 s
python -m autoedit render plans/<name>-h1.json --still 12.4
```

`QA.md` must read PASS before anything goes out. Upload the **master**, never the phone copy (SOP-11e).
Nothing here uploads, posts or launches.

## The DLT ad

`plans/dlt-agency-exterior-h1.json` is the 10/24 DLT agency ad. Pull its five clips from the Drive folder
"DLT Marketing office raw footage" (`python -m autoedit fetch <Drive link> raw/IMG_xxxx.MOV`), then render it.

## Layout

```
autoedit/   config (every SOP number) · media · transcribe · textutil · cut · matte · fonts · looks · sound
            render (build, pass A, align, analyse, draw, pass B, finish) · qa · plan · lock · __main__
plans/      one JSON per ad (examples/ holds a client and a breakout demo)
fonts/      open-licence fonts          tests/   python -m unittest discover -s tests
raw/ work/ out/ sops/ models/           local only (git-ignored)
```
