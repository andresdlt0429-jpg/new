const path = require('path');
const Database = require('better-sqlite3');
const { DEFAULT_STAGES } = require('./stages');

const db = new Database(path.join(__dirname, '..', 'crm.db'));
db.pragma('journal_mode = WAL');

db.exec(`
  CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    notes TEXT,
    stage TEXT NOT NULL,
    google_event_id TEXT UNIQUE,
    calendar_id TEXT,
    event_link TEXT,
    event_start TEXT,
    color_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
  );
`);

function getSetting(key, fallback = null) {
  const row = db.prepare('SELECT value FROM settings WHERE key = ?').get(key);
  if (!row) return fallback;
  try {
    return JSON.parse(row.value);
  } catch {
    return row.value;
  }
}

function setSetting(key, value) {
  const serialized = typeof value === 'string' ? value : JSON.stringify(value);
  db.prepare(
    `INSERT INTO settings (key, value) VALUES (?, ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value`
  ).run(key, serialized);
}

// Seed defaults on first run.
if (getSetting('stages') === null) setSetting('stages', DEFAULT_STAGES);
if (getSetting('calendar_id') === null) setSetting('calendar_id', 'primary');
if (getSetting('sync_interval_minutes') === null) setSetting('sync_interval_minutes', 5);
if (getSetting('discord_webhook_url') === null) setSetting('discord_webhook_url', '');
if (getSetting('google_tokens') === null) setSetting('google_tokens', null);
if (getSetting('last_sync_at') === null) setSetting('last_sync_at', null);

module.exports = { db, getSetting, setSetting };
