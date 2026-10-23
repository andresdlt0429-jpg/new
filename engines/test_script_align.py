"""python3 -m unittest engines/test_script_align.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align  # noqa: E402
import script_parser  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def src(text, key="a", t0=0.0, step=0.4):
    words = [{"w": w, "start": round(t0 + i * step, 2), "end": round(t0 + i * step + 0.3, 2), "p": 0.95}
             for i, w in enumerate(text.split())]
    return {"key": key, "dir": "work/x/", "raw": "raw/x", "proxy": "proxy/x", "duration": 99, "recorded": "2026",
            "words": words, "toks": align.tokenize_words(words)}


def script(*says):
    return {"source": "spec/scripts/t.md", "hooks": [],
            "beats": [{"id": f"B{i + 1}", "beat": "X", "say": s, "on_screen": None, "on_screen_kind": None,
                       "protected_pauses": []} for i, s in enumerate(says)]}


class Parser(unittest.TestCase):
    def test_real_scripts(self):
        p = lambda n: script_parser.parse(os.path.join(ROOT, "spec", "scripts", n))  # noqa: E731
        tower, start, office = p("tower-ads-38s-v4.md"), p("starting-out-40s.md"), p("office-ad-v1-DRAFT.md")
        self.assertEqual((len(tower["beats"]), tower["beats"][0]["beat"], tower["beats"][0]["on_screen"]),
                         (8, "HOOK", "$25–50K/MO"))
        self.assertEqual((len(start["beats"]), len(start["hooks"]), len(start["lock_items"])), (8, 5, 3))
        self.assertEqual(start["beats"][1]["protected_pauses"][0]["before"], "this is for you")
        self.assertEqual(office["beats"][2]["on_screen_kind"], "title")
        self.assertTrue(office["draft"])

    def test_no_table(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("# Just notes\n\nNo table here.\n")
        self.assertIn("no script table found (needs a SAY / LINE column)", script_parser.parse(f.name)["problems"])


class Align(unittest.TestCase):
    def test_last_complete_take_wins_and_cues_labelled(self):
        s = src("okay go we built the system you are missing um okay ready set go we built the system you are missing")
        a = align.align(script("We built the system you're missing."), [s])
        ln = a["lines"][0]
        self.assertEqual(len(ln["takes"]), 2)
        self.assertGreater(ln["chosen"]["start"], ln["takes"][0]["start"])
        self.assertIn("cue", [o["kind"] for o in a["off_script"]])

    def test_missing_and_partial(self):
        s = src("so if you want more jobs click the link below")
        a = align.align(script("So if you want more jobs click the link below and see if you qualify.",
                               "Pressure washing is all I do."), [s])
        self.assertTrue(any(f.startswith("PARTIAL") for f in a["lines"][0]["flags"]) or
                        a["lines"][0]["chosen"]["coverage"] >= 0.8)
        self.assertTrue(any(f.startswith("MISSING") for f in a["lines"][1]["flags"]))

    def test_number_mismatch(self):
        s = src("over 255 star reviews")
        a = align.align(script("Over 250 five-star reviews."), [s])
        self.assertTrue(any(f.startswith("NUMBER_MISMATCH") for f in a["lines"][0]["flags"]))

    def test_off_script_speech(self):
        s = src("hey can you move the truck we built the system you are missing")
        a = align.align(script("We built the system you're missing."), [s])
        self.assertEqual([o["kind"] for o in a["off_script"]], ["off_script"])


if __name__ == "__main__":
    unittest.main()
