from __future__ import annotations

import time
from collections import deque
from threading import Condition
from typing import Callable

from sonic_pi_mcp.models import SonicEvent, event_now


class EventBuffer:
    def __init__(self, maxlen: int = 5000) -> None:
        self._events: deque[SonicEvent] = deque(maxlen=maxlen)
        self._next_seq = 1
        self._condition = Condition()

    @property
    def latest_seq(self) -> int:
        with self._condition:
            return self._next_seq - 1

    def append(
        self,
        event_type: str,
        *,
        text: str | None = None,
        job_id: int | None = None,
        line: int | None = None,
        payload: dict | None = None,
    ) -> SonicEvent:
        with self._condition:
            event = event_now(
                self._next_seq,
                event_type,
                text=text,
                job_id=job_id,
                line=line,
                payload=payload,
            )
            self._events.append(event)
            self._next_seq += 1
            self._condition.notify_all()
            return event

    def snapshot(self, since: int | None = None, limit: int | None = None) -> list[SonicEvent]:
        with self._condition:
            events = list(self._events)
        if since is not None:
            events = [event for event in events if event.seq > since]
        if limit is not None and limit >= 0:
            events = events[-limit:]
        return events

    def wait_for(
        self,
        predicate: Callable[[SonicEvent], bool],
        timeout: float,
        *,
        since: int | None = None,
    ) -> SonicEvent | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for event in self._events:
                    if since is not None and event.seq <= since:
                        continue
                    if predicate(event):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def collect_for(self, seconds: float, *, since: int | None = None) -> list[SonicEvent]:
        deadline = time.monotonic() + max(0.0, seconds)
        with self._condition:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
        return self.snapshot(since=since)

