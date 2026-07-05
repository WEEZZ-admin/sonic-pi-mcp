from __future__ import annotations

from typing import Any

from sonic_pi_mcp.config import Config
from sonic_pi_mcp.docs.search import SonicDocs
from sonic_pi_mcp.errors import SonicPiNotFoundError, SonicPiStateError
from sonic_pi_mcp.sonic.locator import find_sonic_pi_root
from sonic_pi_mcp.sonic.session import SonicPiSession

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - depends on runtime installation
    raise RuntimeError("The 'mcp' package is required to run sonic-pi-mcp") from exc


mcp = FastMCP("sonic-pi-mcp")
session = SonicPiSession(Config.from_env())


@mcp.tool()
def sonic_start(root_path: str | None = None, no_inputs: bool = False) -> dict[str, Any]:
    """Start a managed local Sonic Pi runtime."""
    return session.start(root_path=root_path, no_inputs=no_inputs)


@mcp.tool()
def sonic_status() -> dict[str, Any]:
    """Return current Sonic Pi session status."""
    return session.status()


@mcp.tool()
def sonic_preflight(root_path: str | None = None) -> dict[str, Any]:
    """Check whether the local environment looks ready to start Sonic Pi."""
    return session.preflight(root_path=root_path)


@mcp.tool()
def sonic_run_code(
    code: str,
    buffer_name: str | None = None,
    collect_ms: int | None = None,
) -> dict[str, Any]:
    """Run Sonic Pi code in the managed runtime and return newly collected events."""
    return session.run_code(code, buffer_name=buffer_name, collect_ms=collect_ms)


@mcp.tool()
def sonic_play_file(
    path: str,
    buffer_name: str | None = None,
    collect_ms: int | None = None,
) -> dict[str, Any]:
    """Run a local Sonic Pi .rb file in the managed runtime."""
    return session.play_file(path, buffer_name=buffer_name, collect_ms=collect_ms)


@mcp.tool()
def sonic_stop(collect_ms: int | None = 500) -> dict[str, Any]:
    """Stop all running Sonic Pi jobs."""
    return session.stop_all(collect_ms=collect_ms)


@mcp.tool()
def sonic_shutdown() -> dict[str, Any]:
    """Shut down the managed Sonic Pi runtime."""
    return session.shutdown()


@mcp.tool()
def sonic_read_events(since: int | None = None, limit: int | None = 100) -> dict[str, Any]:
    """Read buffered Sonic Pi events after an optional sequence number."""
    return session.read_events(since=since, limit=limit)


@mcp.tool()
def sonic_get_logs(source: str | None = None, tail: int = 200) -> dict[str, str]:
    """Read Sonic Pi log tails. Source can be gui, spider, daemon, supersonic, or scsynth."""
    return session.get_logs(source=source, tail=tail)


@mcp.tool()
def sonic_send_cue(path: str, args: list[Any] | None = None) -> dict[str, Any]:
    """Send an external OSC cue into Sonic Pi's cue server."""
    return session.send_cue(path, args=args)


@mcp.tool()
def sonic_search_docs(query: str, limit: int = 10, root_path: str | None = None) -> list[dict[str, str]]:
    """Search local Sonic Pi markdown documentation and snippets."""
    docs = _docs(root_path)
    return docs.search_docs(query, limit=limit)


@mcp.tool()
def sonic_list_samples(limit: int | None = None, root_path: str | None = None) -> list[str]:
    """List bundled Sonic Pi sample names."""
    return _docs(root_path).list_samples(limit=limit)


@mcp.tool()
def sonic_list_synths(limit: int | None = None, root_path: str | None = None) -> list[str]:
    """List bundled Sonic Pi synth names."""
    return _docs(root_path).list_synths(limit=limit)


@mcp.tool()
def sonic_list_fx(limit: int | None = None, root_path: str | None = None) -> list[str]:
    """List bundled Sonic Pi FX names."""
    return _docs(root_path).list_fx(limit=limit)


def _docs(root_path: str | None = None) -> SonicDocs:
    if root_path:
        root = find_sonic_pi_root(root_path, session.config.sonic_pi_root)
        return SonicDocs(root)
    status = session.status()
    if status.get("root_path"):
        return SonicDocs(find_sonic_pi_root(status["root_path"], session.config.sonic_pi_root))
    try:
        return SonicDocs(find_sonic_pi_root(None, session.config.sonic_pi_root))
    except SonicPiNotFoundError as exc:
        raise SonicPiStateError(
            "Sonic Pi root is unknown. Start a session first, set SONIC_PI_ROOT, "
            "or pass root_path."
        ) from exc


def main() -> None:
    mcp.run(transport="stdio")
