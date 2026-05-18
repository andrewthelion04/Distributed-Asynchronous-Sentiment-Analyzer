// WebSocket lifecycle + status indicator.

function setStatus(text, state) {
  $('status-text').textContent = text;
  $('ws-dot').className = 'dot' + (state ? ' ' + state : '');
}

function openWS(channel) {
  if (ws) {
    const old = ws; ws = null;
    old.onmessage = old.onclose = old.onerror = null;
    old.close();
  }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/chat/${channel}/`);

  ws.onopen  = () => setStatus(`Conectat la #${channel} — aştept mesaje...`, 'on');
  ws.onclose = () => { setStatus('WebSocket deconectat', ''); ws = null; };
  ws.onerror = () => setStatus('Eroare WebSocket', 'err');
  ws.onmessage = e => {
    try {
      const data = JSON.parse(e.data);
      if (data.type === 'message') ingestMessage(data);
    } catch (_) {}
  };
}
