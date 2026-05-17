"use strict";
// Spawns and supervises the Python core. Parses the readiness line
// "NIBBLE_READY port=<p> token=<t>" from stdout, then resolves with {port,token}.
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const MAX_RESTARTS = 3;

function resolveCoreCmd() {
  // 1) explicit override (manual core during dev)
  if (process.env.NIBBLE_CORE_CMD) {
    return { cmd: process.env.NIBBLE_CORE_CMD, args: [], shell: true };
  }
  // 2) packaged binary in resources/
  const packaged = path.join(
    process.resourcesPath || "",
    "nibble-core",
    process.platform === "win32" ? "nibble-core.exe" : "nibble-core"
  );
  if (fs.existsSync(packaged)) return { cmd: packaged, args: [], shell: false };

  // 3) dev: venv python running the module
  const venvPy = path.join(
    __dirname, "..", "core", ".venv",
    process.platform === "win32" ? "Scripts" : "bin",
    process.platform === "win32" ? "python.exe" : "python"
  );
  const py = fs.existsSync(venvPy) ? venvPy : "python";
  return { cmd: py, args: ["-m", "nibble"], shell: false,
           cwd: path.join(__dirname, "..", "core") };
}

class Sidecar {
  constructor(onState, onDown) {
    this.onState = onState;
    this.onDown = onDown;
    this.proc = null;
    this.restarts = 0;
    this.info = null;
  }

  start() {
    return new Promise((resolve, reject) => {
      const { cmd, args, shell, cwd } = resolveCoreCmd();
      const proc = spawn(cmd, [...args, "--port", "0"], {
        cwd, shell, windowsHide: true,
      });
      this.proc = proc;
      let buf = "";
      let ready = false;

      proc.stdout.on("data", (d) => {
        buf += d.toString();
        let i;
        while ((i = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 1);
          const m = line.match(/NIBBLE_READY port=(\d+) token=(\S+)/);
          if (m && !ready) {
            ready = true;
            this.info = { port: Number(m[1]), token: m[2] };
            resolve(this.info);
          }
        }
      });
      proc.stderr.on("data", (d) =>
        process.stderr.write(`[core] ${d}`)
      );
      proc.on("exit", (code) => {
        if (!ready) {
          reject(new Error(`core exited early (code ${code})`));
          return;
        }
        this.onDown && this.onDown();
        if (this.restarts < MAX_RESTARTS) {
          this.restarts += 1;
          setTimeout(() => {
            this.start()
              .then((info) => this.onState && this.onState(info))
              .catch(() => {});
          }, 1500);
        }
      });
    });
  }

  stop() {
    if (this.proc && !this.proc.killed) {
      this.proc.kill();
    }
  }
}

module.exports = { Sidecar };
