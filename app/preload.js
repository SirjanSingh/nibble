"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("nibble", {
  // connection info for a direct renderer WebSocket (stable push transport)
  coreInfo: () => ipcRenderer.invoke("core-info"),
  // request/response helpers proxied through main (no socket reuse)
  getSummary: () => ipcRenderer.invoke("get-summary"),
  getTool: (t) => ipcRenderer.invoke("get-tool", t),
  getSettings: () => ipcRenderer.invoke("get-settings"),
  saveSettings: (p) => ipcRenderer.invoke("save-settings", p),
  // renderer -> main: keep tray tooltip / notifications in sync
  reportState: (s) => ipcRenderer.send("tray-state", s),
  setInteractive: (on) => ipcRenderer.send("set-interactive", on),
  openPanel: () => ipcRenderer.send("open-panel"),
  openCockpit: () => ipcRenderer.send("open-cockpit"),
  // Conductor actions (proxied to the core via main)
  cond: {
    snapshot: () => ipcRenderer.invoke("cond-snapshot"),
    gate: (gid, action, reason) =>
      ipcRenderer.invoke("cond-gate", { gid, action, reason }),
    panic: (on) => ipcRenderer.invoke("cond-panic", on),
    sessionMode: (sid, mode) =>
      ipcRenderer.invoke("cond-session-mode", { sid, mode }),
    addPolicy: (p) => ipcRenderer.invoke("cond-policy-add", p),
    patchPolicy: (pid, p) =>
      ipcRenderer.invoke("cond-policy-patch", { pid, p }),
    delPolicy: (pid) => ipcRenderer.invoke("cond-policy-del", pid),
    setCap: (c) => ipcRenderer.invoke("cond-cap", c),
    hooksStatus: () => ipcRenderer.invoke("cond-hooks-status"),
    hooksInstall: () => ipcRenderer.invoke("cond-hooks-install"),
    hooksUninstall: () => ipcRenderer.invoke("cond-hooks-uninstall"),
  },
  openExternal: (url) => ipcRenderer.send("open-external", url),
});

// core respawned with a new port/token -> tell the WS client to reconnect
ipcRenderer.on("core-reconnect", () =>
  window.dispatchEvent(new Event("core-reconnect-hint"))
);
