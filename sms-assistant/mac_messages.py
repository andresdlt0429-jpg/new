"""Read your texts from the Messages app on your Mac.

When your iPhone syncs Messages to your Mac, every text (blue and green
bubbles) gets saved in a database file on the Mac:
    ~/Library/Messages/chat.db
This file only ever *reads* that database. It never changes or deletes anything.

macOS protects that folder, so the app you run this from (Terminal) needs
"Full Disk Access". See README.md, Mac setup.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import assistant as a

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
ADDRESS_BOOK = Path.home() / "Library" / "Application Support" / "AddressBook"
STATE = a.HERE / "data" / "mac_state.json"
APPLE_EPOCH = 978307200  # Apple's clock counts from Jan 1, 2001
LATE_AFTER = timedelta(minutes=30)

NO_ACCESS = (
    "Your Mac blocked access to Messages.\n"
    "Fix: System Settings > Privacy & Security > Full Disk Access > turn on Terminal,\n"
    "then quit Terminal completely (Cmd+Q) and open it again."
)


class NoAccess(Exception):
    pass


def open_readonly(path):
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.execute("SELECT 1 FROM message LIMIT 1")
        return db
    except sqlite3.OperationalError as e:
        if not Path(path).exists():
            raise NoAccess(f"Can't find {path}. Is Messages set up on this Mac?") from e
        raise NoAccess(NO_ACCESS) from e


def apple_time(value):
    """Apple stores time as seconds (old Macs) or nanoseconds (new Macs) since 2001."""
    if not value:
        return datetime.now(timezone.utc)
    seconds = value / 1e9 if value > 1e11 else value
    return datetime.fromtimestamp(seconds + APPLE_EPOCH, tz=timezone.utc)


def decode_body(text, blob):
    """Newer Macs leave the 'text' column empty and pack the words into
    'attributedBody'. The words sit right after the class name NSString,
    a '+' marker, and a length."""
    if text:
        return text
    if not blob:
        return None
    for marker in (b"NSString", b"NSMutableString"):
        i = blob.find(marker)
        if i != -1:
            break
    else:
        return None
    plus = blob.find(b"+", i + len(marker), i + len(marker) + 12)
    if plus == -1:
        return None
    pos = plus + 1
    size = blob[pos]
    if size == 0x81:
        size, pos = int.from_bytes(blob[pos + 1:pos + 3], "little"), pos + 3
    elif size == 0x82:
        size, pos = int.from_bytes(blob[pos + 1:pos + 5], "little"), pos + 5
    else:
        pos += 1
    return blob[pos:pos + size].decode("utf-8", errors="replace") or None


QUERY = """
SELECT m.ROWID, m.text, m.attributedBody, m.date, m.is_from_me, m.cache_has_attachments,
       h.id, c.chat_identifier, c.display_name
FROM message m
LEFT JOIN handle h ON h.ROWID = m.handle_id
LEFT JOIN chat_message_join j ON j.message_id = m.ROWID
LEFT JOIN chat c ON c.ROWID = j.chat_id
WHERE m.ROWID > ? AND m.associated_message_type = 0 AND m.item_type = 0
GROUP BY m.ROWID
ORDER BY m.ROWID
LIMIT ?
"""


def fetch(db, after=0, limit=500):
    """Texts newer than row `after`, oldest first. Skips tapbacks and group-rename events."""
    out = []
    for rowid, text, blob, date, from_me, has_file, handle, chat_id, chat_name in db.execute(
        QUERY, (after, limit)
    ):
        body = decode_body(text, blob)
        if body:
            body = body.replace("￼", "").strip()  # placeholder char where a photo sits
        if not body:
            if not has_file:
                continue
            body = "[photo or file]"
        if chat_id and chat_id.startswith("chat"):  # group chats are named chat123...
            body = f"[{chat_name or 'group chat'}] {body}"
        out.append({
            "rowid": rowid,
            "sender": handle or chat_id or "",
            "body": body,
            "when": apple_time(date),
            "from_me": bool(from_me),
        })
    return out


def newest_rowid(db):
    return db.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0]


def load_names(root=ADDRESS_BOOK):
    """Names from the Contacts app, so you see 'Jess' instead of '+13125550000'."""
    names = {}
    for f in sorted(Path(root).glob("**/AddressBook-v22.abcddb")) if Path(root).exists() else []:
        try:
            db = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            for table, column in (("ZABCDPHONENUMBER", "ZFULLNUMBER"), ("ZABCDEMAILADDRESS", "ZADDRESS")):
                for first, last, org, handle in db.execute(
                    f"SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, x.{column} "
                    f"FROM ZABCDRECORD r JOIN {table} x ON x.ZOWNER = r.Z_PK"
                ):
                    name = " ".join(p for p in (first, last) if p) or org
                    if name and handle:
                        names.setdefault(a.person_key(handle), name)
            db.close()
        except sqlite3.Error:
            continue  # an unreadable address book just means numbers instead of names
    return names


class MacWatcher:
    def __init__(self, bot, db, state_path=STATE, skip=()):
        self.bot = bot
        self.db = db
        self.state_path = Path(state_path)
        # Never react to texts from yourself or from the assistant's own Twilio number,
        # or it would keep alerting you about its own alerts.
        self.skip = {a.person_key(s) for s in skip if s}

    @property
    def last(self):
        try:
            return json.loads(self.state_path.read_text())["last_rowid"]
        except (OSError, ValueError, KeyError):
            return None

    @last.setter
    def last(self, rowid):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"last_rowid": rowid}))

    def check_once(self):
        """Look for new texts since last time. Returns the decisions made."""
        if self.last is None:  # first run: start from now, don't replay years of texts
            self.last = newest_rowid(self.db)
            return []
        tz = ZoneInfo(self.bot.settings["me"].get("timezone", "UTC"))
        now = datetime.now(tz)
        done = []
        while True:
            batch = fetch(self.db, self.last)
            if not batch:
                break
            for m in batch:
                if not m["from_me"] and a.person_key(m["sender"]) not in self.skip:
                    d = self.bot.handle(
                        m["sender"], m["body"], source="imessage", now=now,
                        received=m["when"].astimezone(tz), ext_id=f"imsg:{m['rowid']}",
                        late=now - m["when"] > LATE_AFTER,
                    )
                    if d:
                        done.append(d)
                self.last = m["rowid"]
        return done

    def import_history(self):
        """Copy every old text into your searchable inbox. Never pings you."""
        tz = ZoneInfo(self.bot.settings["me"].get("timezone", "UTC"))
        after, saved = 0, 0
        if self.last is None:
            self.last = newest_rowid(self.db)  # the watcher takes over from here
        stop = self.last
        while True:
            batch = [m for m in fetch(self.db, after, 2000) if m["rowid"] <= stop]
            if not batch:
                break
            for m in batch:
                saved += self.bot.archive(
                    m["sender"], m["body"], m["when"].astimezone(tz),
                    f"imsg:{m['rowid']}", from_me=m["from_me"], source="imessage-history",
                )
            after = batch[-1]["rowid"]
        return saved
