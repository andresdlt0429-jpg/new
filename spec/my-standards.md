# My standards (Andy)

Every correction Andy gives becomes a dated row here, in his exact words.
Where a row conflicts with an older row, the newer row wins.
`spec/SOP-DLT-1-v2-video-standard.md` is still the law; a row that contradicts it gets flagged to Andy, not silently applied.

| # | Date | Andy's exact words | Applies to | Enforced in |
|---|------|--------------------|------------|-------------|
| 1 | 2026-10-22 | "make sure 1440p" | Working copies (proxy/) | `engines/smoke_test.py` `verify_working_copy()`: ffprobe measures every working copy; a source at 1440p or larger must come out exactly 1440x2560 (2560x1440 landscape), and nothing may exceed 1440p. |
| 2 | 2026-10-23 | "make sure this video will be about 35-50secs long" | Office ad (DLT Marketing office raw footage) | `plans/office-ad/plan.json` `output.target_duration_s: [35, 50]`; lock check `duration_in_range` fails outside it. |
