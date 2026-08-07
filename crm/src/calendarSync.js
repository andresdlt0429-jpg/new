const { google } = require('googleapis');
const { db, getSetting, setSetting } = require('./db');
const { getAuthedClient } = require('./googleAuth');
const { colorIdToStageKey, stageKeyToColorId } = require('./stages');
const { notifyNewLead, notifyStageChange } = require('./discord');

function calendarClient() {
  const auth = getAuthedClient();
  if (!auth) return null;
  return google.calendar({ version: 'v3', auth });
}

function extractEmail(event) {
  if (Array.isArray(event.attendees) && event.attendees.length) {
    const notSelf = event.attendees.find((a) => !a.self) || event.attendees[0];
    return notSelf.email || null;
  }
  return null;
}

const upsertStmt = db.prepare(`
  INSERT INTO leads (name, email, phone, notes, stage, google_event_id, calendar_id, event_link, event_start, color_id, created_at, updated_at)
  VALUES (@name, @email, NULL, @notes, @stage, @google_event_id, @calendar_id, @event_link, @event_start, @color_id, @now, @now)
  ON CONFLICT(google_event_id) DO UPDATE SET
    name = excluded.name,
    stage = excluded.stage,
    event_link = excluded.event_link,
    event_start = excluded.event_start,
    color_id = excluded.color_id,
    notes = CASE WHEN leads.notes IS NULL OR leads.notes = '' THEN excluded.notes ELSE leads.notes END,
    updated_at = excluded.updated_at
`);

const getByEventId = db.prepare('SELECT * FROM leads WHERE google_event_id = ?');

// Pulls events from the configured calendar, and for every event whose
// colorId matches a configured stage color, creates or updates a lead.
// Events with no colorId (calendar default color) or an unmapped color are
// ignored — the whole point is that YOU classify a lead by coloring the event.
async function syncCalendar() {
  const calendar = calendarClient();
  if (!calendar) throw new Error('Google Calendar is not connected yet.');

  const stages = getSetting('stages');
  const calendarId = getSetting('calendar_id') || 'primary';

  const timeMin = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
  const timeMax = new Date(Date.now() + 120 * 24 * 60 * 60 * 1000).toISOString();

  let events = [];
  let pageToken;
  do {
    const res = await calendar.events.list({
      calendarId,
      timeMin,
      timeMax,
      singleEvents: true,
      orderBy: 'startTime',
      maxResults: 250,
      pageToken,
    });
    events = events.concat(res.data.items || []);
    pageToken = res.data.nextPageToken;
  } while (pageToken);

  let created = 0;
  let updated = 0;

  for (const event of events) {
    if (event.status === 'cancelled') continue;
    const stageKey = colorIdToStageKey(stages, event.colorId);
    if (!stageKey) continue; // not lead-colored, skip

    const existing = getByEventId.get(event.id);
    const stageLabel = (stages.find((s) => s.key === stageKey) || {}).label || stageKey;

    const row = {
      name: event.summary || '(untitled event)',
      email: extractEmail(event),
      notes: event.description || '',
      stage: stageKey,
      google_event_id: event.id,
      calendar_id: calendarId,
      event_link: event.htmlLink || null,
      event_start: (event.start && (event.start.dateTime || event.start.date)) || null,
      color_id: event.colorId || null,
      now: new Date().toISOString(),
    };

    upsertStmt.run(row);

    if (!existing) {
      created += 1;
      const lead = getByEventId.get(event.id);
      notifyNewLead(lead, stageLabel);
    } else if (existing.stage !== stageKey) {
      updated += 1;
      const fromLabel = (stages.find((s) => s.key === existing.stage) || {}).label || existing.stage;
      notifyStageChange(row, fromLabel, stageLabel);
    }
  }

  setSetting('last_sync_at', new Date().toISOString());
  return { scanned: events.length, created, updated };
}

// Two-way sync: when a lead's stage is changed inside the CRM (e.g. dragged
// on the board), push the matching event color back to Google Calendar so
// the calendar and the board never disagree about a lead's stage.
async function pushStageToCalendarEvent(lead, newStageKey) {
  if (!lead.google_event_id) return;
  const calendar = calendarClient();
  if (!calendar) return;

  const stages = getSetting('stages');
  const colorId = stageKeyToColorId(stages, newStageKey);
  if (!colorId) return;

  await calendar.events.patch({
    calendarId: lead.calendar_id || getSetting('calendar_id') || 'primary',
    eventId: lead.google_event_id,
    requestBody: { colorId },
  });
}

async function listCalendars() {
  const calendar = calendarClient();
  if (!calendar) throw new Error('Google Calendar is not connected yet.');
  const res = await calendar.calendarList.list();
  return (res.data.items || []).map((c) => ({ id: c.id, summary: c.summary, primary: !!c.primary }));
}

// Creates a new "call" event on the calendar for a lead, colored to match
// its current stage, and links it back to the lead record.
async function scheduleCallForLead(lead, { start, end, calendarId } = {}) {
  const calendar = calendarClient();
  if (!calendar) throw new Error('Google Calendar is not connected yet.');

  const stages = getSetting('stages');
  const colorId = stageKeyToColorId(stages, lead.stage);
  const targetCalendar = calendarId || getSetting('calendar_id') || 'primary';

  const res = await calendar.events.insert({
    calendarId: targetCalendar,
    requestBody: {
      summary: lead.name,
      description: lead.notes || '',
      colorId: colorId || undefined,
      start: { dateTime: start },
      end: { dateTime: end },
      attendees: lead.email ? [{ email: lead.email }] : undefined,
    },
  });

  db.prepare(
    `UPDATE leads SET google_event_id = ?, calendar_id = ?, event_link = ?, event_start = ?, color_id = ?, updated_at = ? WHERE id = ?`
  ).run(res.data.id, targetCalendar, res.data.htmlLink, start, colorId || null, new Date().toISOString(), lead.id);

  return getByEventId.get(res.data.id);
}

module.exports = { syncCalendar, pushStageToCalendarEvent, listCalendars, scheduleCallForLead };
