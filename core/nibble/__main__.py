"""Nibble core entrypoint.

    python -m nibble --port 8777 --token <token>

Prints a single readiness line to stdout once the HTTP server is listening so
the Electron supervisor knows it can connect:

    NIBBLE_READY port=8777
"""
from __future__ import annotations

import argparse
import asyncio
import secrets as pysecrets
import sys

import uvicorn

from .server import Hub, create_app
from .service import start_in_thread
from .store import Store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nibble")
    ap.add_argument("--port", type=int, default=0,
                    help="loopback port (0 = let OS pick)")
    ap.add_argument("--token", default="",
                    help="shared auth token (generated if omitted)")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)

    token = args.token or pysecrets.token_urlsafe(24)
    store = Store()
    hub = Hub()
    app = create_app(store, token, hub)

    config = uvicorn.Config(
        app, host=args.host, port=args.port, log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    async def runner():
        loop = asyncio.get_running_loop()
        hub.bind_loop(loop)
        start_in_thread(store, hub)

        # Start serving; once sockets are bound, emit the readiness line.
        serve_task = asyncio.ensure_future(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        bound = server.servers[0].sockets[0].getsockname()
        print(
            f"NIBBLE_READY port={bound[1]} token={token}",
            flush=True,
        )
        await serve_task

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
