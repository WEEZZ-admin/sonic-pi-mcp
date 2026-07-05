from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from sonic_pi_mcp.config import Config
from sonic_pi_mcp.errors import SonicPiBootError, SonicPiStateError
from sonic_pi_mcp.sonic.daemon import SonicDaemon
from sonic_pi_mcp.sonic.events import EventBuffer
from sonic_pi_mcp.sonic.locator import find_sonic_pi_root
from sonic_pi_mcp.sonic.logs import read_logs
from sonic_pi_mcp.sonic.osc import OSCClient, OSCMessage, OSCUDPServer
from sonic_pi_mcp.sonic.protocol import (
    SPIDER_PING,
    SPIDER_SAVE_AND_RUN_BUFFER,
    SPIDER_STOP_ALL_JOBS,
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
        except Exception:
            self.shutdown()
            with self._lock:
                self.state = "error"
            raise

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "root_path": str(self.root) if self.root else None,
                "ports": self.ports.to_dict(include_token=False) if self.ports else None,
                "started_at": self.started_at,
                "latest_event_seq": self.events.latest_seq,
            }

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
        sonic_path = str(code_path).replace("\\", "/")
        escaped_path = sonic_path.replace("\\", "\\\\").replace('"', '\\"')
        return f'run_file "{escaped_path}"'

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
