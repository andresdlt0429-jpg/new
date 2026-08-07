const express = require('express');
const googleAuth = require('../googleAuth');

const router = express.Router();

router.get('/start', (req, res) => {
  res.redirect(googleAuth.getAuthUrl());
});

router.get('/callback', async (req, res) => {
  const { code, error } = req.query;
  if (error) return res.redirect(`/settings.html?error=${encodeURIComponent(error)}`);
  try {
    await googleAuth.exchangeCodeForTokens(code);
    res.redirect('/settings.html?connected=1');
  } catch (err) {
    console.error('[auth] token exchange failed:', err);
    res.redirect(`/settings.html?error=${encodeURIComponent(err.message)}`);
  }
});

router.get('/status', (req, res) => {
  res.json({ connected: googleAuth.isConnected() });
});

router.post('/disconnect', (req, res) => {
  googleAuth.disconnect();
  res.json({ ok: true });
});

module.exports = router;
