const express = require('express');
const { db, getSetting } = require('./../db');
const { pushStageToCalendarEvent, scheduleCallForLead } = require('../calendarSync');
const { notifyStageChange } = require('../discord');

const router = express.Router();

router.get('/', (req, res) => {
  const leads = db.prepare('SELECT * FROM leads ORDER BY updated_at DESC').all();
  res.json(leads);
});

router.post('/', (req, res) => {
  const { name, email, phone, notes, stage } = req.body || {};
  if (!name || !name.trim()) return res.status(400).json({ error: 'name is required' });

  const stages = getSetting('stages');
  const validStage = stages.some((s) => s.key === stage) ? stage : stages[0].key;
  const now = new Date().toISOString();

  const info = db
    .prepare(
      `INSERT INTO leads (name, email, phone, notes, stage, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`
    )
    .run(name.trim(), email || null, phone || null, notes || '', validStage, now, now);

  const lead = db.prepare('SELECT * FROM leads WHERE id = ?').get(info.lastInsertRowid);
  res.status(201).json(lead);
});

router.patch('/:id', async (req, res) => {
  const lead = db.prepare('SELECT * FROM leads WHERE id = ?').get(req.params.id);
  if (!lead) return res.status(404).json({ error: 'not found' });

  const { name, email, phone, notes, stage } = req.body || {};
  const stages = getSetting('stages');
  const nextStage = stage && stages.some((s) => s.key === stage) ? stage : lead.stage;

  db.prepare(
    `UPDATE leads SET name = ?, email = ?, phone = ?, notes = ?, stage = ?, updated_at = ? WHERE id = ?`
  ).run(
    name ?? lead.name,
    email ?? lead.email,
    phone ?? lead.phone,
    notes ?? lead.notes,
    nextStage,
    new Date().toISOString(),
    lead.id
  );

  if (stage && stage !== lead.stage) {
    try {
      await pushStageToCalendarEvent(lead, stage);
    } catch (err) {
      console.error('[leads] failed to push stage color to calendar:', err.message);
    }
    const fromLabel = (stages.find((s) => s.key === lead.stage) || {}).label || lead.stage;
    const toLabel = (stages.find((s) => s.key === stage) || {}).label || stage;
    notifyStageChange({ ...lead, name: name ?? lead.name }, fromLabel, toLabel);
  }

  res.json(db.prepare('SELECT * FROM leads WHERE id = ?').get(lead.id));
});

router.delete('/:id', (req, res) => {
  db.prepare('DELETE FROM leads WHERE id = ?').run(req.params.id);
  res.json({ ok: true });
});

// Creates a real Google Calendar event for this lead (colored to match its
// current stage) so it shows up on the calendar as well as the board.
router.post('/:id/schedule-call', async (req, res) => {
  const lead = db.prepare('SELECT * FROM leads WHERE id = ?').get(req.params.id);
  if (!lead) return res.status(404).json({ error: 'not found' });

  const { start, end, calendarId } = req.body || {};
  if (!start || !end) return res.status(400).json({ error: 'start and end are required (ISO datetimes)' });

  try {
    const updated = await scheduleCallForLead(lead, { start, end, calendarId });
    res.json(updated);
  } catch (err) {
    res.status(502).json({ error: err.message });
  }
});

module.exports = router;
