from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from sonic_pi_mcp.config import Config
from sonic_pi_mcp.errors import SonicPiNotFoundError
from sonic_pi_mcp.sonic.locator import find_sonic_pi_root, ruby_executable
from sonic_pi_mcp.sonic.logs import LOG_SOURCES, read_logs, sonic_user_log_dir


def preflight_report(root_path: str | Path | None, config: Config) -> dict[str, Any]:
    """Check the local environment needed to boot Sonic Pi."""
    checks: list[dict[str, str]] = []
    root: Path | None = None

    try:
        root = find_sonic_pi_root(root_path, config.sonic_pi_root)
        _add_check(checks, "sonic_pi_root", "ok", str(root))
    except SonicPiNotFoundError as exc:
        _add_check(
            checks,
            "sonic_pi_root",
            "error",
            str(exc),
            "Set SONIC_PI_ROOT or pass root_path to sonic_start/sonic_preflight.",
        )

    if root is not None:
        _check_file(
            checks,
            "daemon_script",
            root / "app/server/ruby/bin/daemon.rb",
            "Install Sonic Pi or point SONIC_PI_ROOT at a full Sonic Pi root.",
        )
        _check_file(
            checks,
            "spider_script",
            root / "app/server/ruby/bin/spider-server.rb",
            "Install Sonic Pi or point SONIC_PI_ROOT at a full Sonic Pi root.",
        )
        _check_ruby(checks, root)

    runtime_dir = config.resolved_runtime_dir()
    _check_writable_dir(
        checks,
        "runtime_dir",
        runtime_dir,
        "Set SONIC_PI_MCP_RUNTIME_DIR to a writable directory.",
    )

    log_dir = sonic_user_log_dir()
    _check_writable_dir(
        checks,
        "sonic_pi_log_dir",
        log_dir,
        "Set SONIC_PI_HOME to a writable directory that Sonic Pi can use.",
    )

    _add_check(
        checks,
        "audio_backend",
        "warning",
        "Audio device access is verified only when Sonic Pi starts scsynth.",
        "If boot fails, check the scsynth log and the OS output device/permissions.",
    )
    _check_windows_processes(checks)

    return {
        "ok": not any(item["status"] == "error" for item in checks),
        "root_path": str(root) if root else None,
        "runtime_dir": str(runtime_dir),
        "log_dir": str(log_dir),
        "checks": checks,
        "recommendations": _recommendations(checks),
    }


def startup_failure_report(
    error: BaseException,
    *,
    root_path: str | Path | None,
    config: Config,
    resolved_root: Path | None = None,
    daemon_output: str | None = None,
    tail: int = 60,
) -> str:
    """Build a compact human-readable report for boot failures."""
    report = preflight_report(resolved_root or root_path, config)
    logs = read_logs(tail=tail)
    suggestions = _failure_suggestions(str(error), report, logs, daemon_output)

    lines = ["Sonic Pi startup diagnostics:", "", "Preflight checks:"]
    for item in report["checks"]:
        lines.append(f"- {item['name']}: {item['status']} - {item['detail']}")
        if item.get("fix") and item["status"] != "ok":
            lines.append(f"  fix: {item['fix']}")

    if daemon_output:
        lines.extend(["", "Recent daemon stdout:"])
        lines.extend(_format_tail(daemon_output, tail))

    lines.extend(["", "Recent Sonic Pi logs:"])
    for source in LOG_SOURCES:
        text = logs.get(source, "")
        if not text.strip():
            continue
        lines.append(f"[{source}]")
        lines.extend(_format_tail(text, tail))

    if suggestions:
        lines.extend(["", "Likely fixes:"])
        lines.extend(f"- {item}" for item in suggestions)

    return "\n".join(lines)


def _add_check(
    checks: list[dict[str, str]],
    name: str,
    status: str,
    detail: str,
    fix: str | None = None,
) -> None:
    item = {"name": name, "status": status, "detail": detail}
    if fix:
        item["fix"] = fix
    checks.append(item)


