"""python3 -m unittest engines/test_phase8_10.py  (music curve, client cards, matrix naming, intake codes)"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import client_card  # noqa: E402
import export_matrix  # noqa: E402
import intake  # noqa: E402
import music  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MusicCurve(unittest.TestCase):
    def setUp(self):
        self.words = [(2.5, 2.9), (3.0, 3.4), (6.0, 6.5), (12.5, 13.0)]
        self.silent = [(0.0, 2.0), (10.0, 12.0)]
        self.g, self.level, self.gate = music.gain_curve(14.0, 16.5, self.words, self.silent, 2.0)
        self.t = np.arange(self.g.size) / music.CTRL

    def test_silent_in_hook_and_guarantee(self):
        for a, b in self.silent:
            self.assertEqual(float(self.g[(self.t >= a) & (self.t < b)].max()), 0.0)

    def test_ducked_under_words(self):
        mid = int(3.2 * music.CTRL)  # inside a word, after the settle-in
        gap = int(5.2 * music.CTRL)
        self.assertLess(self.level[mid], music.M["gaps_lu"] - 3)
        self.assertGreater(self.level[gap], self.level[mid])

    def test_ends_at_zero(self):
        self.assertEqual(float(self.g[-1]), 0.0)
        self.assertGreater(float(self.g[int(14.5 * music.CTRL)]), 0.0)  # audible on the card before the fade

    def test_effects_are_soft(self):
        for k in ("whoosh", "tick", "pop"):
            self.assertAlmostEqual(float(music.db(np.abs(music.sfx(k)).max())), music.M["sfx"]["peak_dbfs"], 1)


class ClientCards(unittest.TestCase):
    def test_template_blocks(self):
        card, d = client_card.load("_template")
        codes = {c[0] for c in client_card.check(card, d)}
        self.assertTrue({"C02", "C03", "C06", "C10"} <= codes)

    def test_example_holds_not_blocks(self):
        card, d = client_card.load("example-client")
        sev = {c[1] for c in client_card.check(card, d)}
        self.assertNotIn("BLOCK", sev)
        self.assertIn("HOLD", sev)

    def test_low_contrast_primary_uses_dark_text(self):
        card, d = client_card.load("example-client")
        card = copy.deepcopy(card)
        card["brand"]["colors"]["primary"] = "#F2C14E"  # yellow: white text fails
        col, ratio = client_card.text_on_primary(card)
        self.assertEqual(col, card["brand"]["colors"]["text_dark"])
        self.assertGreaterEqual(ratio, 4.5)

    def test_money_offer_is_held(self):
        card, d = client_card.load("example-client")
        card = copy.deepcopy(card)
        card["offer"]["headline"] = "We help you make $20k a month"
        self.assertIn("C09", {c[0] for c in client_card.check(card, d)})

    def test_brand_merge(self):
        base = json.load(open(os.path.join(ROOT, "brand.json")))
        b, _ = client_card.brand_for({"ad_type": "client", "client": "example-client"}, base)
        self.assertEqual(b["colors"]["primary"], "#0B6E4F")
        self.assertTrue(b["logo"]["path"].endswith("clients/example-client/logo.png"))
        self.assertEqual(client_card.brand_for({"ad_type": "own"}, base)[0], base)


class Matrix(unittest.TestCase):
    def setUp(self):
        self.plan = json.load(open(os.path.join(ROOT, "plans", "office-ad", "plan.json")))

    def test_names(self):
        self.assertEqual(export_matrix.camel("office-ad"), "OfficeAd")
        self.assertEqual(export_matrix.hook_no(self.plan, {"id": "A", "body": "A"}), 1)
        p = copy.deepcopy(self.plan)
        p["parts"][0]["group"] = "hook:H3"
        p["parts"].insert(0, dict(p["parts"][0], id="B1_0", group="hook:H1"))
        # the script's number, not the position: H2 never filmed, H3 still exports as H3
        self.assertEqual(export_matrix.hook_no(p, {"id": "x", "hook": "H3"}), 3)

    def test_version_follows_content(self):
        v = self.plan["variants"][0]
        h1 = export_matrix.content_hash(self.plan, v)
        self.assertEqual(h1, export_matrix.content_hash(copy.deepcopy(self.plan), v))
        p = copy.deepcopy(self.plan)
        p["layers"]["graphics"][0]["text"] += " X"
        self.assertNotEqual(h1, export_matrix.content_hash(p, v))


class Intake(unittest.TestCase):
    def test_codes_on_generated_clips(self):
        with tempfile.TemporaryDirectory() as d:
            th = os.path.join(d, "office")  # talking-head folder
            os.makedirs(th)
            small = os.path.join(th, "landscape_silent.mp4")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
                            "-t", "2", "-pix_fmt", "yuv420p", small], check=True)
            dark = os.path.join(th, "dark_vertical.mp4")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x080808:size=1080x1920:rate=30",
                            "-f", "lavfi", "-i", "sine=f=220:sample_rate=48000", "-t", "5", "-pix_fmt", "yuv420p",
                            "-shortest", dark], check=True)
            a = {c["code"] for c in intake.check_clip(small)["codes"]}
            self.assertTrue({"I02", "I03", "I05", "I06", "I12"} <= a, a)
            b = intake.check_clip(dark)
            codes = {c["code"]: c["level"] for c in b["codes"]}
            self.assertEqual(codes.get("I11"), "REJECT")
            self.assertIn("I12", codes)
            self.assertNotIn("I03", codes)


if __name__ == "__main__":
    unittest.main()


class HookMatrixPlan(unittest.TestCase):
    """build_plan on a script with a hooks table: hook x body variants, hooks played first,
    a hook never filmed costs only its own cuts. Built from the office ad's real takes."""

    @classmethod
    def setUpClass(cls):
        import shutil
        import build_plan
        import policy_scan
        import render
        src = os.path.join(ROOT, "plans", "office-ad")
        cls.tmp = tempfile.mkdtemp()
        d = os.path.join(cls.tmp, "hook-test")
        os.makedirs(d)
        shutil.copy(os.path.join(src, "script.json"), d)
        al = json.load(open(os.path.join(src, "alignment.json")))
        cuts = json.load(open(os.path.join(src, "cuts.json")))
        b1 = next(x for x in al["lines"] if x["id"] == "B1")
        h1 = dict(b1, id="H1", kind="hook", beat=None)
        h2 = dict(b1, id="H2", kind="hook", beat=None)
        h3 = dict(b1, id="H3", kind="hook", beat=None, takes=[], chosen=None,
                  flags=["MISSING: no take of this line in any source"])
        al["lines"] = [h1, h2, h3] + [x for x in al["lines"] if x["id"] != "B1"]
        keeps = []
        for k in cuts["keeps"]:
            if k["line"] == "B1":
                keeps += [dict(k, line="H1"), dict(k, line="H2")]
            else:
                keeps.append(k)
        cuts["keeps"] = keeps
        json.dump(al, open(os.path.join(d, "alignment.json"), "w"))
        json.dump(cuts, open(os.path.join(d, "cuts.json"), "w"))
        cls.plan = build_plan.build(d)
        cls.render, cls.policy = render, policy_scan

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_variants_are_hooks_times_bodies(self):
        self.assertEqual([v["id"] for v in self.plan["variants"]], ["H1_A", "H1_C2", "H2_A", "H2_C2"])

    def test_hooks_grouped_and_first(self):
        groups = [p["group"] for p in self.plan["parts"]]
        self.assertTrue(groups[0].startswith("hook:"))
        self.assertFalse(any(p["beat"] == "HOOK" and p["group"] == "shared" for p in self.plan["parts"]))
        srcs = {k: align.load_source(os.path.join(ROOT, v["ingest"])) for k, v in self.plan["sources"].items()}
        for v in self.plan["variants"]:
            segs = self.render.build_timeline(self.plan, v, srcs)
            self.assertEqual(segs[0]["part"].split("_")[0], v["hook"])  # each cut opens on its own hook
            self.assertEqual(sum(s["beat"] == "HOOK" for s in segs), 1)  # and only that one

    def test_unfilmed_hook_warns_not_blocks(self):
        c = self.plan["checks"]
        self.assertEqual(c["no_missing_lines"]["status"], "pass")
        self.assertEqual(c["hooks_recorded"]["status"], "warn")
        self.assertIn("H3", " ".join(c["hooks_recorded"]["notes"]))

    def test_each_variant_has_hero_and_policy_text(self):
        for v in self.plan["variants"]:
            self.assertIn(v["id"], self.plan["layers"]["hero"])
            spoken, shown = self.policy.variant_texts(self.plan, v)
            self.assertTrue(spoken[0][0].startswith(v["hook"]))
        self.assertEqual([export_matrix.hook_no(self.plan, v) for v in self.plan["variants"]], [1, 1, 2, 2])


import align  # noqa: E402
