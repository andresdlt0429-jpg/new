"""Every number the SOPs fix, in one place. Each constant names the rule it comes from.

SOP ids: SOP-11 (05 editor pipeline), SOP-11a (05a 1080p), SOP-11b (05b client look), SOP-11c (05c toolchain),
SOP-11d (05d breakout/landscape), SOP-11e (05e HD quality), SOP-3D (sop-3d graphics), README (auto-edit README),
NOTES (CLIENT-AD-NOTES).
"""

FPS = 30                      # SOP-11a rule 1
WORK_SHORT = 1440             # SOP-11e rule 1: 4K becomes a 1440 working copy (short side)
WORK_CRF = 14                 # SOP-11e rule 1: CRF <= 15, lanczos
MASTER_CRF = 14               # SOP-11e rule 6: CRF 14, preset slow, tune film
MASTER_AUDIO = "256k"         # SOP-11e rule 6
LUFS = -14.0                  # SOP-11e rule 6, NOTES "Sound"
PEAK_MAX = -1.0               # NOTES: peak always under -1 dBFS
PHONE_MB = 28                 # SOP-11a rule 8 / SOP-11e rule 7: phone copy under 30 MB, two-pass
HOOK_WINDOW = 1.5             # SOP-3D §0, README: a hook title inside the first 1.5 s
SYNC_MEDIAN_MS = 20           # NOTES / SOP-11c: caption sync median under 20 ms (9:16 ads)
SYNC_MEDIAN_MS_LANDSCAPE = 50 # SOP-11d §8.5: within +/-50 ms for breakouts
STRETCHED_WORD = 0.9          # NOTES "relisten": any word longer than 0.9 s is re-heard
REPEAT_MAX_N = 6              # NOTES: drop any repeated phrase of 1 to 6 words
EDGE_FADE = 0.03              # SOP-11c T1: 30 ms audio fade on every cut
SAFE_TOP, SAFE_BOTTOM = 0.14, 0.35   # SOP-11b rule 17: Reels covers the top 14% and the bottom 35%
FACE_CLEAR = 0.025            # never over the face: a front title keeps this share of H below the chin
TITLE_GAP = 0.06              # one title at a time: the old one leaves before the next lands

# The three kinds of video the SOPs define. Everything a look or a cut does differently lives here.
PROFILES = {
    # ECL/DLT's own 9:16 agency ads: README (autoedit.py "cuts agency ads only"), SOP-3D §10B.
    "agency": dict(
        size=(1080, 1920),
        max_pause=0.40,                     # README: cuts dead air over 0.4 s
        keep_pad=0.12,
        punch="sentence",                   # README: punches in on each new sentence
        zoom_cycle=[1.00, 1.12, 1.05, 1.18],
        title_push=0.04,                    # README: every title that lands mid-shot gets a small push-in
        captions="thin",                    # README: thin lowercase line, built word by word
        captions_under_titles=False,
        caption_y=0.60,
        end_card=True,                      # SOP-3D §6.5 / §7: logo + CTA end card
        tail=1.9,                           # the end card plays over his held take
        music={"style": "soul", "db": -16},
        glow=0.35,                          # README default look: glowing extruded yellow
        whisper="small.en",
        max_headlines=None,
        intro=(0.045, 0.5),                 # a 0.5 s settle-in
    ),
    # A client's 9:16 ad: SOP-11b (10/19 final + 10/20 rule 18), NOTES, SOP-11 §7.
    "client": dict(
        size=(1080, 1920),
        max_pause=0.60,                     # SOP-11b rule 13: keep natural pauses up to 0.6 s
        keep_pad=0.15,
        punch="cuts",                       # a cut inside one take is a small punch instead (README depth.py)
        zoom_cycle=[1.00, 1.08],
        title_push=0.0,
        captions="clean",                   # SOP-11b 10/19: small clean white sentence case, soft shadow, no color
        captions_under_titles=False,        # titles_hide_captions
        caption_y=0.50,                     # SOP-11b rule 17 bands
        end_card=False,                     # NOTES: end on the speaker, no cut-away
        tail=0.8,                           # NOTES: tail 0.8
        music={"style": "soul", "db": -16},
        glow=0.0,                           # SOP-11b rule 8: glow 0, rim 0
        whisper="medium.en",                # SOP-11c: medium.en for client ads
        max_headlines=3,                    # SOP-11b rule 7/8
        max_tags=4,                         # SOP-11b rule 6
        intro=(0.04, 0.5),                  # SOP-11b rule 8: 0.5 s settle
    ),
    # ECL/DLT's own 16:9 breakout and explainer videos: SOP-11d.
    "breakout": dict(
        size=(1920, 1080),
        max_pause=0.40,
        keep_pad=0.12,
        punch="alternate",                  # SOP-11d §11: punch in 5-17% on alternate jump cuts
        zoom_cycle=[1.00, 1.12, 1.00, 1.17],
        title_push=0.0,
        captions="pill",                    # SOP-11d §6: white Montserrat 800, spoken word on a red pill
        captions_under_titles=True,         # SOP-11d §6: captions stay on under graphics
        caption_y=0.885,
        end_card=False,
        tail=0.6,                           # SOP-11d §7: 0.6 s fade out
        music={"style": "soul", "db": -22}, # SOP-11d §7: soft bed at -22 dB
        glow=0.0,
        whisper="medium.en",                # SOP-11d §8: medium.en for the cut
        max_headlines=None,
        intro=(0.04, 0.5),
    ),
}

# Standing mishearing fixes, applied to every plan before its own `fix` (SOP-11d §8.4 keeps the same kind of list).
DEFAULT_FIX = {
    "get offered": "get offer",
    "tap get offered": "tap get offer",
    "ECO marketing": "ECL Marketing",
    "Andrew meta": "Andromeda",
    "ceramic cutting": "ceramic coating",
}

# Look colours. Agency gold/yellow: README default look. ECL red/black: SOP-11d §5.
GOLD, YELLOW, WHITE, PEARL = "#F2C14E", "#FFD21F", "#FFFFFF", "#F4EFE6"
GOLD_DEPTH = ("#8A5A12", "#2E1C04")
RED_TOP, RED_BOTTOM, RED_DEPTH = "#FF3B3B", "#C4000A", ("#4A050A", "#0A0002")
CARD_BLACK, MUTED = "#0C0C0E", "#C9CDD3"
