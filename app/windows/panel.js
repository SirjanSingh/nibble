"use strict";
const $ = (id) => document.getElementById(id);
let haveData = false;

function renderState(s) {
  if (!s) return;
  if (typeof s.spent_today === "number") {
    haveData = true;
    $("spent").textContent = "$" + s.spent_today.toFixed(2);
  }
  if (typeof s.daily_budget === "number")
    $("budget").textContent = "$" + s.daily_budget.toFixed(0);

  const pct = Math.max(0, s.pct_used || 0);
  const ring = $("ring");
  ring.style.setProperty("--p", Math.min(100, pct));
  ring.className = "ring" + (pct >= 100 ? " over" : pct >= 70 ? " warn" : "");
  $("pct").textContent = pct.toFixed(0) + "%";

  if (s.projected_eod != null) {
    const p = $("proj");
    p.textContent = "$" + s.projected_eod.toFixed(2);
    p.className = "proj " + (s.on_track ? "good" : "bad");
  }

  const kvS = $("kv-sess");
  if (s.session_active) {
    const m = s.session_resets_in_min || 0;
    const rs = m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
    $("sess").textContent =
      `$${(s.session_spent || 0).toFixed(2)} · resets ${rs}`;
    kvS.style.display = "flex";
  } else if (s.session_active === false) {
    kvS.style.display = "none";
  }

  // tools with relative bars
  const tools = (s.per_tool || []).filter((t) => t.cost != null);
  const max = Math.max(0.0001, ...tools.map((t) => t.cost));
  const tw = $("tools");
  if (tools.length) {
    tw.innerHTML = tools.map((t) => `
      <div class="row">
        <div class="name">${t.tool.replace(/_/g, " ")}</div>
        <div class="bar"><i style="width:${(t.cost / max) * 100}%"></i></div>
        <div class="val">$${(t.cost || 0).toFixed(2)}</div>
      </div>`).join("");
  } else if (!haveData) {
    tw.innerHTML = `<div class="empty">Loading…</div>`;
  } else {
    tw.innerHTML = `<div class="empty">No AI spend yet today.</div>`;
  }

  // sources
  const src = $("sources");
  const ents = Object.entries(s.sources || {});
  if (ents.length) {
    src.innerHTML = ents.map(([name, st]) => `
      <div class="src ${st.available ? "on" : "off"}">
        <span class="dot"></span>
        <div class="meta"><b>${name.replace(/_/g, " ")}</b>
          <span>${st.detail}</span></div>
      </div>`).join("");
  }
}

function setConn(live) {
  const c = $("conn");
  c.textContent = live ? "live" : "reconnecting…";
  c.className = "conn " + (live ? "ok" : "off");
}

async function loadSummary() {
  try {
    const sum = await window.nibble.getSummary();
    const days = (sum.daily || []).slice().reverse();
    const max = Math.max(0.01, ...days.map((d) => d.cost || 0));
    $("chart").innerHTML = days.map((d, i) => {
      const c = d.cost || 0;
      const cls = i === days.length - 1 ? "col today" : "col";
      return `<div class="${cls}" style="height:${(c / max) * 100}%"
        data-tip="${d.d}: $${c.toFixed(2)}"></div>`;
    }).join("");
    const tot = days.reduce((a, d) => a + (d.cost || 0), 0);
    $("trend").textContent = days.length
      ? `$${tot.toFixed(2)} over ${days.length} days` : "";
  } catch (_) {}
}

async function loadSettings() {
  try {
    const s = await window.nibble.getSettings();
    $("in-budget").value = s.daily_budget;
    $("in-commentary").checked = !!s.commentary_enabled;
    const ph = (h) => (h ? "•••• stored — leave blank to keep" : "not set");
    $("in-openai").placeholder = ph(s.keys.openai);
    $("in-anthropic").placeholder = ph(s.keys.anthropic);
    $("in-comkey").placeholder = ph(s.keys.anthropic_commentary);
  } catch (_) {}
}

$("save").addEventListener("click", async () => {
  const payload = {
    daily_budget: parseFloat($("in-budget").value),
    commentary_enabled: $("in-commentary").checked,
  };
  if ($("in-openai").value) payload.openai_key = $("in-openai").value;
  if ($("in-anthropic").value) payload.anthropic_key = $("in-anthropic").value;
  if ($("in-comkey").value)
    payload.anthropic_commentary_key = $("in-comkey").value;
  try { await window.nibble.saveSettings(payload); } catch (_) {}
  ["in-openai", "in-anthropic", "in-comkey"].forEach((k) => ($(k).value = ""));
  const tag = $("saved");
  tag.classList.remove("hidden");
  setTimeout(() => tag.classList.add("hidden"), 1800);
  loadSettings();
});

window.NibbleWS.start(
  (s) => { setConn(true); renderState(s); },
  () => setConn(false)        // grace-gated; data stays on screen
);
loadSummary();
loadSettings();
setInterval(loadSummary, 45000);
