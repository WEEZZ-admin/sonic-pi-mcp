from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    sonic_pi_root: Path | None = None
    runtime_dir: Path | None = None
    startup_timeout: float = 60.0
    keepalive_interval: float = 4.0
    event_buffer_size: int = 5000
    default_collect_ms: int = 1500

    @classmethod
    def from_env(cls) -> "Config":
        root = os.environ.get("SONIC_PI_ROOT")
        runtime_dir = os.environ.get("SONIC_PI_MCP_RUNTIME_DIR")
        return cls(
            sonic_pi_root=Path(root).expanduser() if root else None,
            runtime_dir=Path(runtime_dir).expanduser() if runtime_dir else None,
            startup_timeout=_float_env("SONIC_PI_MCP_STARTUP_TIMEOUT", 60.0),
            keepalive_interval=_float_env("SONIC_PI_MCP_KEEPALIVE_INTERVAL", 4.0),
            event_buffer_size=_int_env("SONIC_PI_MCP_EVENT_BUFFER_SIZE", 5000),
            default_collect_ms=_int_env("SONIC_PI_MCP_DEFAULT_COLLECT_MS", 1500),
        )

    def resolved_runtime_dir(self) -> Path:
        if self.runtime_dir:
            return self.runtime_dir.expanduser()
        return default_runtime_dir()


def default_runtime_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "sonic-pi-mcp"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "sonic-pi-mcp"
    else:
        base = os.environ.get("XDG_CACHE_HOME")
        if base:
            return Path(base) / "sonic-pi-mcp"
    return Path.home() / ".cache" / "sonic-pi-mcp"


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
