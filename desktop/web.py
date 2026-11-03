"""Same-origin production UI hosting and local desktop session protection."""
from __future__ import annotations

import asyncio
import hmac
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal


class PaperInitializeBody(BaseModel):
    book: Literal["spot", "futures"]
    balance: float = Field(gt=0, le=1_000_000_000, allow_inf_nan=False)
    confirmation: Literal["INITIALIZE PAPER BOOK"]


class DesktopSession:
    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"x-sigma-desktop", b"").decode("utf-8", errors="replace")
            if not hmac.compare_digest(supplied, self.token):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": b'{"detail":"Desktop session required"}'})
                return
        await self.app(scope, receive, send)


def configure_desktop(app, assets: Path) -> None:
    token = os.environ.get("SIGMA_DESKTOP_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("A private desktop session token is required")
    app.add_middleware(DesktopSession, token=token)

    @app.get("/api/desktop/status")
    async def desktop_status():
        from app.server.main import state
        from app.tv.worker import get_tv_queue
        from app.core.redis_client import is_fake_redis
        import shutil
        return {
            "desktop": True, "mode": "paper", "redis": "memory" if is_fake_redis() else "persistent",
            "market_source": state.ingestor.feed_source_effective,
            "backtest_available": state.tv_backtest is not None,
            "kraken_cli_available": shutil.which("kraken") is not None,
            "worker": get_tv_queue(state.config).snapshot(),
            "tasks_alive": bool(state._tasks) and all(not task.done() for task in state._tasks),
            "workspace": str(Path.cwd()),
        }

    @app.post("/api/desktop/paper/initialize")
    async def initialize_paper(body: PaperInitializeBody):
        from app.core import blueprint as bp
        from app.server.main import state
        from app.execution.KrakenCliBridge import KrakenCliBridge
        bridge = KrakenCliBridge(state.config, execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
                                 futures=body.book == "futures")
        leaf = "paper/init" if body.book == "spot" else "futures/paper/init"
        result = await asyncio.to_thread(bridge.run_leaf, leaf, f"--balance={body.balance:g}")
        if not result.ok:
            raise HTTPException(503, {"code": result.error_code,
                                      "detail": result.stderr or result.stdout or "Paper book initialization failed"})
        return {"ok": True, "mode": "paper", "book": body.book, "receipt": result.stdout}

    # Register LAST: unknown APIs must never become a successful SPA response.
    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Unknown API route")
        candidate = (assets / path).resolve()
        root = assets.resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(404)
        if candidate.is_file():
            return FileResponse(candidate)
        if path.startswith("assets/"):
            raise HTTPException(404)
        return FileResponse(root / "index.html")
