// Bootstrap entry point — helpers, shared state, event wiring.
// Loaded LAST: other modules (theme/charts/chat/...) define functions that
// this file calls at startup, while the mutable state declared here is read
// by all of them at runtime.

// ── Data attributes from Django template ────────────────────────────────────
const CSRF            = document.body.dataset.csrf || '';
const INITIAL_CHANNEL = document.body.dataset.initialChannel || '';

// ── Shared mutable state ────────────────────────────────────────────────────
const counts = { POSITIVE: 0, NEGATIVE: 0, NEUTRAL: 0 };
let total = 0, spamCount = 0;
let ws = null, currentChannel = '';

const MAX_LOG     = 200;
const MAX_HISTORY = 400;

const messageHistory = [];     // all received messages (for re-filter)
let activeFilter     = null;   // lowercase word currently filtered
let isPaused         = false;
const pausedBuffer   = [];     // messages that arrive while paused

// ── DOM / fetch / CSS helpers ───────────────────────────────────────────────
const $ = id => document.getElementById(id);

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function post(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': CSRF },
    body: new URLSearchParams(body),
  });
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function hexWithAlpha(hex, alpha) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// ── Bootstrap ───────────────────────────────────────────────────────────────
// All scripts above declared their functions; DOM is parsed (this tag is at
// the end of <body>); safe to wire everything up synchronously.

applyTheme(getTheme());
initCharts();
setPauseButton();

$('start-btn').addEventListener('click', async () => {
  const url = $('twitch-url').value.trim();
  if (!url) return;
  const res = await post('/api/start/', { url });
  const data = await res.json();
  if (!res.ok) { setStatus('Eroare: ' + (data.error || '?'), 'err'); return; }
  currentChannel = data.channel;
  resetDashboard();
  loadProfile(currentChannel);
  setBackground(data.banner);
  openWS(currentChannel);
  $('stop-btn').disabled = false;
});

$('stop-btn').addEventListener('click', async () => {
  await post('/api/stop/', {});
  if (ws) { ws.close(); ws = null; }
  $('stop-btn').disabled = true;
  $('profile-bar').classList.add('hidden');
  setBackground(null);
  setStatus('Oprit', '');
});

$('pause-btn').addEventListener('click', togglePause);
$('filter-chip-clear').addEventListener('click', () => clearFilter());
$('theme-toggle').addEventListener('click', toggleTheme);

$('tldr-btn').addEventListener('click',      () => runAction('/api/summary/',           { title: '&#128203; Context TL;DR',           render: renderTldr }));
$('questions-btn').addEventListener('click', () => runAction('/api/extract-questions/', { title: '&#10067; Întrebări către streamer', render: renderQuestions }));
$('vibe-btn').addEventListener('click',      () => runAction('/api/vibe-check/',        { title: '&#9889; Vibe Check',                 render: renderVibe }));

$('modal-close').addEventListener('click', () => $('modal-overlay').classList.remove('open'));
$('modal-overlay').addEventListener('click', e => {
  if (e.target === $('modal-overlay')) $('modal-overlay').classList.remove('open');
});

// Resume a session if Django says an ingestor is already running.
if (INITIAL_CHANNEL) {
  currentChannel = INITIAL_CHANNEL;
  $('twitch-url').value = INITIAL_CHANNEL;
  loadProfile(INITIAL_CHANNEL);
  fetchBannerFromDecapi(INITIAL_CHANNEL).then(setBackground);
  openWS(INITIAL_CHANNEL);
  $('stop-btn').disabled = false;
}
