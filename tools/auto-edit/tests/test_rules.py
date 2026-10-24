"""Fast checks of the rules that are easy to break. Run: python -m unittest discover -s tests (from tools/auto-edit)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autoedit import config, cut, fonts, looks, plan, textutil  # noqa: E402


def words(text, start=0.0, step=0.3):
    return [[start + i * step, start + i * step + 0.25, w] for i, w in enumerate(text.split())]


class TextRules(unittest.TestCase):
    def test_figure_must_be_said(self):
        self.assertTrue(textutil.figure_said(["LIVE IN", "4 DAYS"], "all live within just four days")[0])
        self.assertTrue(textutil.figure_said(["stuck at $20K a month?"], "stuck at $20K a month")[0])
        ok, missing = textutil.figure_said(["$299 FULL DETAIL"], "full detail this month")
        self.assertFalse(ok)
        self.assertEqual(missing, ["299"])

    def test_fix_word_for_word_keeps_timing(self):
        ws = textutil.apply_fixes(words("tap get offered below"), {"get offered": "get offer"})
        self.assertEqual([w[2] for w in ws], ["tap", "get", "offer", "below"])
        self.assertAlmostEqual(ws[2][0], 0.6)

    def test_fix_phrase_merges(self):
        ws = textutil.apply_fixes(words("Next to your cleaner, stuck"), {"next to your": "Exterior"})
        self.assertEqual([w[2] for w in ws], ["Exterior", "cleaner,", "stuck"])
        self.assertAlmostEqual(ws[0][1], 0.85)

    def test_find_phrase_uses_stems(self):
        ws = words("exterior cleaners stuck at")
        self.assertEqual(textutil.find_phrase(ws, "Exterior cleaner"), (0, 2))
        self.assertIsNone(textutil.find_phrase(ws, "window cleaner"))

    def test_repeats(self):
        toks = [textutil.norm(t) for t in "the exterior gets a the exterior gets a complete wash".split()]
        self.assertEqual(textutil.repeats(toks), [(0, 4)])
        self.assertEqual(textutil.repeats(toks, protect=["the exterior gets a the exterior"]), [])

    def test_retakes(self):
        ws = words("so stop chasing referrals get real jobs on autopilot", 0.0) + \
             words("so stop chasing referrals get actual real jobs on autopilot", 5.0)
        found = textutil.retakes(ws)
        self.assertTrue(found and found[0]["verdict"] == "retake")


class CutRules(unittest.TestCase):
    def setUp(self):
        sr = 16000
        rng = np.random.default_rng(0)
        a = rng.standard_normal(sr * 6).astype(np.float32) * 0.0003            # room tone
        for s, e in [(0.5, 1.5), (1.65, 2.6), (3.6, 4.6)]:                       # speech, a 0.15 s and a 1.0 s gap
            n = int((e - s) * sr)
            a[int(s * sr):int(s * sr) + n] += 0.2 * np.sin(np.arange(n) * 2 * np.pi * 180 / sr)
        self.audio = a
        self.words = [[0.5, 1.0, "we"], [1.0, 1.5, "build"], [1.65, 2.1, "the"], [2.1, 2.6, "offer."],
                      [3.6, 4.1, "all"], [4.1, 4.6, "live."]]

    def test_dead_air_cut_and_edges(self):
        prof = dict(config.PROFILES["agency"], punch="none")
        pieces, kept, rep = cut.section_pieces({"src": "x", "from": 0.5, "to": 4.6}, self.words, self.audio, prof)
        self.assertEqual(len(pieces), 2)                                          # the 1.0 s gap goes, the 0.15 s stays
        self.assertLess(pieces[0]["b"], 2.9)
        self.assertGreater(pieces[1]["a"], 3.3)
        self.assertEqual(len(kept), 6)
        self.assertLessEqual(pieces[0]["a"], 0.5)                                  # never clips his first word

    def test_sentence_punch(self):
        pieces, _, _ = cut.section_pieces({"src": "x", "from": 0.5, "to": 4.6}, self.words, self.audio,
                                          config.PROFILES["agency"])
        self.assertGreaterEqual(len(pieces), 2)

    def test_client_keeps_short_pauses(self):
        pieces, _, _ = cut.section_pieces({"src": "x", "from": 0.5, "to": 2.6}, self.words[:4], self.audio,
                                          config.PROFILES["client"])
        self.assertEqual(len(pieces), 1)


class LookRules(unittest.TestCase):
    def test_captions_never_show_codes(self):
        faces = fonts.ensure(Path(__file__).resolve().parent.parent / "work" / "_test")
        c = looks.Canvas((1080, 1920), faces)
        looks.captions(c, words("tired of knocking {doors} all day."), "thin", lambda t: 1150)
        import re
        for ev in c.caps:
            text = re.sub(r"\{[^}]*\}", "", ev.split(",", 9)[9])
            self.assertNotIn("\\", text)
            self.assertNotIn("&H", text)

    def test_fit_uses_libass_scale(self):
        faces = fonts.ensure(Path(__file__).resolve().parent.parent / "work" / "_test")
        s = fonts.fit(faces, "head", "EXTERIOR CLEANERS", 400, 972)
        self.assertLessEqual(fonts.width(faces, "head", "EXTERIOR CLEANERS", s), 972)
        self.assertGreater(s, 100)          # the em-size mistake gave ~82 here


class PlanRules(unittest.TestCase):
    def test_root_of_nested_plan(self):
        root = Path("/x/tools/auto-edit")
        self.assertEqual(plan.root_of(root / "plans" / "examples" / "a.json"), root)
        self.assertEqual(plan.root_of(root / "plans" / "a.json"), root)

    def test_draft_titles(self):
        ws = words("Exterior cleaners, stuck at $20K a month? all live within just four days")
        t = plan.draft_titles("agency", ws, ws[:8])
        styles = [x["style"] for x in t]
        self.assertEqual(styles[0], "combo")
        self.assertIn("stack", styles)


if __name__ == "__main__":
    unittest.main()
