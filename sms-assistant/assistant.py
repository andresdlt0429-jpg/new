"""Text assistant core: sort incoming texts, word them in your tone, and
reach you by text or phone call through Twilio.

Only uses the Python standard library, so there is nothing to install.
"""

import base64
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

try:
    import tomllib
except ImportError:  # Python older than 3.11 (common on Macs)
    raise SystemExit("This needs Python 3.11 or newer. Get it free at python.org/downloads, "
                     "then run commands with python3 again.")

HERE = Path(__file__).resolve().parent
LEVELS = ["low", "normal", "high", "urgent"]
ACTIONS = {"call", "text", "digest", "ignore"}


# ---------------------------------------------------------------- settings

def load_env(path=HERE / ".env"):
    """Read KEY=value lines from .env into os.environ (real env vars win)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def settings_path():
    mine = HERE / "settings.toml"
    return mine if mine.exists() else HERE / "settings.example.toml"


def load_settings(path=None):
    with open(path or settings_path(), "rb") as f:
        s = tomllib.load(f)
    check_settings(s)
    return s


def check_settings(s):
    """Catch typos early with a plain-English message."""
    for level in LEVELS:
        action = s["levels"].get(level)
        if action not in ACTIONS:
            raise ValueError(f'[levels] {level} = "{action}" -- must be one of {sorted(ACTIONS)}')
    if s["levels"].get("default", "normal") not in LEVELS:
        raise ValueError(f"[levels] default must be one of {LEVELS}")
    for c in s.get("contacts", []):
        if c.get("level") not in LEVELS:
            raise ValueError(f'contact {c.get("name")!r}: level must be one of {LEVELS}')
    if s["tone"]["style"] not in TONES:
        raise ValueError(f'[tone] style must be one of {sorted(TONES)}')


def normalize_phone(number):
    """'(555) 555-0100' and '+1 555 555 0100' both become '+15555550100'."""
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    return "+" + digits if digits else ""


def person_key(handle):
    """One spelling per person: emails lowercased, phone numbers normalized."""
    handle = str(handle or "").strip()
    return handle.lower() if "@" in handle else normalize_phone(handle)


# ---------------------------------------------------------------- sorting

@dataclass
class Decision:
    level: str
    action: str
    who: str
    reasons: list = field(default_factory=list)


def find_contact(settings, sender):
    key = person_key(sender)
    for c in settings.get("contacts", []):
        if key and person_key(c["phone"]) == key:
            return c
    return None


def in_quiet_hours(settings, now):
    q = settings.get("quiet_hours", {})
    if not q.get("enabled"):
        return False
    start = time.fromisoformat(q["start"])
    end = time.fromisoformat(q["end"])
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # window crosses midnight, e.g. 22:00-07:00


def now_local(settings):
    return datetime.now(ZoneInfo(settings["me"].get("timezone", "UTC")))


def triage(settings, sender, body, now=None):
    """Decide how important a text is and what to do about it."""
    now = now or now_local(settings)
    text = body.lower()
    words = settings.get("keywords", {})
    contact = find_contact(settings, sender)
    who = contact["name"] if contact else sender
    reasons = []

    level = settings["levels"].get("default", "normal")
    if contact:
        level = contact["level"]
        reasons.append(f"{contact['name']} is set to {level}")

    for bump in ("urgent", "high"):
        hit = next((w for w in words.get(bump, []) if w.lower() in text), None)
        if hit and LEVELS.index(bump) > LEVELS.index(level):
            level = bump
            reasons.append(f'says "{hit}"')

    # Promo/spam words only push strangers down; a saved contact keeps their level.
    low_hit = next((w for w in words.get("low", []) if w.lower() in text), None)
    if low_hit and not contact and level == settings["levels"].get("default", "normal"):
        level = "low"
        reasons.append(f'looks like junk ("{low_hit}")')

    if not reasons:
        reasons.append("no contact or keyword match, using default")

    action = settings["levels"][level]
    if action in ("call", "text") and level != "urgent" and in_quiet_hours(settings, now):
        action = "digest"
        reasons.append("quiet hours, holding for the digest")

    return Decision(level, action, who, reasons)


# ---------------------------------------------------------------- tone

TONES = {
    "chill": {
        "text": "yo {name}, {who} texted: \"{body}\"",
        "urgent": "{name}!! {who} needs you: \"{body}\"",
        "digest": "hey {name}, {count} text{s} you missed:",
        "call": "Hey {name}, it's your assistant. Urgent text from {who}. It says: {body}. "
                "Again, that was from {who}.",
        "emoji": "📱 ",
        "urgent_emoji": "🚨 ",
    },
    "straight": {
        "text": "{who}: {body}",
        "urgent": "URGENT - {who}: {body}",
        "digest": "{count} text{s}:",
        "call": "Urgent text from {who}: {body}. Repeat, from {who}.",
        "emoji": "",
        "urgent_emoji": "",
    },
    "hype": {
        "text": "{NAME}! {who} just hit you up: \"{body}\"",
        "urgent": "{NAME}!!! DROP EVERYTHING. {who}: \"{body}\"",
        "digest": "{NAME}! {count} text{s} rolled in while you were busy:",
        "call": "{name}! Your assistant here, and this one's urgent! {who} says: {body}. "
                "One more time, that's from {who}!",
        "emoji": "🔥 ",
        "urgent_emoji": "🚨🔥 ",
    },
    "professional": {
        "text": "New message from {who}: \"{body}\"",
        "urgent": "Urgent message from {who}: \"{body}\"",
        "digest": "Summary: {count} message{s} received.",
        "call": "Hello {name}. This is your assistant with an urgent message from {who}. "
                "The message reads: {body}. Once again, from {who}.",
        "emoji": "",
        "urgent_emoji": "",
    },
}


def shorten(text, limit):
    return text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"


def word_it(settings, kind, who="", body="", count=0):
    """Write a message in the tone from settings. kind: text, urgent, digest, call."""
    tone = settings["tone"]
    t = TONES[tone["style"]]
    name = settings["me"].get("name", "")
    msg = t[kind].format(name=name, NAME=name.upper(), who=who, body=body, count=count,
                         s="" if count == 1 else "s")
    if kind == "call":
        return msg
    if tone.get("emojis"):
        msg = t["urgent_emoji" if kind == "urgent" else "emoji"] + msg
    return shorten(msg, int(tone.get("max_length", 300)))


def build_digest(settings, messages):
    """One summary text out of a list of (who, body) pairs."""
    if not messages:
        return None
    head = word_it(settings, "digest", count=len(messages))
    lines = [f"• {who}: {shorten(body, 60)}" for who, body in messages]
    return shorten("\n".join([head, *lines]), int(settings["tone"].get("max_length", 300)) * 2)


# ---------------------------------------------------------------- Twilio

class Phone:
    """Sends texts and places calls through Twilio.

    With no Twilio keys set it runs in practice mode: it prints what it
    would have sent instead of sending, so you can try everything for free.
    """

    API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/{what}.json"

    def __init__(self, sid=None, token=None, from_number=None, opener=None):
        self.sid = sid or os.environ.get("TWILIO_ACCOUNT_SID", "")
        self.token = token or os.environ.get("TWILIO_AUTH_TOKEN", "")
        self.from_number = from_number or os.environ.get("TWILIO_FROM_NUMBER", "")
        self.opener = opener or urllib.request.urlopen

    @property
    def live(self):
        return bool(self.sid and self.token and self.from_number)

    def text(self, to, body):
        return self._send("Messages", {"To": to, "From": self.from_number, "Body": body})

    def call(self, to, script):
        twiml = f'<Response><Say voice="Polly.Matthew">{escape(script)}</Say></Response>'
        return self._send("Calls", {"To": to, "From": self.from_number, "Twiml": twiml})

    def _send(self, what, fields):
        if not self.live:
            print(f"[practice mode, nothing sent] {what[:-1].lower()} to {fields['To']}:")
            print("   " + (fields.get("Body") or fields.get("Twiml")).replace("\n", "\n   "))
            return {"practice": True}
        req = urllib.request.Request(
            self.API.format(sid=self.sid, what=what),
            data=urllib.parse.urlencode(fields).encode(),
            method="POST",
        )
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
        with self.opener(req, timeout=20) as resp:
            return json.loads(resp.read())


# ---------------------------------------------------------------- inbox

class Inbox:
    """Every text that comes in gets saved here so you can search it later."""

    def __init__(self, path=HERE / "data" / "inbox.db"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                received_at TEXT, sender TEXT, who TEXT, body TEXT,
                level TEXT, action TEXT, reasons TEXT,
                pending_digest INTEGER DEFAULT 0, source TEXT, ext_id TEXT UNIQUE)"""
        )

    def add(self, sender, body, decision, source, when, ext_id=None):
        """Save a text. Returns False if this exact text (same ext_id) was already saved."""
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO messages (received_at, sender, who, body, level, action,"
                " reasons, pending_digest, source, ext_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (when.isoformat(timespec="seconds"), sender, decision.who, body, decision.level,
                 decision.action, "; ".join(decision.reasons),
                 int(decision.action == "digest"), source, ext_id),
            )
        return cur.rowcount == 1

    def recent(self, limit=20, level=None):
        sql = "SELECT received_at, who, level, action, body FROM messages"
        args = []
        if level:
            sql += " WHERE level = ?"
            args.append(level)
        return self.db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()

    def search(self, words, limit=50):
        like = f"%{words}%"
        return self.db.execute(
            "SELECT received_at, who, level, action, body FROM messages"
            " WHERE body LIKE ? OR who LIKE ? OR sender LIKE ? ORDER BY id DESC LIMIT ?",
            (like, like, like, limit),
        ).fetchall()

    def take_digest(self):
        """Hand back everything waiting for the digest and mark it as sent."""
        with self.db:
            rows = self.db.execute(
                "SELECT id, who, body FROM messages WHERE pending_digest = 1 ORDER BY id"
            ).fetchall()
            self.db.executemany(
                "UPDATE messages SET pending_digest = 0 WHERE id = ?", [(r[0],) for r in rows]
            )
        return [(who, body) for _, who, body in rows]


