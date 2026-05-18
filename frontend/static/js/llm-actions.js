// Action-bar buttons (TL;DR / Questions / Vibe) — share one modal.

async function runAction(url, opts) {
  const overlay = $('modal-overlay');
  overlay.classList.add('open');
  $('modal-title').innerHTML = opts.title;
  $('modal-text').innerHTML = '<span class="spinner"></span> Se generează...';
  $('modal-meta').textContent = '';
  try {
    const res = await post(url, {});
    const data = await res.json();
    if (res.ok) {
      $('modal-text').innerHTML = opts.render(data);
      const parts = [];
      if (data.note)  parts.push(data.note);
      if (data.count) parts.push(`Bazat pe ${data.count} mesaje recente.`);
      $('modal-meta').textContent = parts.join(' ');
    } else {
      $('modal-text').textContent = data.error || 'Eroare necunoscută.';
    }
  } catch (e) {
    $('modal-text').textContent = 'Eroare de reţea.';
  }
}

function renderTldr(d)      { return esc(d.summary || ''); }
function renderQuestions(d) {
  if (!d.questions || !d.questions.length) {
    return '<div class="empty">Nu s-au găsit întrebări clare în ultimele mesaje.</div>';
  }
  return '<ul class="q-list">' + d.questions.map(q => `<li>${esc(q)}</li>`).join('') + '</ul>';
}
function renderVibe(d) { return `<div class="vibe-badge">${esc(d.vibe || '?')}</div>`; }
