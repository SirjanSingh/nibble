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
from .governor import Governor
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


def create_app(store: Store, token: str, hub: Hub,
                governor: Governor | None = None) -> FastAPI:
    app = FastAPI(title="nibble-core", docs_url=None, redoc_url=None)
    gov = governor or Governor(store)

    def auth(authorization: str = Header(default="")):
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    def conductor_snapshot() -> dict:
        return {
            "type": "conductor",
            "panic": gov.panic,
            "sessions": store.list_sessions(40),
            "pending": gov.pending(),
            "events": store.recent_events(30),
            "policies": store.list_policies(),
            "caps": store.list_caps(),
        }

    async def push_conductor():
        await hub.broadcast(conductor_snapshot())

    @app.get("/health")
    def health():
        return {"ok": True, "version": "0.1.0"}

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        return hub.latest

    @app.get("/api/summary", dependencies=[Depends(auth)])
    def summary():
        from .budget import as_dict, compute, local_daily

        b = compute(store)
        return {
            "budget": as_dict(b),
            "daily": local_daily(store, 14),
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

    # ---- Conductor: governor endpoints --------------------------------
    @app.post("/api/hook", dependencies=[Depends(auth)])
    async def hook(payload: dict):
        n = gov.normalize(payload)
        d = gov.decide_static(n)
        if d.action == "ask":
            g = gov.new_gate(n)
            loop = asyncio.get_running_loop()
            g.fut = loop.create_future()
            await push_conductor()
            try:
                d = await asyncio.wait_for(
                    asyncio.shield(g.fut), timeout=gov.supervise_timeout())
            except asyncio.TimeoutError:
                d = gov.failsafe(n)
            finally:
                gov.gates.pop(g.gid, None)
        gov.record(n, d)
        await push_conductor()
        return {"action": d.action, "reason": d.reason,
                "by": d.by, "protocol": "1"}

    @app.get("/api/conductor", dependencies=[Depends(auth)])
    def conductor():
        return conductor_snapshot()

    @app.post("/api/gate/{gid}", dependencies=[Depends(auth)])
    async def resolve_gate(gid: str, payload: dict):
        ok = gov.resolve_gate(
            gid, (payload.get("action") or "deny").lower(),
            payload.get("reason") or "")
        await push_conductor()
        return {"ok": ok}

    @app.post("/api/panic", dependencies=[Depends(auth)])
    async def panic(payload: dict):
        gov.set_panic(bool(payload.get("on")))
        await push_conductor()
        return {"ok": True, "panic": gov.panic}

    @app.post("/api/session/{sid}/mode", dependencies=[Depends(auth)])
    async def session_mode(sid: str, payload: dict):
        mode = payload.get("mode")
        if mode not in ("supervise", "autopilot"):
            raise HTTPException(400, "mode must be supervise|autopilot")
        store.set_session_mode(sid, mode)
        await push_conductor()
        return {"ok": True}

    @app.post("/api/policies", dependencies=[Depends(auth)])
    async def add_policy(payload: dict):
        store.add_policy(
            payload.get("label", "rule"),
            json.dumps(payload.get("match") or {}),
            (payload.get("action") or "ask").lower(),
            payload.get("reason") or "",
            int(payload.get("idx", 0)),
        )
        await push_conductor()
        return {"ok": True}

    @app.patch("/api/policies/{pid}", dependencies=[Depends(auth)])
    async def patch_policy(pid: int, payload: dict):
        fields = {}
        if "enabled" in payload:
            fields["enabled"] = 1 if payload["enabled"] else 0
        if "action" in payload:
            fields["action"] = payload["action"]
        if "reason" in payload:
            fields["reason"] = payload["reason"]
        if "match" in payload:
            fields["match_json"] = json.dumps(payload["match"])
        store.update_policy(pid, **fields)
        await push_conductor()
        return {"ok": True}

    @app.delete("/api/policies/{pid}", dependencies=[Depends(auth)])
    async def del_policy(pid: int):
        store.delete_policy(pid)
        await push_conductor()
        return {"ok": True}

    @app.post("/api/caps", dependencies=[Depends(auth)])
    async def set_cap(payload: dict):
        store.set_cap(
            payload.get("scope", "today"),
            payload.get("limit_usd"),
            payload.get("limit_tokens"),
        )
        await push_conductor()
        return {"ok": True}

    @app.get("/api/hooks/status", dependencies=[Depends(auth)])
    def hooks_status():
        from . import hooks_install
        return hooks_install.status()

    @app.post("/api/hooks/install", dependencies=[Depends(auth)])
    def hooks_install_ep():
        from . import hooks_install
        return hooks_install.install()

    @app.post("/api/hooks/uninstall", dependencies=[Depends(auth)])
    def hooks_uninstall_ep():
        from . import hooks_install
        return hooks_install.uninstall()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        if websocket.query_params.get("token") != token:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        hub.clients.add(websocket)
        await websocket.send_text(json.dumps(hub.latest))
        await websocket.send_text(json.dumps(conductor_snapshot()))
        try:
            while True:
                await websocket.receive_text()  # keepalive / pings
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(websocket)

    return app
