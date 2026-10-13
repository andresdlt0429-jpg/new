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
python3 -m unittest -v                                 # run the 27 self-checks
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

## Step 4: Let it read your texts

This part depends on your phone.

**Android: yes, it can read all incoming texts.**
Install **SMS Gateway for Android** (free, open source, sms-gate.app). In its settings, add a
webhook for the `sms:received` event pointing to your listener (Step 5):
`https://YOUR-ADDRESS/incoming?token=YOUR_WEBHOOK_TOKEN`.
Now every text your phone gets is copied to the assistant.

**iPhone: Apple doesn't let any app read your texts.** Options:
- Give people your **Twilio number** for the things you want watched (like work texts). Point the Twilio
  number's "A message comes in" webhook to the same link above.
- If you have a **Mac** with Messages synced, your texts are stored on it, and Claude Code running on that Mac
  can read them. (That's a separate add-on; ask and it can be built.)

## Step 5: Keep it running 24/7

The listener has to be on all the time with a web address, like a shop that has to be open for customers to walk in.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # put this in .env as WEBHOOK_TOKEN
python3 sms.py serve                                            # starts listening on port 8080
```

Where to run it:
- **A computer that stays on** + a free tunnel like **Cloudflare Tunnel** to give it a web address.
- **A small hosting service** (Railway, Render, Fly.io, about $5/month). Set the `.env` values as their "environment variables".

The listener also sends your digest automatically at the times in `[digest] times`.

## Everyday commands

```bash
python3 sms.py inbox                  # newest 20 texts
python3 sms.py inbox --level urgent   # just the urgent ones
python3 sms.py search "rent"          # search every saved text (by words, name, or number)
python3 sms.py digest                 # send the "what you missed" text now
python3 sms.py receive "+1555..." "hi" # pretend a text came in (sends for real if Twilio is set up)
```

## Safety built in

- The listener only accepts texts that carry your secret `WEBHOOK_TOKEN`, so strangers can't feed it fake texts.
- Your keys (`.env`), your settings, and your saved texts (`data/`) never get uploaded to GitHub.
- No Twilio keys = practice mode = nothing gets sent.
