from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ConnectivityResult:
    checked_at: str
    online: bool
    latency_ms: float | None
    jitter_ms: float | None
    failed_probes: int
    dns_ms: float | None
    error: str | None = None
    packet_loss_percent: float | None = None
    packets_sent: int | None = None
    packets_lost: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SpeedResult:
    tested_at: str
    download_mbps: float | None
    upload_mbps: float | None
    duration_seconds: float
    target: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
