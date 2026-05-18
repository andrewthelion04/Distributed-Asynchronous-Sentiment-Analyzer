// Chat log rendering, pause/resume, cross-filtering.

const THEME_LABELS    = { HYPE: 'HYPE', SPAM: 'SPAM', TECHNICAL: 'TECH', CHAT: 'CHAT' };
const SENTIMENT_CLASS = { POSITIVE: 'sentiment-pos', NEGATIVE: 'sentiment-neg', NEUTRAL: 'sentiment-neu' };

function matchesFilter(msg) {
  if (!activeFilter) return true;
  return msg.text.toLowerCase().includes(activeFilter);
}

function highlightedText(text, word) {
  if (!word) return esc(text);
  const re = new RegExp(`(${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
  return esc(text).replace(re, '<span class="highlight">$1</span>');
}

function renderMessage(data) {
  const theme = (data.theme || 'CHAT').toUpperCase();
  const sentClass = SENTIMENT_CLASS[data.sentiment] || 'sentiment-neu';
  const log = $('chat-log');
  const div = document.createElement('div');
  div.className = `msg ${sentClass}`;
  div.innerHTML =
    `<span class="badge ${theme.toLowerCase()}">${THEME_LABELS[theme] || theme}</span>` +
    `<span class="user">${esc(data.username)}:</span>` +
    `<span class="text">${highlightedText(data.text, activeFilter)}</span>`;
  log.appendChild(div);
  while (log.children.length > MAX_LOG) log.removeChild(log.firstChild);
  if (!isPaused) log.scrollTop = log.scrollHeight;
}

function rerenderChatLog() {
  const log = $('chat-log');
  log.innerHTML = '';
  for (const m of messageHistory) {
    if (matchesFilter(m)) renderMessage(m);
  }
  log.scrollTop = log.scrollHeight;
  // When the log was paused, history covers the buffer already; clear it
  // to avoid duplicating those messages on "Reia".
  pausedBuffer.length = 0;
  updateBufferedBadge();
}

function ingestMessage(data) {
  // 1. stats always update, regardless of pause/filter state
  updateSentimentCounters(data.sentiment);
  if ((data.theme || '').toUpperCase() === 'SPAM') {
    spamCount++; $('spam-count').textContent = spamCount;
  }
  lastVelocity = data.velocity || 0;
  $('velocity-val').textContent = lastVelocity.toFixed(1);
  if (data.representative) lastRepresentative = data.representative;
  if (data.top_words)      updateTopWords(data.top_words);
  if (data.window_stats)   updateWindowStats(data.window_stats);

  // 2. history (so cross-filter re-render can replay)
  messageHistory.push(data);
  if (messageHistory.length > MAX_HISTORY) messageHistory.shift();

  // 3. rendering
  if (isPaused) {
    pausedBuffer.push(data);
    updateBufferedBadge();
  } else if (matchesFilter(data)) {
    renderMessage(data);
  }
}

function updateBufferedBadge() {
  const badge = $('buffered-badge');
  if (pausedBuffer.length > 0) {
    badge.textContent = pausedBuffer.length + (pausedBuffer.length === 1 ? ' nou' : ' noi');
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}

function setPauseButton() {
  const btn = $('pause-btn');
  if (isPaused) {
    btn.innerHTML = '&#9654; Reia';
    btn.classList.add('paused');
  } else {
    btn.innerHTML = '&#9208; Pauză';
    btn.classList.remove('paused');
  }
}

function togglePause() {
  isPaused = !isPaused;
  setPauseButton();
  if (!isPaused) {
    for (const m of pausedBuffer) {
      if (matchesFilter(m)) renderMessage(m);
    }
    pausedBuffer.length = 0;
    updateBufferedBadge();
    const log = $('chat-log');
    log.scrollTop = log.scrollHeight;
  }
}

function toggleFilter(word) {
  const w = word.toLowerCase();
  if (activeFilter === w) {
    clearFilter();
  } else {
    activeFilter = w;
    $('filter-chip-word').textContent = '"' + word + '"';
    $('filter-chip').classList.remove('hidden');
    rerenderChatLog();
  }
}

function clearFilter(opts = { rerender: true }) {
  activeFilter = null;
  $('filter-chip').classList.add('hidden');
  if (opts.rerender) rerenderChatLog();
}
