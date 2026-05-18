"use strict";
const {
  app, BrowserWindow, Tray, Menu, ipcMain, screen, shell, Notification,
} = require("electron");
const path = require("path");
const http = require("http");
const { Sidecar } = require("./sidecar");

let tray = null;
let creatureWin = null;
let panelWin = null;
let cockpitWin = null;
let sidecar = null;
let core = { port: 0, token: "" };
let lastSpent = 0;
let notifiedOver = false;

// One-shot HTTP (no keep-alive socket reuse -> avoids ECONNRESET races).
function api(method, p, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(
      {
        host: "127.0.0.1", port: core.port, path: p, method,
        agent: false, timeout: 8000,
        headers: {
          Authorization: `Bearer ${core.token}`,
          "Content-Type": "application/json",
          Connection: "close",
        },
      },
      (res) => {
        let b = "";
        res.on("data", (c) => (b += c));
        res.on("end", () => {
          try { resolve(JSON.parse(b || "{}")); }
          catch (e) { reject(e); }
        });
      }
    );
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

function createCreature() {
  const { width } = screen.getPrimaryDisplay().workAreaSize;
  creatureWin = new BrowserWindow({
    width: 340, height: 500, x: width - 380, y: 70,
    frame: false, transparent: true, resizable: false,
    alwaysOnTop: true, skipTaskbar: true, hasShadow: false,
    webPreferences: { preload: path.join(__dirname, "preload.js") },
  });
  creatureWin.setAlwaysOnTop(true, "screen-saver");
  creatureWin.setVisibleOnAllWorkspaces(true);
  // Start click-through: the transparent window must not block the desktop
  // behind it. `forward:true` still delivers mousemove to the renderer so it
  // can hit-test the sprite and flip interactivity on/off.
  creatureWin.setIgnoreMouseEvents(true, { forward: true });
  creatureWin.loadFile("windows/creature.html");
  creatureWin.on("closed", () => (creatureWin = null));
}

function openPanel() {
  if (panelWin) { panelWin.show(); panelWin.focus(); return; }
  panelWin = new BrowserWindow({
    width: 560, height: 720, resizable: true, title: "Nibble",
    backgroundColor: "#0c1118", minWidth: 460, minHeight: 560,
    webPreferences: { preload: path.join(__dirname, "preload.js") },
  });
  panelWin.removeMenu();
  panelWin.loadFile("windows/panel.html");
  panelWin.on("closed", () => (panelWin = null));
}

function openCockpit() {
  if (cockpitWin) { cockpitWin.show(); cockpitWin.focus(); return; }
  cockpitWin = new BrowserWindow({
    width: 880, height: 760, resizable: true, title: "Nibble Conductor",
    backgroundColor: "#0c1118", minWidth: 680, minHeight: 560,
    webPreferences: { preload: path.join(__dirname, "preload.js") },
  });
  cockpitWin.removeMenu();
  cockpitWin.loadFile("windows/cockpit.html");
  cockpitWin.on("closed", () => (cockpitWin = null));
}

function buildTray() {
  tray = new Tray(path.join(__dirname, "assets", "tray.png"));
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open Conductor", click: openCockpit },
    { label: "Open dashboard", click: openPanel },
    { label: "Show / hide creature", click: () => {
        if (!creatureWin) createCreature();
        else creatureWin.isVisible() ? creatureWin.hide()
                                     : creatureWin.show();
      } },
    { type: "separator" },
    { label: "Quit Nibble", click: () => app.quit() },
  ]));
  tray.setToolTip("Nibble");
  tray.on("double-click", openPanel);
}

ipcMain.handle("core-info", () => core);
ipcMain.handle("get-summary", () => api("GET", "/api/summary"));
ipcMain.handle("get-tool", (_e, t) =>
  api("GET", "/api/tool/" + encodeURIComponent(t)));
ipcMain.handle("get-settings", () => api("GET", "/api/settings"));
ipcMain.handle("save-settings", (_e, p) => api("POST", "/api/settings", p));
ipcMain.on("set-interactive", (_e, on) => {
  if (creatureWin && !creatureWin.isDestroyed()) {
    creatureWin.setIgnoreMouseEvents(!on, { forward: true });
  }
});
ipcMain.on("open-panel", openPanel);
ipcMain.on("open-cockpit", openCockpit);
ipcMain.on("open-external", (_e, url) => shell.openExternal(url));

ipcMain.handle("cond-snapshot", () => api("GET", "/api/conductor"));
ipcMain.handle("cond-gate", (_e, { gid, action, reason }) =>
  api("POST", "/api/gate/" + encodeURIComponent(gid), { action, reason }));
ipcMain.handle("cond-panic", (_e, on) => api("POST", "/api/panic", { on }));
ipcMain.handle("cond-session-mode", (_e, { sid, mode }) =>
  api("POST", "/api/session/" + encodeURIComponent(sid) + "/mode",
      { mode }));
ipcMain.handle("cond-policy-add", (_e, p) => api("POST", "/api/policies", p));
ipcMain.handle("cond-policy-patch", (_e, { pid, p }) =>
  api("PATCH", "/api/policies/" + pid, p));
ipcMain.handle("cond-policy-del", (_e, pid) =>
  api("DELETE", "/api/policies/" + pid));
ipcMain.handle("cond-cap", (_e, c) => api("POST", "/api/caps", c));
ipcMain.handle("cond-hooks-status", () => api("GET", "/api/hooks/status"));
ipcMain.handle("cond-hooks-install", () =>
  api("POST", "/api/hooks/install"));
ipcMain.handle("cond-hooks-uninstall", () =>
  api("POST", "/api/hooks/uninstall"));
ipcMain.on("tray-state", (_e, s) => {
  if (!s || typeof s.spent_today !== "number") return;
  lastSpent = s.spent_today;
  if (tray) tray.setToolTip(`Nibble — $${lastSpent.toFixed(2)} today`);
  if (s.pct_used >= 100 && !notifiedOver) {
    notifiedOver = true;
    new Notification({
      title: "Nibble — over budget",
      body: `You've spent $${lastSpent.toFixed(2)} on AI today.`,
    }).show();
  }
  if (s.pct_used < 90) notifiedOver = false;
});

app.whenReady().then(async () => {
  buildTray();
  sidecar = new Sidecar(
    (info) => {
      core = info;
      if (creatureWin) creatureWin.webContents.send("core-reconnect", core);
      if (panelWin) panelWin.webContents.send("core-reconnect", core);
    },
    () => {}
  );
  try {
    core = await sidecar.start();
  } catch (e) {
    new Notification({
      title: "Nibble failed to start",
      body: String(e.message || e),
    }).show();
  }
  createCreature();
});

app.on("window-all-closed", (e) => e.preventDefault());
app.on("before-quit", () => sidecar && sidecar.stop());
