"""Tests for the Mac reader, using a fake Messages database built with the same
tables and columns as the real ~/Library/Messages/chat.db.

Run with:  python3 -m unittest -v   (from inside sms-assistant/)
"""

import copy
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import assistant as a
import mac_messages as mac
from test_assistant import SETTINGS, FakePhone

MOM, STRANGER, TWILIO = "+15555550100", "+13125550000", "+15550001111"


def attributed(text):
    """Pack words the way newer Macs store them in message.attributedBody."""
    raw = text.encode()
    size = bytes([len(raw)]) if len(raw) < 0x80 else b"\x81" + len(raw).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00"
            b"\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
            + size + raw + b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00")


def apple_ns(dt):
    return int((dt.timestamp() - mac.APPLE_EPOCH) * 1e9)


class FakeMessages:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT);
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
                attributedBody BLOB, handle_id INTEGER, date INTEGER, is_from_me INTEGER,
                service TEXT, associated_message_type INTEGER DEFAULT 0,
                item_type INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        """)
        self.handles, self.chats = {}, {}

    def handle(self, who):
        if who not in self.handles:
            self.handles[who] = self.db.execute("INSERT INTO handle (id) VALUES (?)", (who,)).lastrowid
        return self.handles[who]

    def chat(self, ident, name=""):
        if ident not in self.chats:
            self.chats[ident] = self.db.execute(
                "INSERT INTO chat (chat_identifier, display_name) VALUES (?,?)", (ident, name)).lastrowid
        return self.chats[ident]

    def add(self, who, body=None, *, blob=False, from_me=False, ago=timedelta(0),
            tapback=False, file=False, group=None):
        when = datetime.now(timezone.utc) - ago
        rowid = self.db.execute(
            "INSERT INTO message (text, attributedBody, handle_id, date, is_from_me,"
            " associated_message_type, cache_has_attachments) VALUES (?,?,?,?,?,?,?)",
            (None if blob else body, attributed(body) if blob and body else None,
             self.handle(who), apple_ns(when), int(from_me), 2000 if tapback else 0, int(file)),
        ).lastrowid
        chat = self.chat(*group) if group else self.chat(who)
        self.db.execute("INSERT INTO chat_message_join VALUES (?,?)", (chat, rowid))
        return rowid


class Reading(unittest.TestCase):
    def test_plain_and_packed_text(self):
        f = FakeMessages()
        f.add(MOM, "plain text column")
        f.add(MOM, "packed by a newer Mac", blob=True)
        f.add(MOM, "long " * 60, blob=True)          # over 127 bytes uses a 3-byte length
        f.add(MOM, "emoji ok 🎉 café", blob=True)    # length counts bytes, not letters
        bodies = [m["body"] for m in mac.fetch(f.db)]
        self.assertEqual(bodies, ["plain text column", "packed by a newer Mac",
                                  ("long " * 60).strip(), "emoji ok 🎉 café"])

    def test_skips_tapbacks_and_labels_groups_and_files(self):
        f = FakeMessages()
        f.add(MOM, 'Loved "hi"', tapback=True)
        f.add(MOM, None, file=True)
        f.add(MOM, None)  # empty, no file: skipped
        f.add(STRANGER, "who's bringing chips", group=("chat123456", "Fantasy League"))
        f.add(STRANGER, "unnamed group", group=("chat999", ""))
        bodies = [m["body"] for m in mac.fetch(f.db)]
        self.assertEqual(bodies, ["[photo or file]", "[Fantasy League] who's bringing chips",
                                  "[group chat] unnamed group"])

    def test_apple_time_both_formats(self):
        when = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
        secs = when.timestamp() - mac.APPLE_EPOCH
        self.assertEqual(mac.apple_time(int(secs)), when)        # older Macs: seconds
        self.assertEqual(mac.apple_time(int(secs * 1e9)), when)  # newer Macs: nanoseconds

    def test_missing_database_explains_itself(self):
        with self.assertRaisesRegex(mac.NoAccess, "Can't find"):
            mac.open_readonly("/nope/chat.db")

    def test_contacts_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "Sources" / "ABC"
            src.mkdir(parents=True)
            db = sqlite3.connect(src / "AddressBook-v22.abcddb")
            db.executescript("""
                CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION);
                CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT);
                CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT);
                INSERT INTO ZABCDRECORD VALUES (1, 'Jess', 'Rivera', NULL), (2, NULL, NULL, 'Ace Plumbing');
                INSERT INTO ZABCDPHONENUMBER VALUES (1, '(312) 555-0000'), (2, '+1 800 555 0000');
                INSERT INTO ZABCDEMAILADDRESS VALUES (1, 'Jess@Example.com');
            """)
            db.commit()
            db.close()
            names = mac.load_names(tmp)
        self.assertEqual(names["+13125550000"], "Jess Rivera")
        self.assertEqual(names["+18005550000"], "Ace Plumbing")
        self.assertEqual(names["jess@example.com"], "Jess Rivera")


class Watching(unittest.TestCase):
    def setUp(self):
        s = copy.deepcopy(SETTINGS)
        s["quiet_hours"]["enabled"] = False  # same result no matter when the test runs
        self.f = FakeMessages()
        self.tmp = tempfile.TemporaryDirectory()
        self.bot = a.Assistant(s, FakePhone(), a.Inbox(":memory:"), names={STRANGER: "Jess"})
        self.w = mac.MacWatcher(self.bot, self.f.db, Path(self.tmp.name) / "state.json",
                                skip=["+15555550123", TWILIO])

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_starts_from_now(self):
        self.f.add(MOM, "old text from last year", ago=timedelta(days=300))
        self.assertEqual(self.w.check_once(), [])
        self.assertEqual(self.bot.phone.sent, [])

    def test_new_texts_get_sorted_and_named(self):
        self.w.check_once()
        self.f.add(MOM, "where are you", blob=True)
        self.f.add(STRANGER, "can you send the invoice")
        done = self.w.check_once()
        self.assertEqual([(d.who, d.action) for d in done], [("Mom", "call"), ("Jess", "text")])
        self.assertEqual([k for k, _, _ in self.bot.phone.sent], ["call", "text", "text"])
        self.assertEqual(self.w.check_once(), [])  # nothing new, nothing repeated

    def test_ignores_own_and_assistant_texts(self):
        self.w.check_once()
        self.f.add(MOM, "I sent this", from_me=True)
        self.f.add(TWILIO, "yo boss, Mom texted: ...")  # the assistant's own alert, synced back
        self.assertEqual(self.w.check_once(), [])
        self.assertEqual(self.bot.phone.sent, [])

    def test_late_texts_wait_for_digest_unless_urgent(self):
        self.w.check_once()
        self.f.add(STRANGER, "need a quote", ago=timedelta(hours=3))
        self.f.add(MOM, "call me", ago=timedelta(hours=3))
        done = self.w.check_once()
        self.assertEqual([d.action for d in done], ["digest", "call"])

    def test_history_import_is_silent_searchable_and_no_dupes(self):
        self.f.add(STRANGER, "dinner at 7?", ago=timedelta(days=30))
        self.f.add(STRANGER, "yes see you then", from_me=True, ago=timedelta(days=30))
        self.f.add(MOM, "happy birthday!!", blob=True, ago=timedelta(days=10))
        self.assertEqual(self.w.import_history(), 3)
        self.assertEqual(self.bot.phone.sent, [])
        self.assertIsNone(self.bot.send_digest())  # old texts never land in the digest
        hit = self.bot.inbox.search("dinner")[0]
        self.assertEqual((hit[1], hit[3]), ("Jess", "history"))
        self.assertEqual(self.bot.inbox.search("see you")[0][1], "You -> Jess")
        self.assertEqual(self.w.import_history(), 0)  # running it again adds nothing
        self.f.add(MOM, "brand new")
        self.assertEqual([d.who for d in self.w.check_once()], ["Mom"])  # watcher picks up after


if __name__ == "__main__":
    unittest.main()
