#!/usr/bin/env python3
"""Your text assistant, from the terminal.

  python3 sms.py check                       is everything set up?
  python3 sms.py try "+15555550100" "hi"     how would I sort this text? (sends nothing)
  python3 sms.py receive "+1555..." "hi"     pretend a text came in, for real
  python3 sms.py test-text                   text yourself
  python3 sms.py test-call                   call yourself
  python3 sms.py inbox [--level urgent]      newest texts
  python3 sms.py search "rent"               look through all saved texts
  python3 sms.py digest                      send the summary text now
  python3 sms.py serve                       run 24/7 and listen for texts

On a Mac (reads the texts your iPhone syncs to Messages):
  python3 sms.py mac-check                   can I read your texts? shows the last 5
  python3 sms.py mac-history                 copy all old texts into the searchable inbox
  python3 sms.py mac-watch                   watch for new texts 24/7
"""

import argparse
import hmac
import os
import sqlite3
import sys
import threading
import time as clock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import assistant as a
import mac_messages as mac


def show(rows):
    if not rows:
        print("(nothing yet)")
    for when, who, level, action, body in rows:
        print(f"{when}  [{level:>6} -> {action:<6}]  {who}: {body}")


def make_handler(bot, token):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._reply(200, "text assistant is running\n")

        def do_POST(self):
            url = urlparse(self.path)
            given = parse_qs(url.query).get("token", [""])[0]
            if url.path != "/incoming" or not hmac.compare_digest(given, token):
                return self._reply(403, "nope\n")
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            try:
                got = a.parse_incoming(self.headers.get("Content-Type", ""), raw)
            except ValueError:
                return self._reply(400, "bad body\n")
            if got:
                sender, body, source = got
                d = bot.handle(sender, body, source)
                print(f"in from {d.who} -> {d.level}/{d.action} ({'; '.join(d.reasons)})")
            # Twilio wants TwiML back; an empty <Response/> means "don't auto-reply".
            self._reply(200, "<Response/>", "text/xml")

        def _reply(self, code, body, ctype="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    return Handler


def digest_loop(bot, stop):
    """Send the digest at each time listed in [digest] times."""
    sent = set()
    while not stop.is_set():
        now = a.now_local(bot.settings)
        slot = now.strftime("%H:%M")
        key = (now.date(), slot)
        if slot in bot.settings.get("digest", {}).get("times", []) and key not in sent:
            sent.add(key)
            if bot.send_digest():
                print(f"digest sent at {slot}")
        stop.wait(20)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    for name in ("try", "receive"):
        s = sub.add_parser(name)
        s.add_argument("sender")
        s.add_argument("body")
    sub.add_parser("test-text")
    sub.add_parser("test-call")
    s = sub.add_parser("inbox")
    s.add_argument("--level", choices=a.LEVELS)
    s.add_argument("-n", type=int, default=20)
    s = sub.add_parser("search")
    s.add_argument("words")
    sub.add_parser("digest")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    sub.add_parser("mac-check")
    sub.add_parser("mac-history")
    s = sub.add_parser("mac-watch")
    s.add_argument("--every", type=int, default=10, help="seconds between checks")
    args = p.parse_args(argv)

    a.load_env()
    try:
        settings = a.load_settings()
    except (ValueError, KeyError) as e:
        sys.exit(f"Problem in your settings file: {e}")
    phone = a.Phone()

    if args.cmd == "check":
        print(f"settings file : {a.settings_path().name}")
        if a.settings_path().name == "settings.example.toml":
            print("                (copy it to settings.toml and put your real info in)")
        print(f"your number   : {a.normalize_phone(settings['me']['phone'])}")
        print(f"tone          : {settings['tone']['style']}")
        print(f"levels        : " + ", ".join(f"{l}->{settings['levels'][l]}" for l in reversed(a.LEVELS)))
        print(f"contacts      : {len(settings.get('contacts', []))} saved")
        print(f"Twilio        : {'CONNECTED' if phone.live else 'not set up yet (practice mode)'}")
        print(f"webhook token : {'set' if os.environ.get('WEBHOOK_TOKEN') else 'MISSING'}")
        return

    if args.cmd == "try":
        d = a.triage(settings, args.sender, args.body)
        print(f"level : {d.level}\naction: {d.action}\nwhy   : {'; '.join(d.reasons)}")
        if d.action in ("text", "call"):
            kind = "urgent" if d.level == "urgent" else "text"
            print(f"text  : {a.word_it(settings, kind, d.who, args.body)}")
        if d.action == "call":
            print(f"call  : {a.word_it(settings, 'call', d.who, args.body)}")
        return

    if args.cmd.startswith("mac-"):
        return run_mac(args, settings, phone)

    bot = a.Assistant(settings, phone)
    me = bot.my_number

    if args.cmd == "receive":
        d = bot.handle(args.sender, args.body)
        print(f"saved as {d.level} -> {d.action}")
    elif args.cmd == "test-text":
        phone.text(me, a.word_it(settings, "text", "your assistant", "testing 1 2 3, texts work!"))
    elif args.cmd == "test-call":
        name = settings["me"].get("name", "")
        phone.call(me, f"Hey {name}, it's your assistant. If you can hear this, phone calls are working.")
    elif args.cmd == "inbox":
        show(bot.inbox.recent(args.n, args.level))
    elif args.cmd == "search":
        show(bot.inbox.search(args.words))
    elif args.cmd == "digest":
        print(bot.send_digest() or "nothing waiting for the digest")
    elif args.cmd == "serve":
        token = os.environ.get("WEBHOOK_TOKEN", "")
        if len(token) < 16:
            sys.exit("Set WEBHOOK_TOKEN in .env (16+ random characters) so strangers can't post fake texts.")
        stop = threading.Event()
        threading.Thread(target=digest_loop, args=(bot, stop), daemon=True).start()
        server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(bot, token))
        print(f"listening on port {args.port}  (Twilio {'live' if phone.live else 'practice mode'})")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            stop.set()


