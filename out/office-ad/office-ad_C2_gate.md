# QA gate: office-ad / variant C2

**Verdict: HOLD**  (0 block, 12 hold, 0 note, 24 pass)  ·  2026-10-24 05:57 UTC

Render QA: `out/office-ad/office-ad_C2_DRAFT_qa.json`  ·  Policy: `spec/QA-policy.md`

## HOLD (12)

| Area | Item | Detail |
| :-- | :-- | :-- |
| lock | `script_lock_items` | Andy confirms the hook wording by ear (medium and large-v3 both hear "Exterior cleaners still got 20 grand a month"; "still got" may be "still at").; The guarantee terms for "20 more jobs completely guaranteed" are written on the landing page.; "All live within just four days" is true for a new clie |
| lock | `meta_policy_items` | MEDIUM: Hook "still got 20 grand a month" names the viewer's revenue. Meta's personal-attributes rule rejects ads that imply they know the viewer's financial situation. Safer: "Exterior cleaners who want more booked jobs."; MEDIUM: "$20K A MONTH?" on screen is a money figure. Money on screen can rea |
| lock | `caption_word_fixes` | img_8627: heard "X to your cleaner" -> "Exterior cleaners" (PENDING: Andy to confirm by ear); img_8479: heard "off or" -> "offer" (PENDING: Andy to confirm by ear) |
| lock | `script_is_final` | spec/scripts/office-ad-v1-DRAFT.md is a DRAFT reconstructed from the takes |
| policy | `autopilot` | spoken "on autopilot" (whole spoken text). Close to 'runs without you': can read as little or no work needed. Lower risk when it describes the lead flow, not the money. |
| policy | `personal_attributes` | spoken "still got 20 grand a month" (whole spoken text). Implies the ad knows the viewer's financial situation (Meta personal-attributes rule). |
| policy | `money_on_screen` | on_screen "$20k" (g-B1-1). A money figure on screen can read as an income claim (Meta reviews what is shown, not only what is said). |
| delivery | `clean_render` | this is a DRAFT render; lock the plan and render without --draft to deliver |
| manual | `money_visuals` | No bank/payout screenshots, cash or luxury cars on screen (money and lifestyle visuals read as income proof). |
| manual | `real_testimonials` | Testimonials are real people in their own words; anything they got for it is disclosed on screen. |
| manual | `landing_page` | The landing page and form were reviewed too: Meta checks the ad, page and form together. |
| manual | `phone_check` | Watched once on a real phone, sound on and off. |

## PASS (24)

| Area | Item | Detail |
| :-- | :-- | :-- |
| lock | `no_missing_lines` |  |
| lock | `no_unresolved_off_script` |  |
| lock | `numbers_rule` |  |
| lock | `say_numbers_match_transcript` |  |
| lock | `duration_in_range` |  |
| lock | `speaker_present_for_titles` |  |
| lock | `one_biggest_text_moment` |  |
| render | `master_size` | 1440x2560 @ 30/1 |
| render | `master_duration` | 37.80s (target 35-50s) |
| render | `delivery_size` | 1080x1920 @ 30/1 |
| render | `delivery_duration` | 37.80s (target 35-50s) |
| render | `loudness` | -14.8 LUFS (target -14.0), TP -1.1 dBTP, LRA 3.2 |
| render | `numbers_rule_final` | every number on screen was spoken |
| render | `no_profanity` | no profanity in the kept audio or captions |
| render | `depth_title` | ["t-B3: speaker in front of the slab in 89% of frames, letters hidden median 2% p90 15%, contrast 4.83:1, matte halo 0.05%"] |
| render | `one_biggest_text_moment` | hero g-B1-1 190px = 1.65x the next biggest text (need >= 1.4x), XL count 1 |
| render | `graphics_clear_of_face` | no graphic covers the speaker's face |
| render | `ticks_land_on_words` | 5 ticks, worst offset 33 ms (limit 50 ms) |
| render | `timeline_exact` | planned 37.733s = 1132 frames; video 1132 frames, audio 37.739s decoded (off by +5 ms; limits: 0 frames, 25 ms = one AAC frame) |
| render | `music_silent_in_hook_and_guarantee` | bed -240.0 dBFS RMS in [[0.0, 2.333]] (need silence) |
| render | `music_low_under_speech` | bed 24.9 dB below speech while words are spoken (RMS; need >= 18.0) |
| render | `fade_out_to_silence` | last 50 ms of the final audio peak -74.6 dBFS (need <= -60) |
| render | `effects_soft_and_placed` | 8 effects at peak -26.0 dBFS, none in the hook or guarantee; 1 skipped (silent beat) |
| manual | `footage_rules` | accepted by Andy 2026-10-24: "DONT MIND THE 1 THING YOU SAID" |
