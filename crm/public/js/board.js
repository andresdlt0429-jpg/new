let STAGES = [];
let LEADS = [];
let editingId = null;

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

async function loadAll() {
  const [settings, leads] = await Promise.all([api('/api/settings'), api('/api/leads')]);
  STAGES = settings.stages;
  LEADS = leads;
  renderSyncStatus(settings);
  renderStageSelect();
  renderBoard();
}

function renderSyncStatus(settings) {
  const el = document.getElementById('sync-status');
  if (!settings.connected) {
    el.innerHTML = 'Google Calendar not connected — <a class="navlink" href="settings.html">connect it</a>';
    return;
  }
  const when = settings.lastSyncAt ? new Date(settings.lastSyncAt).toLocaleString() : 'never';
  el.textContent = `Last synced: ${when}`;
}

function renderStageSelect() {
  const select = document.getElementById('lead-stage');
  select.innerHTML = STAGES.map((s) => `<option value="${s.key}">${s.label}</option>`).join('');
}

function renderBoard() {
  const board = document.getElementById('board');
  board.innerHTML = '';

  for (const stage of STAGES) {
    const leadsInStage = LEADS.filter((l) => l.stage === stage.key);

    const col = document.createElement('div');
    col.className = 'column';
    col.innerHTML = `
      <div class="column-header">
        <span class="color-dot" style="background:${stage.color ? stage.color.hex : '#888'}"></span>
        <span class="column-title">${stage.label}</span>
        <span class="column-count">${leadsInStage.length}</span>
      </div>
      <div class="column-body" data-stage="${stage.key}"></div>
    `;

    const body = col.querySelector('.column-body');
    if (leadsInStage.length === 0) {
      body.innerHTML = '<div class="empty-hint">No leads</div>';
    } else {
      for (const lead of leadsInStage) body.appendChild(renderCard(lead));
    }

    body.addEventListener('dragover', (e) => {
      e.preventDefault();
      body.classList.add('drag-over');
    });
    body.addEventListener('dragleave', () => body.classList.remove('drag-over'));
    body.addEventListener('drop', async (e) => {
      e.preventDefault();
      body.classList.remove('drag-over');
      const leadId = e.dataTransfer.getData('text/plain');
      const targetStage = body.dataset.stage;
      const lead = LEADS.find((l) => String(l.id) === leadId);
      if (!lead || lead.stage === targetStage) return;
      try {
        await api(`/api/leads/${leadId}`, { method: 'PATCH', body: JSON.stringify({ stage: targetStage }) });
        await loadAll();
      } catch (err) {
        alert('Could not move lead: ' + err.message);
      }
    });

    board.appendChild(col);
  }
}

function renderCard(lead) {
  const card = document.createElement('div');
  card.className = 'card';
  card.draggable = true;
  card.dataset.id = lead.id;

  const when = lead.event_start ? new Date(lead.event_start).toLocaleString() : '';
  card.innerHTML = `
    <h3>${escapeHtml(lead.name)}</h3>
    ${when ? `<p>${when}</p>` : ''}
    <div class="meta">
      <span>${escapeHtml(lead.email || '')}</span>
      ${lead.event_link ? `<a href="${lead.event_link}" target="_blank" rel="noopener">Calendar ↗</a>` : ''}
    </div>
  `;

  card.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData('text/plain', String(lead.id));
  });

  card.addEventListener('click', () => openModal(lead));
  return card;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function openModal(lead) {
  editingId = lead ? lead.id : null;
  document.getElementById('modal-title').textContent = lead ? 'Edit Lead' : 'New Lead';
  document.getElementById('lead-id').value = lead ? lead.id : '';
  document.getElementById('lead-name').value = lead ? lead.name : '';
  document.getElementById('lead-email').value = lead ? lead.email || '' : '';
  document.getElementById('lead-phone').value = lead ? lead.phone || '' : '';
  document.getElementById('lead-notes').value = lead ? lead.notes || '' : '';
  document.getElementById('lead-stage').value = lead ? lead.stage : STAGES[0].key;
  document.getElementById('lead-delete-btn').style.display = lead ? 'inline-block' : 'none';

  const linkEl = document.getElementById('lead-calendar-link');
  linkEl.innerHTML = lead && lead.event_link
    ? `Linked to calendar event: <a href="${lead.event_link}" target="_blank" rel="noopener">open ↗</a> (color-coding here comes from Calendar; editing stage syncs back)`
    : '';

  document.getElementById('lead-overlay').classList.remove('hidden');
}

function closeModal() {
  document.getElementById('lead-overlay').classList.add('hidden');
  editingId = null;
}

document.getElementById('add-lead-btn').addEventListener('click', () => openModal(null));
document.getElementById('lead-cancel-btn').addEventListener('click', closeModal);

document.getElementById('lead-save-btn').addEventListener('click', async () => {
  const payload = {
    name: document.getElementById('lead-name').value.trim(),
    email: document.getElementById('lead-email').value.trim(),
    phone: document.getElementById('lead-phone').value.trim(),
    notes: document.getElementById('lead-notes').value,
    stage: document.getElementById('lead-stage').value,
  };
  if (!payload.name) return alert('Name is required.');

  try {
    if (editingId) {
      await api(`/api/leads/${editingId}`, { method: 'PATCH', body: JSON.stringify(payload) });
    } else {
      await api('/api/leads', { method: 'POST', body: JSON.stringify(payload) });
    }
    closeModal();
    await loadAll();
  } catch (err) {
    alert('Could not save lead: ' + err.message);
  }
});

document.getElementById('lead-delete-btn').addEventListener('click', async () => {
  if (!editingId) return;
  if (!confirm('Delete this lead? This does not delete the calendar event.')) return;
  try {
    await api(`/api/leads/${editingId}`, { method: 'DELETE' });
    closeModal();
    await loadAll();
  } catch (err) {
    alert('Could not delete lead: ' + err.message);
  }
});

document.getElementById('sync-now-btn').addEventListener('click', async () => {
  const btn = document.getElementById('sync-now-btn');
  btn.disabled = true;
  btn.textContent = 'Syncing…';
  try {
    await api('/api/settings/sync', { method: 'POST' });
    await loadAll();
  } catch (err) {
    alert('Sync failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Sync Now';
  }
});

loadAll().catch((err) => {
  document.getElementById('board').innerHTML = `<div class="empty-hint">Failed to load: ${err.message}</div>`;
});
