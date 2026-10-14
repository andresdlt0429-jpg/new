# Text Assistant

A small helper that **watches your texts, decides what matters, and reaches you**
by text or phone call, in the tone you pick.

Think of it like a **receptionist at a front desk**:

- Texts walk in the door (the **listener**).
- The receptionist checks your rules: "Is this Mom? Does it say *emergency*?" (**sorting**).
- Then they either **call you**, **text you**, **put it on a pile for later** (the digest), or **file it away** (ignore).
- Everything gets filed in a cabinet you can search any time (the **inbox**).

You control the receptionist's rules with one file: `settings.toml`.

---

## Step 1: Try it right now (free, no accounts)

Out of the box it runs in **practice mode**: it prints what it *would* send instead of sending.

```bash
cd sms-assistant
python3 sms.py check                                   # what's set up
python3 sms.py try "+15555550100" "can you call me"    # how would this text be sorted?
python3 sms.py test-text                               # see the test text it would send
python3 -m unittest -v                                 # run the 37 self-checks
```

## Step 2: Make it yours (priorities and tone)

```bash
cp settings.example.toml settings.toml
```

Open `settings.toml` and change it. Every section has notes. The main dials:

| Dial | What it does | Example |
|---|---|---|
| `[me]` | your number, what I call you, your timezone | `name = "boss"` |
| `[tone] style` | how I talk: `chill`, `straight`, `hype`, `professional` | `style = "hype"` |
| `[levels]` | what each priority does: `call`, `text`, `digest`, `ignore` | `high = "call"` |
| `[[contacts]]` | people who always get a set priority | Mom → `urgent` |
| `[keywords]` | words that bump a text up or down | `"emergency"` → urgent |
| `[quiet_hours]` | only urgent gets through while you sleep | `22:00` to `07:00` |
| `[digest] times` | when the "here's what you missed" text goes out | `["08:00", "18:00"]` |

After any change, test it without sending anything:

```bash
python3 sms.py try "+13125550000" "need a quote asap"
#   level : high
#   action: text
#   why   : says "quote"
```

`settings.toml` is private: it's in `.gitignore`, so it never gets uploaded.

## Step 3: Let it text and call you (Twilio)

Twilio is a service that gives programs a phone number. It's how the assistant texts and calls you.

1. Sign up at **twilio.com** (free trial credit).
2. Verify your own cell number when it asks.
3. Get a phone number in the Twilio console (about $1–2/month after the trial).
4. Copy `.env.example` to `.env` and paste in your **Account SID**, **Auth Token**, and the **Twilio number**.
   Never paste these into a chat; they're like a password.
5. Test it:
   ```bash
   python3 sms.py check       # should say Twilio: CONNECTED
   python3 sms.py test-text   # your phone buzzes
   python3 sms.py test-call   # your phone rings and reads a message
   ```

Rough costs: about a penny per text, a couple of cents per minute of calling.
Twilio may ask you to fill out a short form to register your number for texting in the US.

## Step 4: Let it read your texts (iPhone + Mac)

Apple doesn't let apps on the iPhone read your texts. But your iPhone already **copies every text to your Mac**
(both blue iMessages and green SMS), and the Mac saves them in a file. The assistant reads that file.

Think of the Mac as a **mailroom**: every letter that reaches your iPhone also lands there, and the assistant sorts the mail.

**4a. Make sure your texts reach the Mac**
1. iPhone: **Settings > [your name] > iCloud > Messages** (or "Show All" > Messages) > turn on **Sync this iPhone**.
2. iPhone: **Settings > Apps > Messages > Text Message Forwarding** > turn on your Mac. (This brings over the green-bubble texts.)
3. Mac: open **Messages** and check that your recent texts show up there.

**4b. Get Python 3.11+ on the Mac** (one time)
In Terminal, type `python3 --version`. If it says lower than 3.11 (or nothing), install the latest from
**python.org/downloads** (normal installer, click Next a few times).

**4c. Get this code onto the Mac**
```bash
git clone https://github.com/andresdlt0429-jpg/new.git
cd new && git checkout claude/loving-bell-qtgr0d && cd sms-assistant
```
If git asks for a password, download it instead: on GitHub, switch to the `claude/loving-bell-qtgr0d` branch,
click the green **Code** button > **Download ZIP**, unzip it, and open the `sms-assistant` folder in Terminal.

**4d. Give Terminal permission to read Messages**
macOS locks the Messages folder. Unlock it for Terminal:
**System Settings > Privacy & Security > Full Disk Access** > turn on **Terminal** > quit Terminal (Cmd+Q) and reopen it.

**4e. Check it works**
```bash
python3 sms.py mac-check
```
It should show how many messages it can see and **your last 5 texts**. If those look right, it's reading correctly.
Names come from your Mac's Contacts app automatically.

**4f. Load your old texts so you can search them** (optional, sends nothing)
```bash
python3 sms.py mac-history
python3 sms.py search "dinner"
```

## Step 5: Keep it running

```bash
caffeinate -i python3 sms.py mac-watch
```

Leave that Terminal window open. It checks for new texts every 10 seconds, and `caffeinate` stops the Mac from
falling asleep while it runs. It also sends your digest at the times in `[digest] times`.

- **Laptop?** Keep it plugged in and the lid open. If it sleeps, it catches up when it wakes. Texts that came in more
  than 30 minutes earlier go into the digest instead of pinging you, **except urgent ones**, which still call.
- It never reacts to texts **you** sent, or to the assistant's own alerts (texts from your Twilio number).
- To stop: click the window and press **Ctrl+C**.

<details>
<summary>Not using a Mac? (Android, or a Twilio number as your "work line")</summary>

**Android:** install **SMS Gateway for Android** (free, sms-gate.app). Add a webhook for the `sms:received` event
pointing to `https://YOUR-ADDRESS/incoming?token=YOUR_WEBHOOK_TOKEN`.
**Twilio number:** in Twilio, set the number's "A message comes in" webhook to the same link.

Then run the web listener somewhere that's always on, with a web address (a computer plus a free Cloudflare
Tunnel, or a small host like Railway or Render):
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # put this in .env as WEBHOOK_TOKEN
python3 sms.py serve
```
</details>

## Everyday commands

```bash
python3 sms.py inbox                  # newest 20 texts
python3 sms.py inbox --level urgent   # just the urgent ones
python3 sms.py search "rent"          # search every saved text (by words, name, or number)
python3 sms.py digest                 # send the "what you missed" text now
python3 sms.py receive "+1555..." "hi" # pretend a text came in (sends for real if Twilio is set up)
```

## Safety built in

- The Mac reader only ever *reads* Messages. It can't change or delete your texts.
- The web listener only accepts texts that carry your secret `WEBHOOK_TOKEN`, so strangers can't feed it fake texts.
- Your keys (`.env`), your settings, and your saved texts (`data/`) never get uploaded to GitHub.
- No Twilio keys = practice mode = nothing gets sent.
