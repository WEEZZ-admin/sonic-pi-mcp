from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SonicEvent:
    seq: int
    ts: float
    type: str
    text: str | None = None
    job_id: int | None = None
    line: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "text": self.text,
            "job_id": self.job_id,
            "line": self.line,
            "payload": self.payload,
        }


def event_now(
    seq: int,
    event_type: str,
    *,
    text: str | None = None,
    job_id: int | None = None,
    line: int | None = None,
    payload: dict[str, Any] | None = None,
) -> SonicEvent:
    return SonicEvent(
        seq=seq,
        ts=time.time(),
        type=event_type,
        text=text,
        job_id=job_id,
        line=line,
        payload=payload or {},
    )

