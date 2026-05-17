<div align="center">

# 🐡 Nibble

### One creature, all your AI tools. Know what you're actually spending.

Nibble is a **local-first** desktop app that aggregates your AI usage cost
across every tool into a single dollar figure — surfaced through a system-tray
app and a friendly always-on-top creature that reacts to your spend.

**No account. No cloud. No telemetry.** Everything stays on your machine.

`Python core` · `Electron UI` · `SQLite` · `MIT`

</div>

---

## Why

A developer using Claude Code + the OpenAI API + Anthropic API has **no idea**
that combined they burned $40 this week. Each tool hides spend in its own
silo. Nibble answers the one question nobody else does:

> **How much am I spending on AI today — total, everywhere?**

## What it does

- **One number.** Unified daily spend in USD across all connected tools.
- **A creature that reacts.** A desktop mascot that's calm under budget,
  sweating when you're burning fast, and shocked on a spend spike — with a
  one-line explanation in a speech bubble.
- **Rate-of-spend alerts.** Not just totals — *"58% of budget used and it's
  only 11am, on pace for $36 by midnight."*
- **Local & private.** All usage lives in a local SQLite DB. API keys are
  stored in your OS keyring, never in the database, never sent anywhere.
- **Smart commentary (opt-in).** On an anomaly only, one tiny Anthropic
  Haiku call (your key) turns the numbers into a useful sentence.

## Sources (v0.1)

| Tool | How | Needs |
|------|-----|-------|
| **Claude Code** | reads `~/.claude/projects/**/*.jsonl` locally | nothing |
| **OpenAI** | Organization usage API | org **admin** key |
| **Anthropic** | Organization usage/cost API | org **admin** key |

Without API keys, Nibble still fully tracks Claude Code. Cursor, Copilot and
Gemini are planned for v0.2.

## Architecture

```
Electron UI  (tray · animated creature · dashboard)
     ▲  WebSocket state push  ·  loopback + token auth
     ▼
Python core  (collectors → pricing → SQLite → budget/anomaly → FastAPI)
```

The Electron shell spawns and supervises the Python core as a sidecar on a
random loopback port with a generated token. Pricing uses the live
[LiteLLM price table](https://github.com/BerriAI/litellm) with a hardcoded
offline fallback (same approach as `ccusage`).

## Run (dev)

**1 — Python core**
```powershell
cd core
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**2 — Electron UI** (auto-spawns the core)
```powershell
cd app
npm install
npm start
```

A creature appears near the top-right of your screen. Click it (or
double-click the tray icon) to open the dashboard. Set your daily budget and
optional API keys there.

## Build a Windows installer

See [`core/build_sidecar.md`](core/build_sidecar.md) — PyInstaller bundles the
core, then `npm run dist` (electron-builder) produces an installer with the
core embedded.

## Tests

```powershell
cd core
.\.venv\Scripts\python.exe -m pytest -q
```

Covers pricing math, model best-match, dedupe, the Claude Code JSONL parser,
and budget/state derivation.

## Privacy

- Usage data: local SQLite at `%LOCALAPPDATA%\Nibble`.
- API keys: OS keyring only.
- Network calls: only the price table fetch, the optional opt-in anomaly
  comment, and (if you add keys) the OpenAI/Anthropic usage endpoints.

## License

[MIT](LICENSE) © 2026 Sirjan Singh. Creature artwork is original CSS/SVG
(no third-party assets).
