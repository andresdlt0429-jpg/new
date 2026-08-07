const express = require('express');
const { getSetting, setSetting } = require('../db');
const { GOOGLE_EVENT_COLORS, stagesWithColors, DEFAULT_STAGES } = require('../stages');
const { syncCalendar, listCalendars } = require('../calendarSync');
const { isConnected } = require('../googleAuth');
const { sendDiscordMessage } = require('../discord');

const router = express.Router();

router.get('/', (req, res) => {
  res.json({
    connected: isConnected(),
    stages: stagesWithColors(getSetting('stages')),
    colors: GOOGLE_EVENT_COLORS,
    calendarId: getSetting('calendar_id'),
    discordWebhookUrl: getSetting('discord_webhook_url') || '',
    syncIntervalMinutes: getSetting('sync_interval_minutes'),
    lastSyncAt: getSetting('last_sync_at'),
  });
});

// Update which Google color maps to which stage, the calendar to sync,
// the Discord webhook, and/or the auto-sync interval. Any field omitted
// is left unchanged.
router.put('/', (req, res) => {
  const { stages, calendarId, discordWebhookUrl, syncIntervalMinutes } = req.body || {};

  if (stages) {
    const current = getSetting('stages');
    const byKey = Object.fromEntries(current.map((s) => [s.key, s]));
    for (const update of stages) {
      if (byKey[update.key] && GOOGLE_EVENT_COLORS[update.colorId]) {
        byKey[update.key] = { ...byKey[update.key], colorId: update.colorId };
      }
    }
    setSetting('stages', current.map((s) => byKey[s.key]));
  }

  if (typeof calendarId === 'string' && calendarId) setSetting('calendar_id', calendarId);
  if (typeof discordWebhookUrl === 'string') setSetting('discord_webhook_url', discordWebhookUrl.trim());
  if (Number.isFinite(syncIntervalMinutes) && syncIntervalMinutes >= 1) {
    setSetting('sync_interval_minutes', syncIntervalMinutes);
  }

  res.json({ ok: true });
});

router.post('/reset-stages', (req, res) => {
  setSetting('stages', DEFAULT_STAGES);
  res.json({ ok: true });
});

router.get('/calendars', async (req, res) => {
  try {
    res.json(await listCalendars());
  } catch (err) {
    res.status(502).json({ error: err.message });
  }
});

router.post('/discord-test', async (req, res) => {
  try {
    const result = await sendDiscordMessage('👋 Test message from your CRM pipeline board.');
    if (result.skipped) return res.status(400).json({ error: 'No Discord webhook URL is set yet.' });
    res.json({ ok: true });
  } catch (err) {
    res.status(502).json({ error: err.message });
  }
});

router.post('/sync', async (req, res) => {
  try {
    const result = await syncCalendar();
    res.json(result);
  } catch (err) {
    res.status(502).json({ error: err.message });
  }
});

module.exports = router;
