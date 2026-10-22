"""python3 -m unittest engines/test_plan_schema.py"""
import copy
import json
import os
import unittest

import jsonschema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "plans", "plan.schema.json")) as f:
    SCHEMA = json.load(f)
with open(os.path.join(ROOT, "plans", "example.plan.json")) as f:
    EXAMPLE = json.load(f)
V = jsonschema.Draft202012Validator(SCHEMA)


def errors(plan):
    return [e.message for e in V.iter_errors(plan)]


class PlanSchema(unittest.TestCase):
    def test_schema_is_valid(self):
        jsonschema.Draft202012Validator.check_schema(SCHEMA)

    def test_example_passes(self):
        self.assertEqual(errors(EXAMPLE), [])

    def bad(self, mutate):
        p = copy.deepcopy(EXAMPLE)
        mutate(p)
        self.assertNotEqual(errors(p), [], "should have been rejected")

    def test_rejects_clock_seconds_anchor(self):
        # Layers must anchor to a word or part, never a raw output time.
        self.bad(lambda p: p["layers"]["titles"][0].update({"at": {"t": 3.2}}))

    def test_rejects_hex_color(self):
        self.bad(lambda p: p["parts"][0].update({"background": {"kind": "color", "color": "#1a73e8"}}))

    def test_rejects_typo_key(self):
        self.bad(lambda p: p["layers"]["graphics"][0].update({"untill": {"part": "cta"}}))

    def test_rejects_text_graphic_without_text(self):
        self.bad(lambda p: p["layers"]["graphics"][0].pop("text"))

    def test_rejects_bad_group(self):
        self.bad(lambda p: p["parts"][0].update({"group": "hooks:H1"}))

    def test_rejects_handwritten_lock(self):
        self.bad(lambda p: p.update({"status": "locked", "lock": {"by": "Andy", "at": "2026-10-22T10:00:00Z",
                                                                  "hash": "trust me"}}))

    def test_rejects_caption_fix_without_listener(self):
        self.bad(lambda p: p["layers"]["captions"]["fixes"].append({"word": "a:30", "text": "250"}))

    def test_rejects_unknown_ad_type(self):
        self.bad(lambda p: p.update({"ad_type": "partner"}))


if __name__ == "__main__":
    unittest.main()
