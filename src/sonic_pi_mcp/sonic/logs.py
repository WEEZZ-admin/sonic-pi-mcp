from __future__ import annotations

import os
from pathlib import Path


LOG_SOURCES = {
    "gui": "gui.log",
    "spider": "spider.log",
    "daemon": "daemon.log",
    "supersonic": "supersonic.log",
    "scsynth": "scsynth.log",
}


def sonic_user_log_dir() -> Path:
    root = os.environ.get("SONIC_PI_HOME")
    if root:
        return Path(root).expanduser() / ".sonic-pi" / "log"
    return Path.home() / ".sonic-pi" / "log"


def read_logs(source: str | None = None, tail: int = 200) -> dict[str, str]:
    log_dir = sonic_user_log_dir()
    sources = [source.lower()] if source else list(LOG_SOURCES)
    result: dict[str, str] = {}
    for item in sources:
        filename = LOG_SOURCES.get(item)
        if not filename:
            result[item] = f"Unknown log source: {item}"
            continue
        path = log_dir / filename
        if not path.is_file():
            result[item] = f"Log file not found: {path}"
            continue
        result[item] = "\n".join(_tail_lines(path, tail))
    return result


def _tail_lines(path: Path, tail: int) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [f"Unable to read {path}: {exc}"]
    if tail <= 0:
        return lines
    return lines[-tail:]

