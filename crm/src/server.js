require('dotenv').config();
const path = require('path');
const express = require('express');

const { getSetting } = require('./db');
const { isConnected } = require('./googleAuth');
const { syncCalendar } = require('./calendarSync');

const authRoutes = require('./routes/auth');
const leadsRoutes = require('./routes/leads');
const settingsRoutes = require('./routes/settings');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, '..', 'public')));

app.use('/auth/google', authRoutes);
app.use('/api/leads', leadsRoutes);
app.use('/api/settings', settingsRoutes);

app.use((err, req, res, next) => { // eslint-disable-line no-unused-vars
  console.error(err);
  res.status(500).json({ error: 'Internal server error' });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`CRM pipeline board running at http://localhost:${PORT}`);
  scheduleAutoSync();
});

let syncTimer = null;

function scheduleAutoSync() {
  if (syncTimer) clearInterval(syncTimer);
  const minutes = Number(getSetting('sync_interval_minutes')) || 5;

  syncTimer = setInterval(async () => {
    if (!isConnected()) return;
    try {
      const result = await syncCalendar();
      console.log(`[sync] scanned ${result.scanned}, created ${result.created}, updated ${result.updated}`);
    } catch (err) {
      console.error('[sync] auto-sync failed:', err.message);
    }
  }, minutes * 60 * 1000);

  // Run once shortly after boot if already connected.
  if (isConnected()) {
    setTimeout(() => {
      syncCalendar()
        .then((r) => console.log(`[sync] startup sync: scanned ${r.scanned}, created ${r.created}, updated ${r.updated}`))
        .catch((err) => console.error('[sync] startup sync failed:', err.message));
    }, 2000);
  }
}
