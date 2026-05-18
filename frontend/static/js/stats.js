// Sentiment counters, top-words chart sync, window stats, dashboard reset.

function updateSentimentCounters(sentiment) {
  counts[sentiment]++;
  total++;
  ['pos', 'neg', 'neu'].forEach((p, i) => {
    const key = ['POSITIVE', 'NEGATIVE', 'NEUTRAL'][i];
    const pct = total ? Math.round(counts[key] / total * 100) : 0;
    $(p + '-pct').textContent = pct + '%';
    $(p + '-fill').style.width = pct + '%';
  });
  $('total-count').textContent = total;
}

function updateTopWords(topWords) {
  if (!topWords || !topWords.length) return;
  wordsChart.data.labels = topWords.map(w => w[0]);
  wordsChart.data.datasets[0].data = topWords.map(w => w[1]);
  wordsChart.update('none');
}

function updateWindowStats(stats) {
  if (!stats) return;

  const uniq = Number(stats.unique_ratio) || 0;
  $('unique-ratio-val').textContent = uniq.toFixed(1) + '%';
  const uFill = $('unique-fill');
  uFill.style.width = Math.min(uniq, 100) + '%';
  uFill.classList.remove('warn', 'bad');
  if (uniq < 30)      uFill.classList.add('bad');
  else if (uniq < 60) uFill.classList.add('warn');

  const caps = Number(stats.caps_ratio) || 0;
  $('caps-ratio-val').textContent = caps.toFixed(1) + '%';
  const cFill = $('caps-fill');
  cFill.style.width = Math.min(caps, 100) + '%';
  cFill.classList.remove('warn', 'bad');
  if (caps > 50)      cFill.classList.add('bad');
  else if (caps > 30) cFill.classList.add('warn');

  const list = $('mvp-list');
  list.innerHTML = '';
  if (!stats.mvps || !stats.mvps.length) {
    list.innerHTML = '<li class="empty">—</li>';
    return;
  }
  stats.mvps.forEach(([user, count], i) => {
    const li = document.createElement('li');
    li.innerHTML =
      `<span class="mvp-rank">#${i + 1}</span>` +
      `<span class="mvp-name">${esc(user)}</span>` +
      `<span class="mvp-count">${count} msg</span>`;
    list.appendChild(li);
  });
}

function resetWindowStats() {
  ['unique-fill', 'caps-fill'].forEach(id => {
    $(id).style.width = '0%';
    $(id).classList.remove('warn', 'bad');
  });
  $('unique-ratio-val').textContent = '—';
  $('caps-ratio-val').textContent   = '—';
  $('mvp-list').innerHTML = '<li class="empty">Aşteptând mesaje...</li>';
}

function resetDashboard() {
  counts.POSITIVE = counts.NEGATIVE = counts.NEUTRAL = 0;
  total = spamCount = 0;
  lastVelocity = 0;
  lastRepresentative = null;
  messageHistory.length = 0;
  pausedBuffer.length = 0;
  clearFilter({ rerender: false });

  ['pos', 'neg', 'neu'].forEach(p => {
    $(p + '-pct').textContent = '0%';
    $(p + '-fill').style.width = '0%';
  });
  $('total-count').textContent  = '0';
  $('velocity-val').textContent = '0';
  $('spam-count').textContent   = '0';
  $('chat-log').innerHTML       = '';

  hypeData.fill(0); hypeMessages.fill(null); hypeChart.update('none');
  wordsChart.data.labels = [];
  wordsChart.data.datasets[0].data = [];
  wordsChart.update('none');

  resetWindowStats();
  updateBufferedBadge();
}
