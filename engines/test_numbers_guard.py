"""python3 -m unittest engines/test_numbers_guard.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from numbers_guard import check, screen_numbers, spoken_ledger  # noqa: E402


def words(text):
    return [{"w": w, "start": float(i), "end": float(i) + 0.5, "p": 0.95} for i, w in enumerate(text.split())]


def vals(text):
    return [(n["value"], n["unit"]) for n in spoken_ledger(words(text))]


class SpokenForms(unittest.TestCase):
    def test_digits_and_suffixes(self):
        self.assertEqual(vals("we hit $100,000 last year"), [(100000, "money")])
        self.assertEqual(vals("scale to 100K a month"), [(100000, None)])
        self.assertEqual(vals("save 15% today"), [(15, "percent")])
        self.assertEqual(vals("about 2.5 hours"), [(2.5, None)])

    def test_number_words(self):
        self.assertEqual(vals("two hundred and fifty reviews"), [(250, None)])
        self.assertEqual(vals("twenty five percent off"), [(25, "percent")])
        self.assertEqual(vals("a hundred thousand dollars"), [(100000, "money")])
        self.assertEqual(vals("ten thousand bucks"), [(10000, "money")])
        self.assertEqual(vals("fifteen per cent"), [(15, "percent")])
        self.assertEqual(vals("still doing 20 grand a month"), [(20000, "money")])

    def test_hyphens_split(self):
        self.assertEqual(vals("a fifteen-minute call"), [(15, None)])
        self.assertEqual(vals("over 250 five-star reviews"), [(250, None), (5, None)])
        self.assertEqual(vals("we answer 24/7"), [(24, None), (7, None)])
        self.assertEqual(vals("two hundred and fifty five-star reviews"), [(250, None), (5, None)])
        self.assertEqual(vals("twenty-five dollars"), [(25, "money")])

    def test_separate_numbers_not_merged(self):
        self.assertEqual(vals("one two three"), [(1, None), (2, None), (3, None)])
        self.assertEqual(vals("10 20"), [(10, None), (20, None)])
        self.assertEqual(vals("twenty thirty"), [(20, None), (30, None)])

    def test_no_numbers(self):
        self.assertEqual(vals("we clean roofs and gutters the right way"), [])
        self.assertEqual(vals("a great job"), [])

    def test_low_confidence_flag(self):
        w = words("save fifteen percent")
        w[1]["p"] = 0.4
        self.assertTrue(spoken_ledger(w)[0]["confirm_by_ear"])


class ScreenCheck(unittest.TestCase):
    def setUp(self):
        self.ledger = spoken_ledger(words("book this week and save fifteen percent over 250 five-star reviews"))

    def test_screen_parsing(self):
        self.assertEqual([(n["value"], n["unit"]) for n in screen_numbers("LET'S SCALE TO $100K A MONTH")],
                         [(100000, "money")])
        self.assertEqual([(n["value"], n["unit"]) for n in screen_numbers("+10 EXTRA JOBS")], [(10, None)])
        self.assertEqual([(n["value"], n["unit"]) for n in screen_numbers("BOOK A 15-MIN CALL")], [(15, None)])

    def test_spoken_passes(self):
        r = check("Save 15% this week", self.ledger)
        self.assertEqual((len(r["ok"]), r["block"]), (1, []))
        r = check("250+ five-star reviews", self.ledger)
        self.assertEqual((len(r["ok"]), r["block"]), (2, []))

    def test_unspoken_blocks(self):
        self.assertEqual(len(check("Only $99", self.ledger)["block"]), 1)
        self.assertEqual(len(check("+10 extra jobs a month", self.ledger)["block"]), 1)

    def test_unit_mismatch_blocks(self):
        # Spoken as a percent, shown as money: different claim.
        r = check("Save $15", self.ledger)
        self.assertEqual(len(r["block"]), 1)

    def test_unit_missing_in_speech_warns(self):
        ledger = spoken_ledger(words("scale to a hundred thousand a month"))
        r = check("$100K a month", ledger)
        self.assertEqual((r["block"], len(r["warn"])), ([], 1))

    def test_plus_suffix_is_checked(self):
        self.assertEqual(len(check("1,000+ homes", self.ledger)["block"]), 1)
        self.assertEqual(check("250+ reviews", self.ledger)["block"], [])
        self.assertEqual(len(check("250+ reviews", self.ledger)["ok"]), 1)

    def test_unreadable_digits_fail_closed(self):
        self.assertEqual(len(check("Call 555.123.4567", self.ledger)["block"]), 1)
        self.assertEqual(len(check("Top 5ive", self.ledger)["block"]), 1)

    def test_grand_matches_dollar_overlay(self):
        ledger = spoken_ledger(words("exterior cleaners still doing 20 grand a month"))
        r = check("$20K a month?", ledger)
        self.assertEqual((r["block"], r["warn"], len(r["ok"])), ([], [], 1))

    def test_text_without_numbers_passes(self):
        r = check("Clean roof. Happy home.", self.ledger)
        self.assertEqual((r["ok"], r["block"], r["warn"]), ([], [], []))


if __name__ == "__main__":
    unittest.main()