def run_mac(args, settings, phone):
    try:
        db = mac.open_readonly(mac.CHAT_DB)
    except mac.NoAccess as e:
        sys.exit(str(e))
    names = mac.load_names()
    bot = a.Assistant(settings, phone, names=names)
    watcher = mac.MacWatcher(bot, db, skip=[bot.my_number, phone.from_number])

    if args.cmd == "mac-check":
        total = db.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        print(f"Messages database : readable, {total:,} messages")
        print(f"Contacts          : {len(names):,} phone numbers/emails with names")
        print(f"Twilio            : {'CONNECTED' if phone.live else 'not set up yet (practice mode)'}")
        print("\nLast 5 texts you received (to confirm I'm reading them right):")
        start = max(mac.newest_rowid(db) - 400, 0)
        got = [m for m in mac.fetch(db, start, 400) if not m["from_me"]][-5:]
        for m in got:
            when = m["when"].astimezone(a.ZoneInfo(settings["me"].get("timezone", "UTC")))
            print(f"  {when:%b %d %I:%M%p}  {bot.name_for(m['sender'])}: {a.shorten(m['body'], 70)}")
        if not got:
            print("  (none found)")
    elif args.cmd == "mac-history":
        print("Copying your old texts into the inbox (nothing gets sent)...")
        print(f"done: {watcher.import_history():,} texts saved. Try: python3 sms.py search \"dinner\"")
    elif args.cmd == "mac-watch":
        stop = threading.Event()
        threading.Thread(target=digest_loop, args=(bot, stop), daemon=True).start()
        watcher.check_once()  # the first run marks "now" as the starting line
        print(f"watching Messages every {args.every}s  (Twilio {'live' if phone.live else 'practice mode'})")
        print("leave this window open. Ctrl+C to stop.")
        try:
            while True:
                try:
                    for d in watcher.check_once():
                        print(f"{a.now_local(settings):%I:%M%p} {d.who} -> {d.level}/{d.action}"
                              f" ({'; '.join(d.reasons)})")
                except sqlite3.OperationalError:
                    pass  # Messages was busy writing; try again next round
                except OSError as e:
                    print(f"couldn't reach Twilio ({e}); will keep watching")
                clock.sleep(args.every)
        except KeyboardInterrupt:
            stop.set()


if __name__ == "__main__":
    main()
