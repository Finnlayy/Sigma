"""Frozen service entrypoint. Reuses the existing Core and scraper applications."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def prepare_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    for name in ("config", "models"):
        source = resource_root() / name
        if source.exists():
            shutil.copytree(source, workspace / name, dirs_exist_ok=True,
                            copy_function=_copy_missing)
    source = resource_root() / "app/tv/selectors.yaml"
    target = workspace / "app/tv/selectors.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.exists() and not target.exists():
        shutil.copy2(source, target)
    for directory in ("logs", "signals", "secrets", "tv_jobs", "tv_exports", "redis"):
        (workspace / "data" / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chdir(workspace)
    from dotenv import load_dotenv
    load_dotenv(workspace / ".env", override=False, interpolate=False)
    # Packaging is paper-only, regardless of a saved configuration or shell env.
    os.environ.update({
        "SIGMA_DESKTOP": "1", "SIGMA_LIVE_TRADING": "0",
        "SIGMA_ALLOW_FAKE_REDIS": "0", "SIGMA_ALLOW_SYNTHETIC": "0",
        "SIGMA_SCRAPER_SYNTHETIC_FALLBACK": "0",
        "SIGMA_SCRAPER_VENDOR_PATH": str(resource_root() / "vendor/tradingview-scraper"),
        "SIGMA_DATA_DIR": str(workspace / "data"),
    })
    os.environ.pop("PYTEST_CURRENT_TEST", None)
    os.environ["SIGMA_MARKET_SOURCE"] = "tv_scraper"
    os.environ.setdefault("SIGMA_TV_MCP_URL", "")
    # Keep CLI paper journals separate from any host Kraken installation/credentials.
    os.environ["XDG_CONFIG_HOME"] = str(workspace / "cli-config")


def _copy_missing(source, destination):
    if not Path(destination).exists():
        shutil.copy2(source, destination)
    return str(destination)


def guard_parent(parent_pid: int) -> None:
    """Each service has its own process group; reap it after launcher death."""
    def watch():
        while True:
            time.sleep(1)
            if os.getppid() != parent_pid:
                os.killpg(os.getpgrp(), signal.SIGTERM)
                time.sleep(20)
                os.killpg(os.getpgrp(), signal.SIGKILL)
                return
    threading.Thread(target=watch, daemon=True, name="launcher-watchdog").start()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", choices=("core", "scraper", "redis"), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--redis-binary")
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    os.umask(0o077)
    prepare_workspace(args.workspace.resolve())
    if args.parent_pid:
        guard_parent(args.parent_pid)
    if args.service == "redis":
        config_file = Path.cwd() / "data/redis/desktop.conf"
        password = os.environ.get("SIGMA_REDIS_PASSWORD", "")
        if len(password) < 32 or not password.isalnum():
            raise RuntimeError("Private Redis password is required")
        config_file.write_text(
            f'bind 127.0.0.1\nport {args.port}\nprotected-mode yes\n'
            f'requirepass {password}\nappendonly yes\nappendfsync everysec\n'
            f'dir "{Path.cwd() / "data/redis"}"\nsave ""\ndaemonize no\n')
        config_file.chmod(0o600)
        process = subprocess.Popen([args.redis_binary, str(config_file)])
        def stop(_signum, _frame):
            process.terminate()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        raise SystemExit(process.wait())

    import uvicorn
    if args.service == "scraper":
        from app.scraper.main import app
    else:
        import logging
        worker_logger = logging.getLogger("app.tv.worker")
        handler = logging.FileHandler(Path.cwd() / "data/logs/tv_worker.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        worker_logger.addHandler(handler)
        from app.server.main import app
        from desktop.web import configure_desktop
        configure_desktop(app, resource_root() / "dist")
    netron = None
    if args.service == "core":
        import socket
        from app.services.netron_server import get_netron_service
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        netron = get_netron_service(port=port)
        netron.start_server()
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info", timeout_graceful_shutdown=15)
    finally:
        if netron is not None:
            netron.stop()


if __name__ == "__main__":
    main()
