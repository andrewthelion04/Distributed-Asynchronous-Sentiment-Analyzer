// Streamer profile + dynamic banner background.

function setBackground(url) {
  const bg = $('dynamic-background');
  if (url && url.startsWith('http')) {
    bg.style.backgroundImage = `url("${url.replace(/"/g, '%22')}")`;
    bg.classList.add('active');
  } else {
    bg.classList.remove('active');
  }
}

async function fetchBannerFromDecapi(channel) {
  // Used on initial reconnect (when /api/start/ isn't called).
  for (const ep of [
    `https://decapi.me/twitch/offline_image/${channel}`,
    `https://decapi.me/twitch/banner/${channel}`,
  ]) {
    try {
      const r = await fetch(ep);
      if (!r.ok) continue;
      const txt = (await r.text()).trim();
      if (txt.startsWith('http')) return txt;
    } catch (_) {}
  }
  return null;
}

async function loadProfile(channel) {
  $('profile-name').textContent = channel;
  $('profile-avatar').src = '';
  $('profile-bar').classList.remove('hidden');
  try {
    const resp = await fetch(`https://decapi.me/twitch/avatar/${channel}`);
    if (resp.ok) {
      const avatarUrl = (await resp.text()).trim();
      if (avatarUrl.startsWith('http')) $('profile-avatar').src = avatarUrl;
    }
  } catch (_) {}
}
