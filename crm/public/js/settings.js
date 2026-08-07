async function api(path, opts) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Request failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

function banner(msg, kind) {
  document.getElementById('banner').innerHTML = msg ? `<div class="banner ${kind}">${msg}</div>` : '';
}

let SETTINGS = null;

async function load() {
  const params = new URLSearchParams(location.search);
  if (params.get('connected')) banner('Google Calendar connected successfully.', 'ok');
  if (params.get('error')) banner('Google connection failed: ' + params.get('error'), 'err');

  SETTINGS = await api('/api/settings');
  renderGoogleStatus();
  renderStageMapping();
  document.getElementById('discord-url').value = SETTINGS.discordWebhookUrl || '';
  document.getElementById('sync-interval').value = String(SETTINGS.syncIntervalMinutes || 5);

  if (SETTINGS.connected) await loadCalendars();
}

function renderGoogleStatus() {
  const pill = document.getElementById('google-status');
  const connectBtn = document.getElementById('google-connect-btn');
  const disconnectBtn = document.getElementById('google-disconnect-btn');
  const calendarRow = document.getElementById('calendar-row');

  if (SETTINGS.connected) {
    pill.textContent = 'Connected';
    pill.className = 'status-pill on';
    connectBtn.style.display = 'none';
    disconnectBtn.style.display = 'inline-block';
    calendarRow.style.display = 'flex';
  } else {
    pill.textContent = 'Not connected';
    pill.className = 'status-pill off';
    connectBtn.style.display = 'inline-block';
    disconnectBtn.style.display = 'none';
    calendarRow.style.display = 'none';
  }
}

async function loadCalendars() {
  const select = document.getElementById('calendar-select');
  try {
    const calendars = await api('/api/settings/calendars');
    select.innerHTML = calendars
      .map((c) => `<option value="${c.id}" ${c.id === SETTINGS.calendarId ? 'selected' : ''}>${c.summary}${c.primary ? ' (primary)' : ''}</option>`)
      .join('');
  } catch (err) {
    select.innerHTML = `<option value="${SETTINGS.calendarId}">${SETTINGS.calendarId}</option>`;
  }
}

function renderStageMapping() {
  const wrap = document.getElementById('stage-mapping');
  const colorOptions = Object.entries(SETTINGS.colors)
    .map(([id, c]) => `<option value="${id}">${c.name}</option>`)
    .join('');

  wrap.innerHTML = SETTINGS.stages
    .map(
      (s) => `
      <div class="row" data-stage-key="${s.key}">
        <label>${s.label}</label>
        <span class="color-dot" style="background:${s.color ? s.color.hex : '#888'}; margin-right:8px;"></span>
        <select class="stage-color-select">${colorOptions}</select>
      </div>`
    )
    .join('');

  wrap.querySelectorAll('.row').forEach((row) => {
    const key = row.dataset.stageKey;
    const stage = SETTINGS.stages.find((s) => s.key === key);
    row.querySelector('.stage-color-select').value = stage.colorId;
    row.querySelector('.stage-color-select').addEventListener('change', (e) => {
      const colorId = e.target.value;
      const swatch = row.querySelector('.color-dot');
      swatch.style.background = SETTINGS.colors[colorId].hex;
    });
  });
}

document.getElementById('google-connect-btn').addEventListener('click', () => {
  location.href = '/auth/google/start';
});

document.getElementById('google-disconnect-btn').addEventListener('click', async () => {
  if (!confirm('Disconnect Google Calendar? Auto-sync will stop until you reconnect.')) return;
  await api('/auth/google/disconnect', { method: 'POST' });
  location.reload();
});

document.getElementById('reset-stages-btn').addEventListener('click', async () => {
  if (!confirm('Reset all stage colors back to the defaults?')) return;
  await api('/api/settings/reset-stages', { method: 'POST' });
  location.reload();
});

document.getElementById('discord-test-btn').addEventListener('click', async () => {
  const url = document.getElementById('discord-url').value.trim();
  try {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify({ discordWebhookUrl: url }) });
    await api('/api/settings/discord-test', { method: 'POST' });
    banner('Test message sent to Discord.', 'ok');
  } catch (err) {
    banner('Discord test failed: ' + err.message, 'err');
  }
});

document.getElementById('save-btn').addEventListener('click', async () => {
  const stages = Array.from(document.querySelectorAll('#stage-mapping .row')).map((row) => ({
    key: row.dataset.stageKey,
    colorId: row.querySelector('.stage-color-select').value,
  }));
  const calendarSelect = document.getElementById('calendar-select');

  try {
    await api('/api/settings', {
      method: 'PUT',
      body: JSON.stringify({
        stages,
        calendarId: calendarSelect.value || undefined,
        discordWebhookUrl: document.getElementById('discord-url').value.trim(),
        syncIntervalMinutes: Number(document.getElementById('sync-interval').value),
      }),
    });
    banner('Settings saved. Restart the server for a new sync interval to take effect.', 'ok');
  } catch (err) {
    banner('Could not save settings: ' + err.message, 'err');
  }
});

load().catch((err) => banner('Failed to load settings: ' + err.message, 'err'));
