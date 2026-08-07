// Google Calendar's fixed 11-color event palette (colorId -> metadata).
// Reference: https://developers.google.com/calendar/api/v3/reference/colors
const GOOGLE_EVENT_COLORS = {
  '1': { name: 'Lavender', hex: '#7986cb' },
  '2': { name: 'Sage', hex: '#33b679' },
  '3': { name: 'Grape', hex: '#8e24aa' },
  '4': { name: 'Flamingo', hex: '#e67c73' },
  '5': { name: 'Banana', hex: '#f6bf26' },
  '6': { name: 'Tangerine', hex: '#f4511e' },
  '7': { name: 'Peacock', hex: '#039be5' },
  '8': { name: 'Graphite', hex: '#616161' },
  '9': { name: 'Blueberry', hex: '#3f51b5' },
  '10': { name: 'Basil', hex: '#0b8043' },
  '11': { name: 'Tomato', hex: '#d50000' },
};

// Default stage pipeline, left-to-right board order, each tied to one
// Google Calendar event color. Fully editable later from the Settings page
// (overrides are stored in the settings table as JSON and merged over this).
const DEFAULT_STAGES = [
  { key: 'new_lead', label: 'New Lead', colorId: '9' }, // Blueberry
  { key: 'demo_scheduled', label: 'Demo Call Scheduled', colorId: '7' }, // Peacock
  { key: 'thinking_it_over', label: 'Demo Call – Thinking It Over', colorId: '5' }, // Banana
  { key: 'free_trial', label: 'Demo Call – Free Trial', colorId: '4' }, // Flamingo
  { key: 'closed_won', label: 'Closed Won', colorId: '10' }, // Basil
  { key: 'ghosted', label: 'Ghosted / No-Show', colorId: '11' }, // Tomato
  { key: 'closed_lost', label: 'Closed Lost', colorId: '8' }, // Graphite
];

function stagesWithColors(stages) {
  return stages.map((s) => ({ ...s, color: GOOGLE_EVENT_COLORS[s.colorId] || null }));
}

function colorIdToStageKey(stages, colorId) {
  const stage = stages.find((s) => s.colorId === String(colorId));
  return stage ? stage.key : null;
}

function stageKeyToColorId(stages, stageKey) {
  const stage = stages.find((s) => s.key === stageKey);
  return stage ? stage.colorId : null;
}

module.exports = {
  GOOGLE_EVENT_COLORS,
  DEFAULT_STAGES,
  stagesWithColors,
  colorIdToStageKey,
  stageKeyToColorId,
};
