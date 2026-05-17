# Packaging the sidecar

The Electron app expects a single-file binary at `core/dist/nibble-core`
(`.exe` on Windows). Build it with PyInstaller:

```powershell
cd core
.\.venv\Scripts\Activate.ps1
pip install pyinstaller
pyinstaller --onefile --name nibble-core ^
  --collect-submodules uvicorn --collect-submodules nibble ^
  run_core.py
```

Then build the installer:

```powershell
cd ..\app
npm install
npm run dist
```

`electron-builder` copies `core/dist/nibble-core` into the app resources
(see `package.json > build.extraResources`). At runtime `sidecar.js`
prefers the packaged binary, then a dev venv, then system `python`.
