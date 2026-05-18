// Dark/light theme toggle + chart re-tinting.

function getTheme() { return localStorage.getItem('theme') || 'dark'; }

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const btn = $('theme-toggle');
  btn.innerHTML = theme === 'dark' ? '&#9788;' : '&#9790;';
  btn.setAttribute('title', theme === 'dark' ? 'Trece la temă luminoasă' : 'Trece la temă întunecată');
  requestAnimationFrame(syncChartsToTheme);
}

function toggleTheme() {
  const next = getTheme() === 'dark' ? 'light' : 'dark';
  localStorage.setItem('theme', next);
  applyTheme(next);
}

function syncChartsToTheme() {
  if (!hypeChart || !wordsChart) return;
  const grid    = cssVar('--chart-grid');
  const textCol = cssVar('--chart-text');
  const accent  = cssVar('--accent');

  [hypeChart, wordsChart].forEach(c => {
    c.options.scales.x.ticks.color = textCol;
    c.options.scales.x.grid.color  = grid;
    c.options.scales.y.ticks.color = textCol;
    c.options.scales.y.grid.color  = grid;
  });

  hypeChart.data.datasets[0].borderColor          = accent;
  hypeChart.data.datasets[0].backgroundColor      = hexWithAlpha(accent, 0.14);
  hypeChart.data.datasets[0].pointBackgroundColor = accent;

  wordsChart.data.datasets[0].backgroundColor      = hexWithAlpha(accent, 0.70);
  wordsChart.data.datasets[0].hoverBackgroundColor = accent;

  hypeChart.update('none');
  wordsChart.update('none');
}
