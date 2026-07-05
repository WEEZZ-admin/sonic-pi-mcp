from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from sonic_pi_mcp.config import Config
from sonic_pi_mcp.errors import (
    SonicPiBootError,
    SonicPiMcpError,
    SonicPiNotFoundError,
    SonicPiStateError,
)
from sonic_pi_mcp.sonic.daemon import SonicDaemon
from sonic_pi_mcp.sonic.diagnostics import preflight_report, startup_failure_report
from sonic_pi_mcp.sonic.events import EventBuffer
from sonic_pi_mcp.sonic.locator import find_sonic_pi_root
from sonic_pi_mcp.sonic.logs import read_logs
from sonic_pi_mcp.sonic.osc import OSCClient, OSCMessage, OSCUDPServer
from sonic_pi_mcp.sonic.protocol import (
    SPIDER_DELETE_RECORDING,
    SPIDER_PING,
    SPIDER_SAVE_RECORDING,
    SPIDER_SAVE_AND_RUN_BUFFER,
    SPIDER_START_RECORDING,
    SPIDER_STOP_ALL_JOBS,
    SPIDER_STOP_RECORDING,
    SonicPorts,
)


class SonicPiSession:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
        self.events = EventBuffer(self.config.event_buffer_size)
        self.root: Path | None = None
        self.ports: SonicPorts | None = None
        self.daemon: SonicDaemon | None = None
        self.osc_server: OSCUDPServer | None = None
        self.osc_client = OSCClient()
        self.state = "stopped"
        self.started_at: float | None = None
        self._lock = threading.RLock()

    def start(self, root_path: str | None = None, *, no_inputs: bool = False) -> dict[str, Any]:
        root: Path | None = None
        daemon: SonicDaemon | None = None
        with self._lock:
            if self.state in {"starting", "ready"}:
                return self.status()
            self.state = "starting"
            self.started_at = time.time()

        try:
            root = find_sonic_pi_root(root_path, self.config.sonic_pi_root)
            daemon = SonicDaemon(root, self.config)
            ports = daemon.start(no_inputs=no_inputs)
            server = OSCUDPServer(ports.gui_listen_to_spider, self._handle_osc)
            server.start()

            with self._lock:
                self.root = root
                self.daemon = daemon
                self.ports = ports
                self.osc_server = server

            self.events.append(
                "starting",
                text="Sonic Pi daemon started",
                payload=ports.to_dict(include_token=False),
            )
            self._wait_until_ready()

            with self._lock:
                self.state = "ready"
            return self.status()
        except Exception as exc:
            daemon_output = daemon.recent_output() if daemon else None
            self.shutdown()
            with self._lock:
                self.state = "error"
            raise self._startup_error_with_diagnostics(
                exc,
                root_path=root_path,
                resolved_root=root,
                daemon_output=daemon_output,
            ) from exc

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "root_path": str(self.root) if self.root else None,
                "ports": self.ports.to_dict(include_token=False) if self.ports else None,
                "started_at": self.started_at,
                "latest_event_seq": self.events.latest_seq,
            }

    def preflight(self, root_path: str | None = None) -> dict[str, Any]:
        return preflight_report(root_path, self.config)

    def run_code(
        self,
        code: str,
        *,
        buffer_name: str | None = None,
        collect_ms: int | None = None,
    ) -> dict[str, Any]:
        ports = self._require_ready()
        buffer = buffer_name or "mcp_buffer"
        submitted_code = self._prepare_submitted_code(code, buffer)
        since = self.events.latest_seq
        self.osc_client.send(
            ports.gui_send_to_spider,
            SPIDER_SAVE_AND_RUN_BUFFER,
            ports.token,
            buffer,
            submitted_code,
            buffer,
        )
        wait_s = (collect_ms if collect_ms is not None else self.config.default_collect_ms) / 1000.0
        events = self.events.collect_for(wait_s, since=since)
        return {
            "ok": True,
            "buffer_name": buffer,
            "submitted_via": "run_file" if submitted_code != code else "inline",
            "since": since,
            "events": [event.to_dict() for event in events],
        }

    def play_file(
        self,
        path: str,
        *,
        buffer_name: str | None = None,
        collect_ms: int | None = None,
    ) -> dict[str, Any]:
        file_path = self._resolve_code_path(path)
        buffer = buffer_name or file_path.stem or "mcp_file"
        result = self.run_code(
            f"run_file {self._sonic_path_literal(file_path)}",
            buffer_name=buffer,
            collect_ms=collect_ms,
        )
        result["file_path"] = str(file_path)
        result["submitted_via"] = "run_file"
        return result

    def start_recording(self, *, collect_ms: int | None = 500) -> dict[str, Any]:
        ports = self._require_ready()
        return self._send_spider_command(
            ports,
            SPIDER_START_RECORDING,
            collect_ms=collect_ms,
        )

    def stop_recording(self, *, collect_ms: int | None = 1000) -> dict[str, Any]:
        ports = self._require_ready()
        return self._send_spider_command(
            ports,
            SPIDER_STOP_RECORDING,
            collect_ms=collect_ms,
        )

    def save_recording(
        self,
        output_path: str,
        *,
        collect_ms: int | None = 1000,
        wait_timeout: float = 30.0,
    ) -> dict[str, Any]:
        ports = self._require_ready()
        output = self._resolve_output_path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        result = self._send_spider_command(
            ports,
            SPIDER_SAVE_RECORDING,
            self._sonic_path(output),
            collect_ms=collect_ms,
        )
        result.update(self._wait_for_output_file(output, timeout=wait_timeout))
        return result

    def delete_recording(self, *, collect_ms: int | None = 500) -> dict[str, Any]:
        ports = self._require_ready()
        return self._send_spider_command(
            ports,
            SPIDER_DELETE_RECORDING,
            collect_ms=collect_ms,
        )

    def record_file(
        self,
        path: str,
        output_path: str,
        *,
        duration_seconds: float,
        bit_depth: int = 24,
        buffer_name: str | None = None,
        root_path: str | None = None,
        no_inputs: bool = True,
        overwrite: bool = False,
        shutdown_after: bool = False,
        save_timeout: float = 30.0,
    ) -> dict[str, Any]:
        if duration_seconds <= 0:
            raise SonicPiStateError("duration_seconds must be greater than 0")
        if bit_depth not in {8, 16, 24, 32}:
            raise SonicPiStateError("bit_depth must be one of 8, 16, 24, or 32")

        code_path = self._resolve_code_path(path)
        output = self._resolve_output_path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            if not overwrite:
                raise SonicPiStateError(
                    f"Output file already exists: {output}. Pass overwrite=true to replace it."
                )
            output.unlink()

        started_here = self.status()["state"] != "ready"
        steps: list[dict[str, Any]] = []
        try:
            if started_here:
                steps.append(
                    {
                        "name": "start",
                        "result": self.start(root_path=root_path, no_inputs=no_inputs),
                    }
                )

            setup = self.run_code(
                f"set_recording_bit_depth! {bit_depth}",
                buffer_name="mcp_recording_setup",
                collect_ms=500,
            )
            steps.append({"name": "set_bit_depth", "result": setup})
            record_since = self.events.latest_seq
            steps.append({"name": "start_recording", "result": self.start_recording(collect_ms=0)})
            steps.append(
                {
                    "name": "play_file",
                    "result": self.play_file(
                        str(code_path),
                        buffer_name=buffer_name or code_path.stem or "mcp_recording",
                        collect_ms=0,
                    ),
                }
            )

            time.sleep(duration_seconds)
            steps.append({"name": "stop_recording", "result": self.stop_recording(collect_ms=1000)})
            steps.append({"name": "stop_all", "result": self.stop_all(collect_ms=800)})
            save = self.save_recording(
                str(output),
                collect_ms=1000,
                wait_timeout=save_timeout,
            )
            steps.append({"name": "save_recording", "result": save})

            result: dict[str, Any] = {
                "ok": bool(save.get("output_exists")),
                "file_path": str(code_path),
                "output_path": str(output),
                "output_exists": output.exists(),
                "output_size_bytes": output.stat().st_size if output.exists() else 0,
                "duration_seconds": duration_seconds,
                "bit_depth": bit_depth,
                "started_session": started_here,
                "since": record_since,
                "events": [event.to_dict() for event in self.events.snapshot(since=record_since)],
                "steps": steps,
            }
            if shutdown_after:
                result["shutdown"] = self.shutdown()
            return result
        except Exception:
            try:
                if self.status()["state"] == "ready":
                    self.stop_all(collect_ms=500)
            finally:
                if shutdown_after and self.status()["state"] != "stopped":
                    self.shutdown()
            raise

    def stop_all(self, *, collect_ms: int | None = 500) -> dict[str, Any]:
        ports = self._require_ready()
        since = self.events.latest_seq
        self.osc_client.send(ports.gui_send_to_spider, SPIDER_STOP_ALL_JOBS, ports.token)
        wait_s = max(0, collect_ms or 0) / 1000.0
        events = self.events.collect_for(wait_s, since=since)
        return {"ok": True, "since": since, "events": [event.to_dict() for event in events]}

    def send_cue(self, path: str, args: list[Any] | None = None) -> dict[str, Any]:
        ports = self._require_started()
        cue_path = path if path.startswith("/") else "/" + path
        self.osc_client.send(ports.osc_cues, cue_path, *(args or []))
        return {"ok": True, "path": cue_path, "args": args or []}

    def read_events(self, since: int | None = None, limit: int | None = 100) -> dict[str, Any]:
        events = self.events.snapshot(since=since, limit=limit)
        return {
            "latest_event_seq": self.events.latest_seq,
            "events": [event.to_dict() for event in events],
        }

    def get_logs(self, source: str | None = None, tail: int = 200) -> dict[str, str]:
        return read_logs(source=source, tail=tail)

    def shutdown(self) -> dict[str, Any]:
        with self._lock:
            server = self.osc_server
            daemon = self.daemon
            self.osc_server = None
            self.daemon = None
            self.ports = None
            self.root = None
            self.state = "stopped"
        if server:
            server.stop()
        if daemon:
            daemon.shutdown()
        self.events.append("stopped", text="Sonic Pi session stopped")
        return self.status()

    def _wait_until_ready(self) -> None:
        assert self.ports
        start_seq = self.events.latest_seq
        deadline = time.monotonic() + self.config.startup_timeout
        saw_ack = False
        while time.monotonic() < deadline:
            self.osc_client.send(
                self.ports.gui_send_to_spider,
                SPIDER_PING,
                self.ports.token,
                "mcp/hello",
            )
            if not saw_ack:
                event = self.events.wait_for(
                    lambda item: item.type == "ack",
                    timeout=0.25,
                    since=start_seq,
                )
                saw_ack = event is not None
            ready = self.events.wait_for(
                lambda item: item.type == "ready",
                timeout=0.25,
                since=start_seq,
            )
            if saw_ack and ready:
                return
            if saw_ack and self.ports.tau is not None:
                self.events.append(
                    "ready",
                    text="Spider acknowledged ping; legacy Tau runtime does not emit /spider/ready",
                )
                return
        raise SonicPiBootError("Timed out waiting for Spider /ack and /spider/ready")

    def _require_started(self) -> SonicPorts:
        with self._lock:
            if not self.ports:
                raise SonicPiStateError("Sonic Pi is not started")
            return self.ports

    def _require_ready(self) -> SonicPorts:
        with self._lock:
            if self.state != "ready" or not self.ports:
                raise SonicPiStateError(f"Sonic Pi is not ready; current state is {self.state}")
            return self.ports

    def _prepare_submitted_code(self, code: str, buffer_name: str) -> str:
        # Sonic Pi 4.6's Spider UDP receiver reads 16 KiB packets. Large buffers
        # must be loaded from disk to avoid silent truncation before decoding.
        if len(code.encode("utf-8")) < 12_000:
            return code
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in buffer_name)
        run_dir = self.config.resolved_runtime_dir() / "sonic_runs"
        run_dir.mkdir(parents=True, exist_ok=True)
        code_path = run_dir / f"{safe_name}.rb"
        code_path.write_text(code, encoding="utf-8")
        return f"run_file {self._sonic_path_literal(code_path)}"

    def _send_spider_command(
        self,
        ports: SonicPorts,
        address: str,
        *args: Any,
        collect_ms: int | None,
    ) -> dict[str, Any]:
        since = self.events.latest_seq
        self.osc_client.send(ports.gui_send_to_spider, address, ports.token, *args)
        wait_s = max(0, collect_ms or 0) / 1000.0
        events = self.events.collect_for(wait_s, since=since)
        return {
            "ok": True,
            "address": address,
            "since": since,
            "events": [event.to_dict() for event in events],
        }

    def _resolve_code_path(self, path: str) -> Path:
        code_path = Path(path).expanduser()
        if not code_path.is_absolute():
            code_path = Path.cwd() / code_path
        code_path = code_path.resolve()
        if not code_path.is_file():
            raise SonicPiStateError(f"Sonic Pi code file not found: {code_path}")
        return code_path

    def _resolve_output_path(self, path: str) -> Path:
        output = Path(path).expanduser()
        if not output.is_absolute():
            output = Path.cwd() / output
        return output.resolve()

    def _wait_for_output_file(self, path: Path, *, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, timeout)
        last_size = -1
        stable_since: float | None = None
        while time.monotonic() <= deadline:
            if path.is_file():
                size = path.stat().st_size
                now = time.monotonic()
                if size > 0 and size == last_size:
                    if stable_since is None:
                        stable_since = now
                    elif now - stable_since >= 0.5:
                        return {
                            "output_path": str(path),
                            "output_exists": True,
                            "output_size_bytes": size,
                        }
                else:
                    stable_since = None
                    last_size = size
            time.sleep(0.1)
        return {
            "output_path": str(path),
            "output_exists": path.is_file(),
            "output_size_bytes": path.stat().st_size if path.is_file() else 0,
            "warning": f"Timed out waiting for recording file to settle: {path}",
        }

    def _startup_error_with_diagnostics(
        self,
        exc: Exception,
        *,
        root_path: str | None,
        resolved_root: Path | None,
        daemon_output: str | None,
    ) -> SonicPiMcpError:
        report = startup_failure_report(
            exc,
            root_path=root_path,
            config=self.config,
            resolved_root=resolved_root,
            daemon_output=daemon_output,
        )
        message = f"{exc}\n\n{report}"
        if isinstance(exc, SonicPiNotFoundError):
            return SonicPiNotFoundError(message)
        if isinstance(exc, SonicPiStateError):
            return SonicPiStateError(message)
        return SonicPiBootError(message)

    @staticmethod
    def _sonic_path_literal(path: Path) -> str:
        escaped_path = SonicPiSession._sonic_path(path).replace('"', '\\"')
        return f'"{escaped_path}"'

    @staticmethod
    def _sonic_path(path: Path) -> str:
        return str(path).replace("\\", "/")

    def _handle_osc(self, message: OSCMessage, _addr: tuple[str, int]) -> None:
        address = message.address
        args = list(message.args)
        if address == "/ack":
            self.events.append("ack", text=str(args[0]) if args else None)
        elif address == "/spider/ready":
            self.events.append("ready", text="Spider ready")
        elif address == "/log/info":
            self.events.append(
                "log",
                text=str(args[1]) if len(args) > 1 else "",
                payload={"style": args[0] if args else 0},
            )
        elif address == "/log/multi_message":
            self._handle_multi_message(args)
        elif address == "/incoming/osc":
            self.events.append(
                "cue",
                text=str(args[2]) if len(args) > 2 else None,
                payload={
                    "time": args[0] if len(args) > 0 else None,
                    "id": args[1] if len(args) > 1 else None,
                    "address": args[2] if len(args) > 2 else None,
                    "args": args[3] if len(args) > 3 else None,
                },
            )
        elif address == "/error":
            self.events.append(
                "runtime_error",
                text=str(args[1]) if len(args) > 1 else "",
                job_id=int(args[0]) if args else None,
                line=int(args[3]) if len(args) > 3 else None,
                payload={"backtrace": args[2] if len(args) > 2 else ""},
            )
        elif address == "/syntax_error":
            self.events.append(
                "syntax_error",
                text=str(args[1]) if len(args) > 1 else "",
                job_id=int(args[0]) if args else None,
                line=int(args[3]) if len(args) > 3 else None,
                payload={
                    "error_line": args[2] if len(args) > 2 else "",
                    "line_num": args[4] if len(args) > 4 else "",
                },
            )
        elif address == "/runs/all-completed":
            self.events.append("all_completed", text="All runs completed")
        elif address == "/exited":
            self.events.append("exited", text="Spider exited")
            with self._lock:
                self.state = "stopped"
        elif address == "/exited-with-boot-error":
            self.events.append("startup_error", text=str(args[0]) if args else "")
            with self._lock:
                self.state = "error"
        elif address == "/version":
            self.events.append("version", text=str(args[0]) if args else "", payload={"args": args})
        elif address.startswith("/supersonic/"):
            self.events.append("supersonic", text=address, payload={"args": args})
        elif address in {"/midi/out-ports", "/midi/in-ports", "/gamepad/devices-list"}:
            self.events.append("device", text=address, payload={"args": args})
        elif address in {"/link-num-peers", "/link-bpm"}:
            self.events.append("link", text=address, payload={"args": args})
        else:
            self.events.append("osc", text=address, payload={"args": args})

    def _handle_multi_message(self, args: list[Any]) -> None:
        job_id = int(args[0]) if args else None
        thread_name = str(args[1]) if len(args) > 1 else ""
        runtime = str(args[2]) if len(args) > 2 else ""
        count = int(args[3]) if len(args) > 3 else 0
        messages = []
        cursor = 4
        for _ in range(count):
            if cursor + 1 >= len(args):
                break
            messages.append({"style": args[cursor], "text": args[cursor + 1]})
            cursor += 2
        text = "\n".join(str(item["text"]) for item in messages)
        self.events.append(
            "multi_log",
            text=text,
            job_id=job_id,
            payload={"thread_name": thread_name, "runtime": runtime, "messages": messages},
        )
