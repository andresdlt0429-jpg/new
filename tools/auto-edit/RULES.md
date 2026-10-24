# Rules map: every SOP rule, where the code enforces it, and how QA checks it

The full SOP files are not in this repo (it is public and they are internal). Put them in `tools/auto-edit/sops/`
(git-ignored) so the `edit-ad` skill can read them. SOP ids: **SOP-11** editor pipeline (05), **11a** 1080p (05a),
**11b** client look (05b), **11c** toolchain (05c), **11d** breakout/landscape (05d), **11e** HD quality (05e),
**SOP-3D** 3D graphics, **README** auto-edit README, **NOTES** CLIENT-AD-NOTES.

Legend: **E** = enforced by the code, **Q** = measured in `QA.md`, **P** = a plan-pass decision (you or Claude).

## Picture and export

| Rule | Source | Where | |
|---|---|---|---|
| 1080x1920 (9:16) or 1920x1080 (breakout), 30 fps, H.264 High | 11a r1, 11e r6 | `config.PROFILES`, `render.pass_b` | E Q |
| Never edit 4K: clips above 1440 get a 1440 working copy, CRF 14, lanczos, 30 fps | 11a §2, 11e r1 | `media.working_copy` | E |
| HDR (HLG/PQ) tone-mapped to BT.709 | 11e r5, README | `media.working_copy` | E |
| BT.709 matrix and tags (`bt709` x3) | 11e r5 | `BT709` flags in `render` | E Q |
| Magnification: crop x punch x move <= copy width / 1080 | 11e r2 | zoom cycles <= 1.18; b-roll scale <= 1.2; landscape b-roll copied at 1920 | E Q |
| No grain, no blur filters, glow 0 on client ads | 11e r4, 11b r8 | `PROFILES[client].glow = 0`; no grain stage exists | E |
| Master CRF 14 slow tune film, AAC 256k | 11e r6 | `render.pass_b`, `finish` | E Q |
| Phone copy: two-pass, BT.709, < 30 MB, never uploaded | 11a r8, 11e r7 | `finish` | E Q |
| One render at a time, below-normal priority | 11a r7, NOTES | `lock.render_lock` | E |
| Everything inside Meta's safe zone (top 14%, bottom 35%) | 11b r17, 11c T7 | `render.place`, caption heights | E Q |

## Cut and sound

| Rule | Source | Where | |
|---|---|---|---|
| Cut where the voice actually stops (voice edges) | NOTES | `cut.section_pieces` | E Q (T1) |
| Silence gate read from the file: max(3x p30 RMS, 12% of peak) | 11c T4 | `cut.threshold` | E |
| Dead air: over 0.4 s cut (agency, breakout); up to 0.6 s kept (client) | README, 11b r13 | `max_pause` per profile | E Q |
| Punch in on each new sentence (agency); alternate jump cuts (breakout, client) | README, 11d §11 | `cut` sentence points, `zoom_cycle` | E |
| Stutters: drop any repeated 1-6 word phrase; protect real repeats | NOTES, 11d §8.2 | `textutil.repeats`, `protect` | E Q |
| Relisten: a word over 0.9 s is re-heard verbatim | NOTES, 11d §8.3 | `transcribe.relisten` | E (listed in QA) |
| Whole-sentence retakes reported (Jaccard 0.45 / 0.34 / 0.24) | 11c T2 | `textutil.retakes` | Q (report only) |
| Edge QA: cut inside a word, leftover fragment, edge under 30 ms | 11c T1 | `qa.edge_flags` | Q |
| 30 ms fade on every cut | 11c T1 | `pass_a` voice chain | E |
| Per-take gain +/-8 dB; denoise only when the floor is above -50 dBFS | 11c T3 | `cut.take_gain`, `pass_a` | E |
| Our own music bed (soul / trap), ducked under the voice, key and bpm vary per ad | NOTES, 11c | `sound.bed`, `sidechaincompress` | E |
| Soft sfx: hit, pop, shimmer, tap | 11b r12 | `sound.sfx`, `draw` | E |
| -14 LUFS, true peak <= -1 dBFS, final limiter | 11e r6, NOTES | `render.finish` (measure, gain, limit, re-measure) | E Q |
| End on the speaker, tail 0.8 s, no b-roll after the CTA | NOTES | `tail`, `video_segments` | E Q |
| No 2-3 frame face flash between cutaways (min_face 0.5) | 11b r10 | `video_segments` | E |
| B-roll on its word; vo b-roll one word early; short shots slowed to 0.6x, never frozen | NOTES, README | `build`, `pass_a` | E |

## Words on screen

| Rule | Source | Where | |
|---|---|---|---|
| A hook title inside the first 1.5 s | README, SOP-3D §0 | `at: 0.08` on the hook title | P Q |
| A figure on screen must have been said | README, 11b §2, SOP-11 §9 | `textutil.figure_said` in `render.schedule` (skips + reports) | E Q |
| Every cue pinned to words he says | README | `render.schedule` | E Q |
| One title at a time, never over the face | 11b r8, 11d §4 | `schedule` lanes, `place` (person matte; shrinks into the band when needed) | E Q |
| Captions on every spoken word, synced (median < 20 ms; breakout < 50 ms) | SOP-3D §7, NOTES, 11d §8.5 | `align_captions` (cut's own audio) | E Q |
| Captions say what he means: fixes for mishearings | 11b r13, 11d §8.4 | `fix` + `config.DEFAULT_FIX` | P E Q ("heard differently") |
| No formatting codes on screen | (10/24 escape) | `looks.captions`, `qa` text check | E Q |
| Agency: combo / stack / number, thin lowercase captions built word by word, logo + CTA end card | README, SOP-3D §6.5 | `looks.agency_*`, `end_card` | E |
| Client: white caps + gold script, gold price only when said, bare 3D offer, name tags, clean white captions, brand-colour callout behind the head, TAP / GET OFFER | 11b (10/19 final) | `looks.client_*`, matte composite | E |
| Client: at most 3 headlines, 4 tags | 11b r6-8 | QA counts | Q |
| Breakout: kicker + red headline box, stat CARD, tick CHECK, name TAG, red-pill captions, left lane only | 11d §3-7 | `looks.bo_*` | E |
| No-price account shows no price | SOP-11 §9 i3 | QA | Q |
| Self-check line: spoken = headline = card price; owner; area | SOP-11 §8.5 | QA | Q |
| Spoken claims listed for the proof bank | SOP-3D §0, README | QA | Q |

## Not built (yet)

- Work-focused crops for landscape b-roll (11b r18, `workfocus.py`): b-roll takes a manual `focus_x` for now.
- Part callout rings on a still wide shot (11b r9).
- STEPS timeline, FUNNEL and META logo for breakouts (11d §3).
- Privacy blur zones for plates and house numbers (11b QA 3): check every b-roll crop by eye.
- Uploading anywhere. Nothing here posts, shares or launches.
