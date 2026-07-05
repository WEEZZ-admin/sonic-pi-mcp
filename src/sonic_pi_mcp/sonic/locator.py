from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from sonic_pi_mcp.errors import SonicPiNotFoundError


def find_sonic_pi_root(explicit: str | Path | None = None, env_root: Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if env_root:
        candidates.append(env_root.expanduser())
    env_value = os.environ.get("SONIC_PI_ROOT")
    if env_value:
        candidates.append(Path(env_value).expanduser())

    cwd = Path.cwd()
    candidates.extend([cwd, *cwd.parents])
    candidates.extend(_platform_candidates())

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if is_sonic_pi_root(candidate):
            return candidate

    searched = "\n".join(f"  - {path}" for path in seen)
    raise SonicPiNotFoundError("Could not find Sonic Pi root. Searched:\n" + searched)


def is_sonic_pi_root(path: Path) -> bool:
    return (
        (path / "app/server/ruby/bin/daemon.rb").is_file()
        and (path / "app/server/ruby/bin/spider-server.rb").is_file()
    )


def ruby_executable(root: Path) -> str:
    native = root / "app/server/native/ruby/bin/ruby.exe"
    if sys.platform != "win32":
        native = root / "app/server/native/ruby/bin/ruby"
    if native.is_file():
        return str(native)
    found = shutil.which("ruby")
    return found or "ruby"


def _platform_candidates() -> list[Path]:
    if sys.platform == "win32":
        candidates = []
        for name in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(name)
            if base:
                candidates.append(Path(base) / "Sonic Pi")
        return candidates
    return []
