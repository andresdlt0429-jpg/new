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
"""

import argparse
import hmac
import os
import sys
import threading
import time as clock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import assistant as a


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


if __name__ == "__main__":
    main()
