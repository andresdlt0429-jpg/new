# QA policy

**Generated from `spec/policy-rules.json` by `python3 engines/policy_scan.py --write-policy`. Do not edit by hand.**

Stand-in for SOP 6.2 until `spec/SOP-DLT-1-v2-video-standard.md` arrives. The QA gate (`engines/qa_gate.py`) runs every rule below on what each ad variant says (kept words) and shows (titles, graphics, tick items).

Sources: **policy_safe**: Launchpad Ads: Policy-Safe One-Pagers (Andy's Drive, 2026-10-16), red-flag words table, built from Meta Advertising Standards and FTC guidance; **brief**: spec/ad-editor-brief.md (DLT Ad Editor Brief), Chapter 04 Words / Footage; **rule4**: Andy operating rule 4: never put a number on screen that was not spoken in that video

## Severity

| Level | Meaning |
| :-- | :-- |
| BLOCK | Cannot ship. Fix the words or the footage, then re-render. |
| HOLD | Ships only after Andy accepts it in writing (plans/<ad>/acceptances.json, his exact words). |
| NOTE | Worth knowing; never stops a render. |

## Automatic rules

| Rule | Level | Checks | Why | Say instead | Source |
| :-- | :-- | :-- | :-- | :-- | :-- |
| `income_claim` | BLOCK | spoken, on_screen, ad_copy | Promise of income. Meta bans promises of financial gain; the FTC needs written proof, dates and the share of buyers who got that result. | Describe what they get (ads, scripts, follow-up), not the money. | policy_safe |
| `money_guarantee` | BLOCK | spoken, on_screen, ad_copy | Guarantee of financial gain: banned by Meta. | "If you do the work and miss the goal we set together, we keep going at no extra cost." | policy_safe |
| `jobs_guarantee` | HOLD | spoken, on_screen, ad_copy | A results guarantee (jobs, not money). Allowed in principle, but the terms (what 'booked' means, the conditions) must be written on the landing page, which Meta reviews with the ad. | Keep it, with the terms on the page; or 'If I don't book you the jobs, you don't pay.' | policy_safe |
| `own_boss_freedom` | BLOCK | spoken, on_screen, ad_copy | One of Meta's own examples of a banned income ad. | "Add a high-ticket service", "start a business with support". | policy_safe |
| `no_work_needed` | BLOCK | spoken, on_screen, ad_copy | Meta bans claims that little or no work is needed to make money. | "This takes real work." | policy_safe |
| `autopilot` | HOLD | spoken, on_screen, ad_copy | Close to 'runs without you': can read as little or no work needed. Lower risk when it describes the lead flow, not the money. | "Jobs coming in every week", "we follow up with every lead for you". | policy_safe |
| `get_rich` | BLOCK | spoken, on_screen, ad_copy | Get-rich-quick claims are banned. | "Your ads can start in your first week" (only if true). | policy_safe |
| `personal_attributes` | HOLD | spoken, on_screen, ad_copy | Implies the ad knows the viewer's financial situation (Meta personal-attributes rule). | Name the trade, not their money: "Exterior cleaners who want more booked jobs." | policy_safe |
| `money_on_screen` | HOLD | on_screen | A money figure on screen can read as an income claim (Meta reviews what is shown, not only what is said). | Show the trade or the job count instead of a dollar figure. | policy_safe |
| `dollar_testimonial` | BLOCK | spoken, on_screen, ad_copy | An earnings claim through someone else's mouth is still an earnings claim; the FTC wants the typical result. | Testimonial about the experience, no dollar amounts. | policy_safe |
| `sensational_text` | NOTE | on_screen, ad_copy | Sensational, low-quality text gets rejected. (All-caps on-screen titles are the brief's style; this rule is about '!!!', WARNING, BREAKING.) | Plain wording. | policy_safe |
| `platform_or_brand` | HOLD | spoken, on_screen, ad_copy | Making Facebook/Instagram the focus or naming other brands breaks brand and trademark rules. | "Ads in your city", "a lighting supplier". | policy_safe |
| `profanity` | BLOCK | spoken, on_screen, ad_copy | Profanity is banned in ads, even masked; Meta can limit delivery. | Remove it. | brief |

Rule 4 (every on-screen number was spoken) and profanity in the kept audio are also checked by the renderer's QA on the final video.

## Manual checks (Andy or a reviewer ticks these)

- [ ] `money_visuals`: No bank/payout screenshots, cash or luxury cars on screen (money and lifestyle visuals read as income proof). (policy_safe)
- [ ] `real_testimonials`: Testimonials are real people in their own words; anything they got for it is disclosed on screen. (policy_safe)
- [ ] `landing_page`: The landing page and form were reviewed too: Meta checks the ad, page and form together. (policy_safe)
- [ ] `footage_rules`: Footage: outdoors, real over stock; no offices, desks, cars or white walls; never a person on a ladder with a buzzing phone. (brief)
- [ ] `phone_check`: Watched once on a real phone, sound on and off. (brief)

## Accepting a HOLD

A HOLD item ships only when Andy accepts it in writing. The gate reads `plans/<ad>/acceptances.json`:

```json
[{"item": "jobs_guarantee", "variant": "A", "by": "Andy", "date": "YYYY-MM-DD", "words": "his exact words"}]
```

The same words go into `spec/my-standards.md` as a dated row.