def _check_file(checks: list[dict[str, str]], name: str, path: Path, fix: str) -> None:
    if path.is_file():
        _add_check(checks, name, "ok", str(path))
    else:
        _add_check(checks, name, "error", f"Missing file: {path}", fix)


def _check_ruby(checks: list[dict[str, str]], root: Path) -> None:
    ruby = ruby_executable(root)
    ruby_path = Path(ruby)
    if ruby_path.is_file() or shutil.which(ruby):
        _add_check(checks, "ruby", "ok", ruby)
    else:
        _add_check(
            checks,
            "ruby",
            "error",
            f"Ruby executable not found: {ruby}",
            "Use a packaged Sonic Pi build or install Ruby on PATH.",
        )


def _check_writable_dir(
    checks: list[dict[str, str]],
    name: str,
    path: Path,
    fix: str,
) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".sonic-pi-mcp-write-test-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        if probe.read_text(encoding="utf-8") != "ok":
            raise OSError("write test content mismatch")
        try:
            probe.unlink()
        except OSError:
            pass
    except OSError as exc:
        _add_check(checks, name, "error", f"{path}: {exc}", fix)
        return
    _add_check(checks, name, "ok", str(path))


def _check_windows_processes(checks: list[dict[str, str]]) -> None:
    if sys.platform != "win32":
        return
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _add_check(
            checks,
            "windows_processes",
            "warning",
            f"Unable to inspect running Windows processes: {exc}",
        )
        return

    watched = {"sonic-pi.exe", "scsynth.exe", "tau.exe"}
    running = []
    for line in result.stdout.splitlines():
        if not line.startswith('"'):
            continue
        name = line.split('","', 1)[0].strip('"').lower()
        if name in watched:
            running.append(name)

    if running:
        _add_check(
            checks,
            "windows_processes",
            "warning",
            "Running Sonic Pi related processes: " + ", ".join(sorted(set(running))),
            "Stop old Sonic Pi/MCP sessions before upgrading, deleting files, or diagnosing port locks.",
        )
    else:
        _add_check(checks, "windows_processes", "ok", "No Sonic Pi related processes detected")


def _recommendations(checks: list[dict[str, str]]) -> list[str]:
    recommendations: list[str] = []
    for item in checks:
        if item["status"] == "error" and item.get("fix"):
            recommendations.append(item["fix"])
    return _dedupe(recommendations)


def _failure_suggestions(
    error_text: str,
    report: dict[str, Any],
    logs: dict[str, str],
    daemon_output: str | None,
) -> list[str]:
    combined = "\n".join([error_text, daemon_output or "", *logs.values()]).lower()
    suggestions = list(report.get("recommendations", []))

    if "permission" in combined or "access denied" in combined or "拒绝访问" in combined:
        suggestions.append(
            "Run the MCP server with permissions to start processes and write Sonic Pi logs, "
            "or set SONIC_PI_HOME/SONIC_PI_MCP_RUNTIME_DIR to writable directories."
        )
    if "scsynth" in combined or "audio" in combined or "asio" in combined or "jack" in combined:
        suggestions.append(
            "Check the selected audio output device and verify scsynth can start outside the MCP sandbox."
        )
    if "timed out waiting" in combined:
        suggestions.append(
            "Start Sonic Pi once manually, then retry; if the machine is slow, increase "
            "SONIC_PI_MCP_STARTUP_TIMEOUT."
        )
    if "ruby executable not found" in combined or "ruby: not found" in combined:
        suggestions.append("Use a packaged Sonic Pi build or install Ruby on PATH.")

    return _dedupe(suggestions)


def _format_tail(text: str, tail: int) -> list[str]:
    lines = text.splitlines()
    if tail > 0:
        lines = lines[-tail:]
    return [f"  {line}" for line in lines] or ["  <empty>"]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
