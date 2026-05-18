// Hype Meter (line) + Top Words (bar). Both initialized via initCharts().

const HYPE_WINDOW  = 30;
const hypeLabels   = Array(HYPE_WINDOW).fill('');
const hypeData     = Array(HYPE_WINDOW).fill(0);
const hypeMessages = Array(HYPE_WINDOW).fill(null);

let hypeChart  = null;
let wordsChart = null;
let lastVelocity = 0;
let lastRepresentative = null;

function initCharts() {
  const chartDefaults = {
    animation: false,
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
  };

  hypeChart = new Chart($('hype-chart').getContext('2d'), {
    type: 'line',
    data: {
      labels: hypeLabels,
      datasets: [{
        data: hypeData,
        borderColor: cssVar('--accent'),
        backgroundColor: hexWithAlpha(cssVar('--accent'), 0.14),
        pointBackgroundColor: cssVar('--accent'),
        pointBorderColor: '#fff',
        pointBorderWidth: 1,
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 5,
        borderWidth: 2,
      }],
    },
    options: {
      ...chartDefaults,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: true,
          backgroundColor: 'rgba(8, 8, 10, 0.94)',
          titleColor: '#fff',
          bodyColor: '#dedee3',
          borderColor: '#9146ff',
          borderWidth: 1,
          padding: 10,
          cornerRadius: 6,
          displayColors: false,
          callbacks: {
            title: () => '',
            label: (ctx) => `${ctx.parsed.y.toFixed(1)} mesaje/secundă`,
            afterBody: (items) => {
              const idx = items[0].dataIndex;
              const m = hypeMessages[idx];
              if (!m || !m.text) return '';
              const truncated = m.text.length > 90 ? m.text.slice(0, 90) + '…' : m.text;
              return ['', `${m.username}: ${truncated}`];
            },
          },
        },
      },
      scales: {
        x: { display: false, ticks: { color: cssVar('--chart-text') }, grid: { color: cssVar('--chart-grid') } },
        y: { ticks: { color: cssVar('--chart-text'), maxTicksLimit: 4 }, grid: { color: cssVar('--chart-grid') }, beginAtZero: true },
      },
    },
  });

  wordsChart = new Chart($('words-chart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: [],
      datasets: [{
        data: [],
        backgroundColor: hexWithAlpha(cssVar('--accent'), 0.70),
        hoverBackgroundColor: cssVar('--accent'),
        borderRadius: 3,
      }],
    },
    options: {
      ...chartDefaults,
      indexAxis: 'y',
      onHover: (evt, elements) => {
        const canvas = evt.native && evt.native.target;
        if (canvas) canvas.style.cursor = elements.length ? 'pointer' : 'default';
      },
      onClick: (evt, elements) => {
        if (!elements.length) return;
        const idx = elements[0].index;
        const word = wordsChart.data.labels[idx];
        if (word) toggleFilter(word);
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(8, 8, 10, 0.94)',
          titleColor: '#fff',
          bodyColor: '#dedee3',
          borderColor: '#9146ff',
          borderWidth: 1,
          padding: 8,
          cornerRadius: 6,
          displayColors: false,
          callbacks: {
            title: (items) => items[0].label,
            label: (ctx) => `${ctx.parsed.x} apariții — click pentru filtrare`,
          },
        },
      },
      scales: {
        x: { ticks: { color: cssVar('--chart-text'), maxTicksLimit: 4 }, grid: { color: cssVar('--chart-grid') }, beginAtZero: true },
        y: { ticks: { color: cssVar('--chart-text'), font: { size: 11 } }, grid: { color: cssVar('--chart-grid') } },
      },
    },
  });

  // Advance hype chart every second; snapshot velocity + representative message.
  setInterval(() => {
    hypeData.shift();     hypeData.push(lastVelocity);
    hypeMessages.shift(); hypeMessages.push(lastRepresentative);
    hypeLabels.shift();   hypeLabels.push('');
    hypeChart.update('none');
    lastRepresentative = null;
  }, 1000);
}
