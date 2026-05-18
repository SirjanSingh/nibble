"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const short = (s) => (s || "").slice(0, 8);
const base = (p) => (p || "").replace(/\\/g, "/").split("/").filter(Boolean).pop() || p || "—";

let snap = { sessions: [], pending: [], events: [], policies: [],
             caps: [], panic: false };

function setConn(ok) {
  const c = $("conn");
  c.textContent = ok ? "live" : "reconnecting…";
  c.className = "conn " + (ok ? "ok" : "off");
}

function render(s) {
  snap = s;
  $("panic").classList.toggle("armed", !!s.panic);
  $("panic").textContent = s.panic
    ? "PANIC ARMED · click to release" : "PANIC · stop all";

  // gates
  const g = $("gates");
  $("pend-count").textContent = (s.pending || []).length;
  g.innerHTML = (s.pending || []).length
    ? s.pending.map((p) => `
      <div class="gate" data-gid="${esc(p.gid)}">
        <div class="top"><span>${esc(short(p.sid))} · ${esc(p.tool)}
          · ${esc(p.event)}</span><span>${p.age}s</span></div>
        <div class="sum">${esc(p.summary)}</div>
        <div class="acts">
          <button class="btn allow" data-a="allow">Allow</button>
          <button class="btn deny" data-a="deny">Deny</button>
          <button class="btn ghost" data-a="note">Deny + note…</button>
        </div>
      </div>`).join("")
    : `<div class="empty">Nothing waiting.</div>`;

  // sessions
  $("sessions").innerHTML = (s.sessions || []).length
    ? s.sessions.map((x) => `
      <div class="s" data-sid="${esc(x.sid)}">
        <span class="sid">${esc(short(x.sid))}</span>
        <span class="cwd" title="${esc(x.cwd)}">${esc(base(x.cwd))}</span>
        <span class="st ${x.status === "active" ? "active" : ""}">${esc(x.status)}</span>
        <select>
          <option value="autopilot"${x.mode !== "supervise" ? " selected" : ""}>autopilot</option>
          <option value="supervise"${x.mode === "supervise" ? " selected" : ""}>supervise</option>
        </select>
      </div>`).join("")
    : `<div class="empty">No sessions seen yet. Install the hook, then run Claude Code.</div>`;

  // policies
  $("policies").innerHTML = (s.policies || []).length
    ? s.policies.map((p) => {
        let m = {}; try { m = JSON.parse(p.match_json || "{}"); } catch (_) {}
        const md = [m.tool && `tool=${m.tool}`, m.command_regex &&
          `cmd~/${m.command_regex}/`, m.path_regex && `path~/${m.path_regex}/`,
          m.url_regex && `url~/${m.url_regex}/`].filter(Boolean).join(" ");
        return `<div class="p" data-id="${p.id}">
          <span>${esc(p.label || "rule")}</span>
          <span class="mt" title="${esc(md)}">${esc(md || "—")}</span>
          <span class="act ${esc(p.action)}">${esc(p.action)}</span>
          <span class="icn tog">${p.enabled ? "on" : "off"}</span>
          <span class="icn del">✕</span></div>`;
      }).join("")
    : `<div class="empty">No guardrails. Add one below (e.g. tool=Bash, cmd~ rm -rf, deny).</div>`;

  // caps
  const ct = (s.caps || []).find((c) => c.scope === "today");
  const c5 = (s.caps || []).find((c) => c.scope === "session5h");
  if (ct && document.activeElement !== $("cap-today"))
    $("cap-today").value = ct.limit_usd || "";
  if (c5 && document.activeElement !== $("cap-5h"))
    $("cap-5h").value = c5.limit_usd || "";

  // events
  $("events").innerHTML = (s.events || []).map((e) => `
    <div class="e"><span class="d ${esc(e.decision)}">${esc(e.decision)}</span>
      <span class="x" title="${esc(e.summary)}">${esc(e.tool || e.event)} ·
        ${esc(e.summary)}</span>
      <span class="muted">${esc((e.reason || e.decided_by || ""))}</span>
    </div>`).join("") || `<div class="empty">No activity yet.</div>`;
}

// ---- actions ----
$("gates").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button"); if (!btn) return;
  const gid = ev.target.closest(".gate").dataset.gid;
  const a = btn.dataset.a;
  if (a === "note") {
    const r = prompt("Reason / instruction for the agent:");
    if (r == null) return;
    await window.nibble.cond.gate(gid, "deny", r);
  } else {
    await window.nibble.cond.gate(gid, a, "");
  }
});

$("sessions").addEventListener("change", async (ev) => {
  const sid = ev.target.closest(".s").dataset.sid;
  await window.nibble.cond.sessionMode(sid, ev.target.value);
});

$("panic").addEventListener("click", () =>
  window.nibble.cond.panic(!snap.panic));

$("policies").addEventListener("click", async (ev) => {
  const row = ev.target.closest(".p"); if (!row) return;
  const id = row.dataset.id;
  if (ev.target.classList.contains("del"))
    await window.nibble.cond.delPolicy(Number(id));
  else if (ev.target.classList.contains("tog")) {
    const p = (snap.policies || []).find((x) => String(x.id) === id);
    await window.nibble.cond.patchPolicy(Number(id), { enabled: !p.enabled });
  }
});

$("p-add").addEventListener("click", async () => {
  const match = {};
  if ($("p-tool").value.trim()) match.tool = $("p-tool").value.trim();
  if ($("p-cmd").value.trim()) match.command_regex = $("p-cmd").value.trim();
  if (!Object.keys(match).length) {
    alert("Add at least a tool or a command regex."); return;
  }
  await window.nibble.cond.addPolicy({
    label: $("p-label").value.trim() || "rule",
    match, action: $("p-action").value,
    reason: $("p-reason").value.trim(),
  });
  ["p-label", "p-tool", "p-cmd", "p-reason"].forEach((k) => ($(k).value = ""));
});

$("cap-save").addEventListener("click", async () => {
  await window.nibble.cond.setCap({
    scope: "today",
    limit_usd: parseFloat($("cap-today").value) || null, limit_tokens: null });
  await window.nibble.cond.setCap({
    scope: "session5h",
    limit_usd: parseFloat($("cap-5h").value) || null, limit_tokens: null });
});

async function refreshHooks() {
  try {
    const h = await window.nibble.cond.hooksStatus();
    $("hook-state").textContent = h.installed
      ? "installed — every Claude Code session is governed"
      : "not installed — sessions are NOT governed yet";
    $("hook-state").style.color = h.installed ? "#5fcf90" : "#f3b13a";
  } catch (_) {}
}
$("hook-install").addEventListener("click", async () => {
  await window.nibble.cond.hooksInstall(); refreshHooks();
});
$("hook-uninstall").addEventListener("click", async () => {
  await window.nibble.cond.hooksUninstall(); refreshHooks();
});

window.NibbleWS.start(
  null,
  () => setConn(false),
  (c) => { setConn(true); render(c); }
);
window.nibble.cond.snapshot().then((s) => { setConn(true); render(s); })
  .catch(() => {});
refreshHooks();
setInterval(refreshHooks, 15000);
