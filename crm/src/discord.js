const { getSetting } = require('./db');

async function sendDiscordMessage(content) {
  const webhookUrl = getSetting('discord_webhook_url');
  if (!webhookUrl) return { skipped: true };

  const res = await fetch(webhookUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Discord webhook failed (${res.status}): ${text}`);
  }
  return { ok: true };
}

function notifyNewLead(lead, stageLabel) {
  return sendDiscordMessage(
    `🆕 **New lead:** ${lead.name}\n` +
      `Stage: **${stageLabel}**${lead.event_start ? `\nCall time: ${lead.event_start}` : ''}` +
      (lead.event_link ? `\n${lead.event_link}` : '')
  ).catch((err) => console.error('[discord] notifyNewLead failed:', err.message));
}

function notifyStageChange(lead, fromLabel, toLabel) {
  return sendDiscordMessage(
    `🔁 **${lead.name}** moved: ${fromLabel} → **${toLabel}**`
  ).catch((err) => console.error('[discord] notifyStageChange failed:', err.message));
}

module.exports = { sendDiscordMessage, notifyNewLead, notifyStageChange };
