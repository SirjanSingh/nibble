"use strict";
// Stable state transport: a native WebSocket to the core with auto-reconnect.
// Crucially, a brief disconnect does NOT blank the UI — `onReconnecting`
// only fires after the socket has been down past a grace period, so the
// last good state stays on screen (no populated/empty flicker).
(function () {
  const GRACE_MS = 7000;
  let socket = null;
  let backoff = 800;
  let graceTimer = null;
  let stateCb = () => {};
  let reconnCb = () => {};
  let info = null;

  async function ensureInfo() {
    if (!info || !info.port) info = await window.nibble.coreInfo();
    return info;
  }

  function scheduleGrace() {
    if (graceTimer) return;
    graceTimer = setTimeout(() => reconnCb(), GRACE_MS);
  }
  function clearGrace() {
    if (graceTimer) { clearTimeout(graceTimer); graceTimer = null; }
  }

  async function connect() {
    const ci = await ensureInfo();
    if (!ci || !ci.port) { setTimeout(connect, 1000); return; }
    let ws;
    try {
      ws = new WebSocket(
        `ws://127.0.0.1:${ci.port}/ws?token=${encodeURIComponent(ci.token)}`
      );
    } catch (_) { setTimeout(connect, 1200); return; }
    socket = ws;

    ws.onopen = () => { backoff = 800; clearGrace(); };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg && msg.type === "state") { clearGrace(); stateCb(msg); }
      } catch (_) {}
    };
    ws.onclose = () => {
      socket = null;
      scheduleGrace();
      info = null; // re-fetch (port may have changed on a core respawn)
      backoff = Math.min(backoff * 1.6, 8000);
      setTimeout(connect, backoff);
    };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  window.addEventListener("core-reconnect-hint", () => {
    info = null;
    if (socket) { try { socket.close(); } catch (_) {} }
  });

  window.NibbleWS = {
    start(onState, onReconnecting) {
      stateCb = onState || stateCb;
      reconnCb = onReconnecting || reconnCb;
      connect();
    },
  };
})();
