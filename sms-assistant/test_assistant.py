"""Run with:  python3 -m unittest -v   (from inside sms-assistant/)"""

import copy
import io
import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from zoneinfo import ZoneInfo

import assistant as a
import sms

SETTINGS = a.load_settings(a.HERE / "settings.example.toml")
TZ = ZoneInfo("America/New_York")
NOON = datetime(2026, 10, 13, 12, 0, tzinfo=TZ)
MIDNIGHT = datetime(2026, 10, 13, 0, 30, tzinfo=TZ)
MOM, CLIENT, SPAM, STRANGER = "+15555550100", "(555) 555-0101", "+15555550199", "+13125550000"


class FakePhone:
    live = True

    def __init__(self):
        self.sent = []

    def text(self, to, body):
        self.sent.append(("text", to, body))

    def call(self, to, script):
        self.sent.append(("call", to, script))


def bot():
    return a.Assistant(SETTINGS, FakePhone(), a.Inbox(":memory:"))


class Sorting(unittest.TestCase):
    def t(self, sender, body, now=NOON):
        return a.triage(SETTINGS, sender, body, now)

    def test_vip_contact_is_urgent_and_calls(self):
        d = self.t(MOM, "hey")
        self.assertEqual((d.level, d.action, d.who), ("urgent", "call", "Mom"))

    def test_phone_formats_still_match_contact(self):
        self.assertEqual(self.t(CLIENT, "hello").who, "Best client")
        self.assertEqual(self.t("5555550100", "hi").who, "Mom")

    def test_urgent_keyword_from_stranger(self):
        d = self.t(STRANGER, "There was an ACCIDENT on site")
        self.assertEqual((d.level, d.action), ("urgent", "call"))

    def test_high_keyword_texts(self):
        d = self.t(STRANGER, "can you send a quote for my driveway?")
        self.assertEqual((d.level, d.action), ("high", "text"))

    def test_plain_stranger_goes_to_digest(self):
        d = self.t(STRANGER, "hey what's up")
        self.assertEqual((d.level, d.action), ("normal", "digest"))

    def test_promo_is_ignored(self):
        d = self.t(STRANGER, "40% off everything! Reply STOP to unsubscribe")
        self.assertEqual((d.level, d.action), ("low", "ignore"))

    def test_low_contact_is_ignored(self):
        self.assertEqual(self.t(SPAM, "new arrivals").action, "ignore")

    def test_low_contact_with_emergency_still_escalates(self):
        self.assertEqual(self.t(SPAM, "emergency, store flooded").level, "urgent")

    def test_promo_words_dont_bury_a_saved_contact(self):
        self.assertEqual(self.t(CLIENT, "sale ends friday, want a quote?").level, "high")

    def test_quiet_hours_hold_high_but_not_urgent(self):
        self.assertEqual(self.t(STRANGER, "call me", MIDNIGHT).action, "digest")
        self.assertEqual(self.t(MOM, "hi", MIDNIGHT).action, "call")

    def test_quiet_hours_edges(self):
        at = lambda h, m: datetime(2026, 10, 13, h, m, tzinfo=TZ)
        self.assertTrue(a.in_quiet_hours(SETTINGS, at(22, 0)))
        self.assertTrue(a.in_quiet_hours(SETTINGS, at(6, 59)))
        self.assertFalse(a.in_quiet_hours(SETTINGS, at(7, 0)))
        self.assertFalse(a.in_quiet_hours(SETTINGS, at(21, 59)))

    def test_changing_settings_changes_behavior(self):
        s = copy.deepcopy(SETTINGS)
        s["levels"]["high"] = "call"
        s["quiet_hours"]["enabled"] = False
        self.assertEqual(a.triage(s, STRANGER, "call me", MIDNIGHT).action, "call")

    def test_bad_settings_give_clear_error(self):
        s = copy.deepcopy(SETTINGS)
        s["levels"]["urgent"] = "yell"
        with self.assertRaisesRegex(ValueError, "urgent"):
            a.check_settings(s)


class Tone(unittest.TestCase):
    def styled(self, style, emojis=False):
        s = copy.deepcopy(SETTINGS)
        s["tone"].update(style=style, emojis=emojis)
        return s

    def test_each_style_sounds_different(self):
        outs = {st: a.word_it(self.styled(st), "text", "Mom", "call me back") for st in a.TONES}
        self.assertEqual(len(set(outs.values())), len(a.TONES))
        self.assertEqual(outs["straight"], "Mom: call me back")
        self.assertTrue(outs["chill"].startswith("yo boss"))
        self.assertIn("BOSS!", outs["hype"])

    def test_emojis_toggle(self):
        self.assertTrue(a.word_it(self.styled("chill", True), "urgent", "Mom", "x").startswith("🚨"))
        self.assertFalse(a.word_it(self.styled("chill", False), "urgent", "Mom", "x").startswith("🚨"))

    def test_max_length(self):
        s = self.styled("straight")
        s["tone"]["max_length"] = 40
        out = a.word_it(s, "text", "Mom", "blah " * 100)
        self.assertLessEqual(len(out), 40)
        self.assertTrue(out.endswith("…"))


