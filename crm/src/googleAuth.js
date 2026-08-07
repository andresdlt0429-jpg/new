const { google } = require('googleapis');
const { getSetting, setSetting } = require('./db');

const SCOPES = [
  'https://www.googleapis.com/auth/calendar', // read/write so stage changes can push a color update back
];

function redirectUri() {
  const base = process.env.BASE_URL || `http://localhost:${process.env.PORT || 3000}`;
  return `${base}/auth/google/callback`;
}

function newOAuthClient() {
  return new google.auth.OAuth2(
    process.env.GOOGLE_CLIENT_ID,
    process.env.GOOGLE_CLIENT_SECRET,
    redirectUri()
  );
}

function getAuthUrl() {
  const client = newOAuthClient();
  return client.generateAuthUrl({
    access_type: 'offline',
    prompt: 'consent', // ensures a refresh_token is returned even on repeat connects
    scope: SCOPES,
  });
}

async function exchangeCodeForTokens(code) {
  const client = newOAuthClient();
  const { tokens } = await client.getToken(code);
  setSetting('google_tokens', tokens);
  return tokens;
}

// Returns an authenticated OAuth2 client, or null if the user hasn't connected yet.
// Persists refreshed access tokens back to the DB automatically.
function getAuthedClient() {
  const tokens = getSetting('google_tokens');
  if (!tokens) return null;

  const client = newOAuthClient();
  client.setCredentials(tokens);
  client.on('tokens', (newTokens) => {
    const merged = { ...tokens, ...newTokens };
    setSetting('google_tokens', merged);
  });
  return client;
}

function isConnected() {
  return !!getSetting('google_tokens');
}

function disconnect() {
  setSetting('google_tokens', null);
}

module.exports = {
  getAuthUrl,
  exchangeCodeForTokens,
  getAuthedClient,
  isConnected,
  disconnect,
};
