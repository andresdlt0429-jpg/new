"""python3 -m unittest engines/test_policy.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy_scan as ps  # noqa: E402

CASES = [
    ("you'll make $10k a month", "spoken", "income_claim"),
    ("Guaranteed $100K in 90 days", "on_screen", "money_guarantee"),
    ("20 booked jobs guaranteed", "spoken", "jobs_guarantee"),
    ("be your own boss", "spoken", "own_boss_freedom"),
    ("this is passive income", "spoken", "no_work_needed"),
    ("are you broke?", "spoken", "personal_attributes"),
    ("Exterior cleaners still got 20 grand a month?", "spoken", "personal_attributes"),
    ("I made $40K in 60 days", "spoken", "dollar_testimonial"),
    ("run your Facebook ads", "spoken", "platform_or_brand"),
    ("actual jobs on autopilot", "spoken", "autopilot"),
    ("$20K A MONTH?", "on_screen", "money_on_screen"),
    ("WARNING!!!", "on_screen", "sensational_text"),
]
CLEAN = [("we clean roofs and gutters", "spoken"), ("20 MORE JOBS A MONTH", "on_screen"),
         ("LIVE IN 4 DAYS", "on_screen"), ("SEE IF YOU QUALIFY", "on_screen"), ("all live within just four days", "spoken")]


class Policy(unittest.TestCase):
    def test_flags(self):
        for text, where, rule in CASES:
            with self.subTest(text=text):
                self.assertIn(rule, [f["rule"] for f in ps.scan_text(text, where)])

    def test_clean(self):
        for text, where in CLEAN:
            with self.subTest(text=text):
                self.assertEqual(ps.scan_text(text, where), [])

    def test_where_scoping(self):
        # money_on_screen is about what is shown; saying a dollar figure is a different rule
        self.assertNotIn("money_on_screen", [f["rule"] for f in ps.scan_text("$20K a month", "spoken")])

    def test_policy_doc_is_generated(self):
        p = os.path.join(ps.ROOT, "spec", "QA-policy.md")
        self.assertIn("Generated from `spec/policy-rules.json`", open(p).read())


if __name__ == "__main__":
    unittest.main()