class Actions(unittest.TestCase):
    def test_urgent_calls_and_leaves_a_text(self):
        b = bot()
        b.handle(MOM, "pick up the phone", now=NOON)
        kinds = [k for k, _, _ in b.phone.sent]
        self.assertEqual(kinds, ["call", "text"])
        self.assertTrue(all(to == "+15555550123" for _, to, _ in b.phone.sent))

    def test_digest_collects_then_empties(self):
        b = bot()
        b.handle(STRANGER, "hey there", now=NOON)
        b.handle("+13125550001", "lunch tomorrow?", now=NOON)
        b.handle(STRANGER, "50% off promo", now=NOON)  # ignored, not in digest
        self.assertEqual(b.phone.sent, [])
        msg = b.send_digest()
        self.assertIn("2 texts", msg)
        self.assertIn("lunch tomorrow?", msg)
        self.assertNotIn("promo", msg)
        self.assertIsNone(b.send_digest())

    def test_everything_is_searchable(self):
        b = bot()
        b.handle(STRANGER, "rent is due friday", now=NOON)
        b.handle(SPAM, "big promo", now=NOON)
        self.assertEqual(len(b.inbox.search("rent")), 1)
        self.assertEqual(len(b.inbox.search("Spammy")), 1)  # search by contact name too
        self.assertEqual(len(b.inbox.recent()), 2)
        self.assertEqual(len(b.inbox.recent(level="low")), 1)


class Twilio(unittest.TestCase):
    def capture(self):
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *e):
                pass

        def opener(req, timeout):
            seen.update(url=req.full_url, auth=req.get_header("Authorization"),
                        form=urllib.parse.parse_qs(req.data.decode()))
            return Resp(b'{"sid": "SM123"}')

        return seen, a.Phone("AC1", "secret", "+15550001111", opener)

    def test_text_request(self):
        seen, phone = self.capture()
        self.assertEqual(phone.text("+15555550123", "hi")["sid"], "SM123")
        self.assertTrue(seen["url"].endswith("/Accounts/AC1/Messages.json"))
        self.assertTrue(seen["auth"].startswith("Basic "))
        self.assertEqual(seen["form"]["Body"], ["hi"])
        self.assertEqual(seen["form"]["From"], ["+15550001111"])

    def test_call_request_escapes_text(self):
        seen, phone = self.capture()
        phone.call("+15555550123", "Tom & Jerry <3")
        self.assertTrue(seen["url"].endswith("/Calls.json"))
        self.assertIn("Tom &amp; Jerry &lt;3", seen["form"]["Twiml"][0])

    def test_practice_mode_sends_nothing(self):
        phone = a.Phone("", "", "", opener=lambda *x: self.fail("should not call Twilio"))
        self.assertFalse(phone.live)
        self.assertEqual(phone.text("+1", "x"), {"practice": True})


class Listener(unittest.TestCase):
    """Start the real web listener and post texts to it like a phone would."""

    TOKEN = "test-token-1234567890"

    def setUp(self):
        # The listener uses the real clock, so turn quiet hours off to get the same result any time.
        s = copy.deepcopy(SETTINGS)
        s["quiet_hours"]["enabled"] = False
        self.bot = a.Assistant(s, FakePhone(), a.Inbox(":memory:"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), sms.make_handler(self.bot, self.TOKEN))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, body, ctype, token=TOKEN):
        req = urllib.request.Request(f"{self.base}/incoming?token={token}", data=body,
                                     headers={"Content-Type": ctype}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_android_gateway_text(self):
        body = json.dumps({"event": "sms:received",
                           "payload": {"phoneNumber": MOM, "message": "where are you"}}).encode()
        self.assertEqual(self.post(body, "application/json"), 200)
        self.assertEqual(self.bot.inbox.search("where")[0][1], "Mom")
        self.assertEqual(self.bot.phone.sent[0][0], "call")

    def test_twilio_text(self):
        body = urllib.parse.urlencode({"From": STRANGER, "Body": "send the invoice"}).encode()
        self.assertEqual(self.post(body, "application/x-www-form-urlencoded"), 200)
        self.assertEqual(self.bot.phone.sent[0][0], "text")

    def test_wrong_token_rejected(self):
        body = urllib.parse.urlencode({"From": STRANGER, "Body": "fake"}).encode()
        self.assertEqual(self.post(body, "application/x-www-form-urlencoded", "guess"), 403)
        self.assertEqual(self.bot.inbox.recent(), [])

    def test_non_message_events_are_skipped(self):
        body = json.dumps({"event": "sms:delivered", "payload": {}}).encode()
        self.assertEqual(self.post(body, "application/json"), 200)
        self.assertEqual(self.bot.inbox.recent(), [])

    def test_garbage_json_is_400(self):
        self.assertEqual(self.post(b"{not json", "application/json"), 400)


if __name__ == "__main__":
    unittest.main()
