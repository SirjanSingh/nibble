"use strict";
const stage = document.getElementById("stage");
const badge = document.getElementById("badge");
const bubble = document.getElementById("bubble");
const bubbleText = document.getElementById("bubble-text");
const creature = document.getElementById("creature");
const hint = document.getElementById("hint");

const STATES = ["idle", "sleeping", "happy", "alert", "shocked", "reconnecting"];
let bubbleTimer = null;
let lastSpeech = "";
let interacted = false;

function setState(name) {
  stage.dataset.state = STATES.includes(name) ? name : "idle";
}

function showBubble(text, sticky) {
  if (!text) return;
  bubbleText.textContent = text;
  bubble.classList.remove("hidden");
  if (bubbleTimer) clearTimeout(bubbleTimer);
  bubbleTimer = setTimeout(
    () => bubble.classList.add("hidden"),
    sticky ? 13000 : 6500
  );
}

function applyState(s) {
  setState(s.creature_state);
  if (typeof s.spent_today === "number") {
    badge.textContent = "$" + s.spent_today.toFixed(2);
  }
  const speech = s.speech || s.headline;
  if (speech && speech !== lastSpeech) {
    lastSpeech = speech;
    const sticky = ["shocked", "alert"].includes(s.creature_state);
    showBubble(speech, sticky);
  }
  window.nibble.reportState(s);
}

creature.addEventListener("click", (e) => {
  if (e.detail === 0) return; // ignore drag-release
  interacted = true;
  hint.classList.remove("show");
  window.nibble.openPanel();
});

// first-run affordance: nudge the user that the creature is clickable
setTimeout(() => { if (!interacted) hint.classList.add("show"); }, 2500);
setTimeout(() => hint.classList.remove("show"), 11000);

window.NibbleWS.start(
  applyState,
  () => setState("reconnecting")  // only after the grace period
);
