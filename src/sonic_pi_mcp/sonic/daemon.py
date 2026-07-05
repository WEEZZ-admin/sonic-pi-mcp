from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from sonic_pi_mcp.config import Config
from sonic_pi_mcp.errors import SonicPiBootError, SonicPiProtocolError
from sonic_pi_mcp.sonic.locator import ruby_executable
from sonic_pi_mcp.sonic.osc import OSCClient
from sonic_pi_mcp.sonic.protocol import DAEMON_EXIT, DAEMON_KEEP_ALIVE, SonicPorts, parse_daemon_ports


class SonicDaemon:
    def __init__(self, root: Path, config: Config) -> None:
        self.root = root
        self.config = config
        self.ports: SonicPorts | None = None
        self.process: subprocess.Popen[str] | None = None
        self._stdout_queue: queue.Queue[str] = queue.Queue()
        self._stdout_lines: deque[str] = deque(maxlen=500)
        self._stdout_lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._keepalive_thread: threading.Thread | None = None
        self._stop_keepalive = threading.Event()
        self._client = OSCClient()

    def start(self, *, no_inputs: bool = False) -> SonicPorts:
        if self.process:
            raise SonicPiBootError("Sonic Pi daemon is already started")

        args = [
            ruby_executable(self.root),
            str(self.root / "app/server/ruby/bin/daemon.rb"),
        ]
        if no_inputs:
            args.append("--no-scsynth-inputs")

        try:
            self.process = subprocess.Popen(
                args,
                cwd=str(self.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise SonicPiBootError(f"Unable to start Sonic Pi daemon: {exc}") from exc

        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            name="sonic-pi-daemon-stdout",
            daemon=True,
        )
        self._stdout_thread.start()

        self.ports = self._wait_for_ports(self.config.startup_timeout)
        self._start_keepalive()
        return self.ports

    def shutdown(self) -> None:
        self._stop_keepalive.set()
        if self.ports:
            try:
                self._client.send(self.ports.daemon, DAEMON_EXIT, self.ports.token)
            except OSError:
                pass
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_thread.join(timeout=2)
        if self.process and self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        self._client.close()
        self.process = None

    def recent_output(self, tail: int = 80) -> str:
        with self._stdout_lock:
            lines = list(self._stdout_lines)
        if tail > 0:
            lines = lines[-tail:]
        return "".join(lines)

    def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            with self._stdout_lock:
                self._stdout_lines.append(line)
            self._stdout_queue.put(line)

    def _wait_for_ports(self, timeout: float) -> SonicPorts:
        deadline = time.monotonic() + timeout
        captured: list[str] = []
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                output = "".join(captured)
                raise SonicPiBootError(
                    f"Sonic Pi daemon exited before reporting ports. Output:\n{output}"
                )
            try:
                line = self._stdout_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            captured.append(line)
            try:
                return parse_daemon_ports("".join(captured))
            except SonicPiProtocolError:
                continue
        output = "".join(captured)
        self.shutdown()
        raise SonicPiBootError(f"Timed out waiting for Sonic Pi daemon ports. Output:\n{output}")

    def _start_keepalive(self) -> None:
        self._stop_keepalive.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            name="sonic-pi-daemon-keepalive",
            daemon=True,
        )
        self._keepalive_thread.start()

    def _keepalive_loop(self) -> None:
        assert self.ports
        while not self._stop_keepalive.wait(self.config.keepalive_interval):
            try:
                self._client.send(self.ports.daemon, DAEMON_KEEP_ALIVE, self.ports.token)
            except OSError:
                return
