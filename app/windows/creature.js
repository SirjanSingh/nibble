"use strict";
const stage = document.getElementById("stage");
const badge = document.getElementById("badge");
const bubble = document.getElementById("bubble");
const bubbleText = document.getElementById("bubble-text");
const creature = document.getElementById("creature");
const hint = document.getElementById("hint");
const mini = document.getElementById("mini");
const mList = document.getElementById("m-list");

const STATES = ["idle", "sleeping", "happy", "alert", "shocked", "reconnecting"];
let bubbleTimer = null;
let lastSpeech = "";
let interacted = false;
let last = null;          // last state message
let openTool = null;      // which tool row is expanded

function setState(name) {
  stage.dataset.state = STATES.includes(name) ? name : "idle";
}

function showBubble(text, sticky) {
  if (!text || !mini.classList.contains("hidden")) return; // not while panel open
  bubbleText.textContent = text;
  bubble.classList.remove("hidden");
  if (bubbleTimer) clearTimeout(bubbleTimer);
  bubbleTimer = setTimeout(
    () => bubble.classList.add("hidden"), sticky ? 13000 : 6500);
}

function applyState(s) {
  last = s;
  setState(s.creature_state);
  if (typeof s.spent_today === "number")
    badge.textContent = "$" + s.spent_today.toFixed(2);
  const speech = s.speech || s.headline;
  if (speech && speech !== lastSpeech) {
    lastSpeech = speech;
    showBubble(speech, ["shocked", "alert"].includes(s.creature_state));
  }
  window.nibble.reportState(s);
  if (!mini.classList.contains("hidden")) renderMini();
}

function renderMini() {
  if (!last) return;
  document.getElementById("m-spent").textContent =
    "$" + (last.spent_today || 0).toFixed(2);
  const pct = Math.max(0, last.pct_used || 0);
  document.getElementById("m-pct").textContent = pct.toFixed(0) + "%";
  document.getElementById("m-sub").textContent =
    `today · budget $${(last.daily_budget || 0).toFixed(0)}`;
  document.getElementById("m-ring").style.setProperty(
    "--mp", Math.min(100, pct));

  const tools = (last.per_tool || []).filter((t) => t.cost != null);
  if (!tools.length) {
    mList.innerHTML = `<div class="mini-empty">No AI spend yet today.</div>`;
    return;
  }
  mList.innerHTML = tools.map((t) => `
    <div class="mini-row" data-tool="${t.tool}">
      <span class="ar">›</span>
      <span class="nm">${t.tool.replace(/_/g, " ")}</span>
      <span class="vl">$${(t.cost || 0).toFixed(2)}</span>
    </div>
    <div class="mini-det" data-det="${t.tool}" style="display:none"></div>
  `).join("");

  mList.querySelectorAll(".mini-row").forEach((row) => {
    row.addEventListener("click", () => toggleTool(row.dataset.tool));
  });
  if (openTool) expandTool(openTool, true);
}

async function toggleTool(tool) {
  if (openTool === tool) { openTool = null; collapseAll(); return; }
  openTool = tool;
  collapseAll();
  expandTool(tool, false);
}

function collapseAll() {
  mList.querySelectorAll(".mini-row").forEach((r) =>
    r.classList.remove("open"));
  mList.querySelectorAll(".mini-det").forEach((d) =>
    (d.style.display = "none"));
}

async function expandTool(tool, silent) {
  const row = mList.querySelector(`.mini-row[data-tool="${tool}"]`);
  const det = mList.querySelector(`.mini-det[data-det="${tool}"]`);
  if (!row || !det) return;
  row.classList.add("open");
  det.style.display = "block";
  if (!silent || !det.dataset.loaded) {
    det.innerHTML = `<div><span>loading…</span></div>`;
    try {
      const d = await window.nibble.getTool(tool);
      const models = (d.models || []).filter((m) => m.cost != null);
      det.innerHTML = models.length
        ? models.map((m) => `
            <div>
              <span class="mdl">${m.model || "unknown"}</span>
              <span>${(m.tokens || 0).toLocaleString()} tok ·
                ${m.n} req · $${(m.cost || 0).toFixed(2)}</span>
            </div>`).join("")
        : `<div><span>no detail</span></div>`;
      det.dataset.loaded = "1";
    } catch (_) {
      det.innerHTML = `<div><span>unavailable</span></div>`;
    }
  }
}

function toggleMini() {
  interacted = true;
  hint.classList.remove("show");
  const willOpen = mini.classList.contains("hidden");
  if (willOpen) {
    bubble.classList.add("hidden");
    renderMini();
    mini.classList.remove("hidden");
    stage.classList.add("mini-open");
  } else {
    mini.classList.add("hidden");
    stage.classList.remove("mini-open");
    openTool = null;
  }
}

creature.addEventListener("click", (e) => {
  if (e.detail === 0) return;       // ignore drag-release
  toggleMini();
});
document.getElementById("m-open").addEventListener("click", (e) => {
  e.stopPropagation();
  window.nibble.openPanel();
  mini.classList.add("hidden");
  stage.classList.remove("mini-open");
});

setTimeout(() => { if (!interacted) hint.classList.add("show"); }, 2500);
setTimeout(() => hint.classList.remove("show"), 11000);

window.NibbleWS.start(applyState, () => setState("reconnecting"));
