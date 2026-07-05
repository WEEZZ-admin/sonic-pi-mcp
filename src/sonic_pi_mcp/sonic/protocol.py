from __future__ import annotations

import re
from dataclasses import dataclass

from sonic_pi_mcp.errors import SonicPiProtocolError


@dataclass(frozen=True)
class SonicPorts:
    daemon: int
    gui_listen_to_spider: int
    gui_send_to_spider: int
    scsynth: int
    osc_cues: int
    token: int
    tau: int | None = None
    tau_phx: int | None = None

    def to_dict(self, *, include_token: bool = True) -> dict[str, int | str]:
        values: dict[str, int | str] = {
            "daemon": self.daemon,
            "gui_listen_to_spider": self.gui_listen_to_spider,
            "gui_send_to_spider": self.gui_send_to_spider,
            "scsynth": self.scsynth,
            "osc_cues": self.osc_cues,
        }
        if self.tau is not None:
            values["tau"] = self.tau
        if self.tau_phx is not None:
            values["tau_phx"] = self.tau_phx
        if include_token:
            values["token"] = self.token
        return values


PORT_LINE_RE = re.compile(r"^\s*(-?\d+(?:\s+-?\d+){5,7})\s*$")


def parse_daemon_ports(text: str) -> SonicPorts:
    """Parse the daemon stdout line containing ports and auth token."""
    for line in text.splitlines():
        match = PORT_LINE_RE.match(line)
        if not match:
            continue
        values = [int(value) for value in match.group(1).split()]
        if len(values) == 6:
            daemon, gui_listen, gui_send, scsynth, osc_cues, token = values
            tau = None
            tau_phx = None
        elif len(values) == 8:
            daemon, gui_listen, gui_send, scsynth, osc_cues, tau, tau_phx, token = values
        else:
            continue
        for name, value in {
            "daemon": daemon,
            "gui_listen_to_spider": gui_listen,
            "gui_send_to_spider": gui_send,
            "scsynth": scsynth,
            "osc_cues": osc_cues,
            "tau": tau,
            "tau_phx": tau_phx,
        }.items():
            if value is None:
                continue
            if value <= 0 or value > 65535:
                raise SonicPiProtocolError(f"Invalid {name} port from daemon: {value}")
        return SonicPorts(
            daemon=daemon,
            gui_listen_to_spider=gui_listen,
            gui_send_to_spider=gui_send,
            scsynth=scsynth,
            osc_cues=osc_cues,
            token=token,
            tau=tau,
            tau_phx=tau_phx,
        )
    raise SonicPiProtocolError("Could not find daemon port/token line in stdout")


SPIDER_PING = "/ping"
SPIDER_SAVE_AND_RUN_BUFFER = "/save-and-run-buffer"
SPIDER_STOP_ALL_JOBS = "/stop-all-jobs"
DAEMON_KEEP_ALIVE = "/daemon/keep-alive"
DAEMON_EXIT = "/daemon/exit"
