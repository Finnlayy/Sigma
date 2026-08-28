"""
=========================================================
Datei:      app/services/netron_server.py
Zweck:      §38 / Axiom 16 — Netron ONNX Visualization & Inspection Stack
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Services
=========================================================

Netron ist der kanonische ONNX-Graph-Inspector (Layer, Operatoren,
Tensor-Shapes, Gewichte) und laeuft **vollstaendig offline** auf Port 8082.

Noir-Gate (§38.7): keine Cloud-Calls, nur ``.onnx`` aus ``./models`` bzw. der
Registry, ``browse=False``, Production bindet ``127.0.0.1``.

Direktstart (systemd ``sigma-netron.service``)::

    /opt/sigma/venv/bin/python app/services/netron_server.py
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.services.netron_server")


class NetronUnavailable(RuntimeError):
    """``pip install netron`` fehlt — Panel zeigt den Hinweis an."""


class NetronVisualizerService:
    """Duenner Wrapper um ``netron.start`` mit dynamischem Model-Switch."""

    def __init__(self, *, models_dir: str = bp.NETRON_MODELS_DIR,
                 port: int = bp.PORT_NETRON, dev: bool = False,
                 netron_module: Any = None) -> None:
        self.models_dir = models_dir
        self.port = port
        self.dev = dev
        self.host = bp.NETRON_BIND_DEV if dev else bp.NETRON_BIND_PROD
        self._netron = netron_module
        self.running = False
        self.model_path: str = ""
        self.version_tag: str = ""
        self.started_at: float = 0.0
        self.last_error: str = ""

    # ------------------------------------------------------------ module --
    @property
    def netron(self):
        if self._netron is None:
            try:
                import netron  # type: ignore
            except ImportError as exc:      # pragma: no cover - offline-Umgebung
                raise NetronUnavailable(
                    "netron nicht installiert — 'pip install netron'") from exc
            self._netron = netron
        return self._netron

    @property
    def available(self) -> bool:
        try:
            return self.netron is not None
        except NetronUnavailable:
            return False

    @property
    def url(self) -> str:
        return f"http://{'localhost' if not self.dev else '0.0.0.0'}:{self.port}"

    # -------------------------------------------------------- validation --
    def resolve_model(self, path_or_tag: str) -> str:
        """Tag oder Pfad -> validierter ``.onnx``-Pfad innerhalb ``models_dir``."""
        candidate = path_or_tag or bp.NETRON_DEFAULT_MODEL
        suffix = os.path.splitext(candidate)[1]
        if suffix and suffix != bp.NETRON_ALLOWED_SUFFIX:
            raise ValueError(f"nur {bp.NETRON_ALLOWED_SUFFIX} Modelle sind erlaubt: "
                             f"{path_or_tag}")
        if not candidate.endswith(bp.NETRON_ALLOWED_SUFFIX):
            candidate = os.path.join(self.models_dir,
                                     f"{candidate}{bp.NETRON_ALLOWED_SUFFIX}")
        if not os.path.isabs(candidate):
            candidate = os.path.normpath(candidate)
        root = os.path.normpath(os.path.abspath(self.models_dir))
        absolute = os.path.normpath(os.path.abspath(candidate))
        if not absolute.startswith(root + os.sep) and absolute != root:
            raise ValueError(f"Pfad ausserhalb von {self.models_dir}: {path_or_tag}")
        if not absolute.endswith(bp.NETRON_ALLOWED_SUFFIX):
            raise ValueError("nur .onnx Modelle sind erlaubt")
        if not os.path.exists(absolute):
            raise FileNotFoundError(absolute)
        return absolute

    def models(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not os.path.isdir(self.models_dir):
            return rows
        for name in sorted(os.listdir(self.models_dir)):
            if not name.endswith(bp.NETRON_ALLOWED_SUFFIX):
                continue
            path = os.path.join(self.models_dir, name)
            rows.append({
                "version_tag": name[: -len(bp.NETRON_ALLOWED_SUFFIX)],
                "path": path,
                "size_bytes": os.path.getsize(path),
                "modified": int(os.path.getmtime(path)),
                "active": os.path.abspath(path) == os.path.abspath(self.model_path),
            })
        return rows

    # ------------------------------------------------------------- server --
    def start_server(self, initial_model_path: str = bp.NETRON_DEFAULT_MODEL
                     ) -> Dict[str, Any]:
        try:
            model = self.resolve_model(initial_model_path)
        except (ValueError, FileNotFoundError) as exc:
            self.last_error = str(exc)
            model = ""
        try:
            self.netron.start(model or None, address=(self.host, self.port),
                              browse=bp.NETRON_BROWSE)
        except NetronUnavailable as exc:
            self.last_error = str(exc)
            self.running = False
            return self.status()
        self.running = True
        self.model_path = model
        self.version_tag = self._tag_for(model)
        self.started_at = time.time()
        self.last_error = ""
        logger.info("netron gestartet auf %s:%s (%s)", self.host, self.port, model)
        return self.status()

    def load_model(self, model_path: str) -> bool:
        """Dynamischer Model-Switch — Netron laedt ohne Neustart neu."""
        try:
            model = self.resolve_model(model_path)
        except (ValueError, FileNotFoundError) as exc:
            self.last_error = str(exc)
            return False
        try:
            self.netron.start(model, address=(self.host, self.port),
                              browse=bp.NETRON_BROWSE)
        except NetronUnavailable as exc:
            self.last_error = str(exc)
            return False
        self.running = True
        self.model_path = model
        self.version_tag = self._tag_for(model)
        self.last_error = ""
        return True

    def stop(self) -> None:
        try:
            stop = getattr(self.netron, "stop", None)
            if callable(stop):
                stop()
        except NetronUnavailable:
            pass
        self.running = False

    @staticmethod
    def _tag_for(model_path: str) -> str:
        return os.path.basename(model_path)[: -len(bp.NETRON_ALLOWED_SUFFIX)] \
            if model_path else ""

    # ---------------------------------------------------------- telemetry --
    def status(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "available": self.available,
            "port": self.port,
            "bind": self.host,
            "url": self.url,
            "browse": bp.NETRON_BROWSE,
            "models_dir": self.models_dir,
            "default_model": bp.NETRON_DEFAULT_MODEL,
            "active_model": self.model_path,
            "version_tag": self.version_tag,
            "uptime_s": int(time.time() - self.started_at) if self.running else 0,
            "last_error": self.last_error,
            "models": self.models(),
        }


_SERVICE: Optional[NetronVisualizerService] = None


def get_netron_service(**kwargs: Any) -> NetronVisualizerService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = NetronVisualizerService(**kwargs)
    return _SERVICE


def set_netron_service(service: Optional[NetronVisualizerService]) -> None:
    global _SERVICE
    _SERVICE = service


if __name__ == "__main__":  # pragma: no cover - systemd entrypoint
    logging.basicConfig(level=logging.INFO)
    service = NetronVisualizerService(dev=bool(os.environ.get("SIGMA_NETRON_DEV")))
    service.start_server()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        service.stop()