# ---------------------------------------------------------------- the assistant

class Assistant:
    def __init__(self, settings, phone=None, inbox=None, names=None):
        self.settings = settings
        self.phone = phone or Phone()
        self.inbox = inbox or Inbox()
        self.names = names or {}  # person_key -> name, e.g. from your Mac's Contacts

    @property
    def my_number(self):
        return normalize_phone(self.settings["me"]["phone"])

    def name_for(self, sender):
        contact = find_contact(self.settings, sender)
        return contact["name"] if contact else self.names.get(person_key(sender), sender)

    def handle(self, sender, body, source="manual", now=None, received=None, ext_id=None,
               late=False):
        """A text just came in. Save it, sort it, and ping you if it deserves it.

        late=True means it arrived a while ago (say the Mac was asleep), so only
        urgent texts still get a call or text; the rest go to the digest.
        """
        now = now or now_local(self.settings)
        decision = triage(self.settings, sender, body, now)
        decision.who = self.name_for(sender)
        if late and decision.level != "urgent" and decision.action in ("call", "text"):
            decision.action = "digest"
            decision.reasons.append("came in a while ago, holding for the digest")
        if not self.inbox.add(sender, body, decision, source, received or now, ext_id):
            return None  # already handled this exact text before
        if decision.action == "call":
            self.phone.call(self.my_number, word_it(self.settings, "call", decision.who, body))
            # A call can be missed, so leave the words in a text too.
            self.phone.text(self.my_number, word_it(self.settings, "urgent", decision.who, body))
        elif decision.action == "text":
            kind = "urgent" if decision.level == "urgent" else "text"
            self.phone.text(self.my_number, word_it(self.settings, kind, decision.who, body))
        return decision

    def archive(self, sender, body, received, ext_id, from_me=False, source="history"):
        """Save an old text so it's searchable. Never pings you."""
        if from_me:
            decision = Decision("normal", "sent", f"You -> {self.name_for(sender)}", ["you sent this"])
        else:
            decision = triage(self.settings, sender, body, received)
            decision = Decision(decision.level, "history", self.name_for(sender), decision.reasons)
        return self.inbox.add(sender, body, decision, source, received, ext_id)

    def send_digest(self):
        msg = build_digest(self.settings, self.inbox.take_digest())
        if msg:
            self.phone.text(self.my_number, msg)
        return msg


# ---------------------------------------------------------------- incoming formats

def parse_incoming(content_type, raw):
    """Turn a webhook body into (sender, text, source).

    Understands two senders:
      * SMS Gateway for Android (JSON) - forwards texts from your own phone
      * Twilio (form data) - texts sent to your Twilio number
    Returns None for anything else (delivery receipts, pings, etc).
    """
    if "json" in (content_type or ""):
        data = json.loads(raw or b"{}")
        if data.get("event", "sms:received") != "sms:received":
            return None
        p = data.get("payload", data)
        sender, text = p.get("phoneNumber") or p.get("from"), p.get("message") or p.get("text")
        return (sender, text, "android") if sender and text is not None else None
    form = urllib.parse.parse_qs(raw.decode() if isinstance(raw, bytes) else raw)
    if "From" in form and "Body" in form:
        return form["From"][0], form["Body"][0], "twilio"
    return None
