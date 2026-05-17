"""FastAPI app: token-gated loopback WS (state push) + REST (commands)."""
from __future__ import annotations

import asyncio
import json
from typing import Set

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)

from . import config, secrets
from .store import Store


class Hub:
    """Holds the latest state and fans it out to connected UI clients."""

    def __init__(self):
        self.clients: Set[WebSocket] = set()
        self.latest: dict = {"type": "state", "creature_state": "sleeping"}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop):
        self._loop = loop

    async def _send(self, ws: WebSocket, msg: dict):
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            self.clients.discard(ws)

    async def broadcast(self, msg: dict):
        if msg.get("type") == "state":
            self.latest = msg
        for ws in list(self.clients):
            await self._send(ws, msg)

    def broadcast_threadsafe(self, msg: dict):
        """Callable from the service thread."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(msg), self._loop)


def create_app(store: Store, token: str, hub: Hub) -> FastAPI:
    app = FastAPI(title="nibble-core", docs_url=None, redoc_url=None)

    def auth(authorization: str = Header(default="")):
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    def health():
        return {"ok": True, "version": "0.1.0"}

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        return hub.latest

    @app.get("/api/summary", dependencies=[Depends(auth)])
    def summary():
        from .budget import as_dict, compute

        b = compute(store)
        return {
            "budget": as_dict(b),
            "daily": store.daily_costs(14),
        }

    @app.get("/api/tool/{tool}", dependencies=[Depends(auth)])
    def tool_detail(tool: str):
        from .budget import _local_midnight_utc_iso

        since = _local_midnight_utc_iso()
        return {"tool": tool, "since": since,
                "models": store.tool_models_since(tool, since)}

    @app.get("/api/settings", dependencies=[Depends(auth)])
    def get_settings():
        return {
            "daily_budget": float(store.get_setting("daily_budget", 10.0)),
            "commentary_enabled": store.get_setting(
                "commentary_enabled", "0"
            ) in ("1", "true", "True"),
            "keys": {
                "openai": secrets.has_key(config.KEY_OPENAI),
                "anthropic": secrets.has_key(config.KEY_ANTHROPIC),
                "anthropic_commentary": secrets.has_key(
                    config.KEY_ANTHROPIC_COMMENTARY
                ),
            },
        }

    @app.post("/api/settings", dependencies=[Depends(auth)])
    async def set_settings(payload: dict):
        if "daily_budget" in payload:
            try:
                store.set_setting(
                    "daily_budget", float(payload["daily_budget"])
                )
            except (TypeError, ValueError):
                raise HTTPException(400, "daily_budget must be a number")
        if "commentary_enabled" in payload:
            store.set_setting(
                "commentary_enabled",
                "1" if payload["commentary_enabled"] else "0",
            )
        # API keys: write-only, never read back
        keymap = {
            "openai_key": config.KEY_OPENAI,
            "anthropic_key": config.KEY_ANTHROPIC,
            "anthropic_commentary_key": config.KEY_ANTHROPIC_COMMENTARY,
        }
        for field, name in keymap.items():
            if field in payload:
                secrets.set_key(name, payload[field] or "")
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        if websocket.query_params.get("token") != token:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        hub.clients.add(websocket)
        await websocket.send_text(json.dumps(hub.latest))
        try:
            while True:
                await websocket.receive_text()  # keepalive / pings
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(websocket)

    return app
