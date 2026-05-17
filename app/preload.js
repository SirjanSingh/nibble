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
  openPanel: () => ipcRenderer.send("open-panel"),
  openExternal: (url) => ipcRenderer.send("open-external", url),
});

// core respawned with a new port/token -> tell the WS client to reconnect
ipcRenderer.on("core-reconnect", () =>
  window.dispatchEvent(new Event("core-reconnect-hint"))
);
