"""Per-app resource metering (SPEC.md section 6).

Two enforcement points, matching the spec:
- decode time: hard caps in wire.py (inherited PacketPayloadLimits)
- host time: token-bucket metering here, per app, before transmit
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

# Platform maxima — a manifest above these fails install.
MAX_BYTES_PER_SECOND = 1024
MAX_BURST_BYTES = 4096
MAX_BROADCAST_EVENTS_PER_SECOND = 4
MAX_EVENT_BYTES = 200
MAX_STATE_BYTES = 65536


@dataclass
class Budget:
    bytes_per_second: int = 300
    burst_bytes: int = 1024
    broadcast_events_per_second: int = 2
    max_event_bytes: int = 200
    state_bytes: int = 16384

    @classmethod
    def from_manifest(cls, data: dict) -> "Budget":
        return cls(
            bytes_per_second=int(data.get("bytesPerSecond", 300)),
            burst_bytes=int(data.get("burstBytes", 1024)),
            broadcast_events_per_second=int(data.get("broadcastEventsPerSecond", 2)),
            max_event_bytes=int(data.get("maxEventBytes", 200)),
            state_bytes=int(data.get("stateBytes", 16384)),
        )

    def validate(self) -> Optional[str]:
        if self.bytes_per_second > MAX_BYTES_PER_SECOND:
            return "bytesPerSecond over platform maximum"
        if self.burst_bytes > MAX_BURST_BYTES:
            return "burstBytes over platform maximum"
        if self.broadcast_events_per_second > MAX_BROADCAST_EVENTS_PER_SECOND:
            return "broadcastEventsPerSecond over platform maximum"
        if self.max_event_bytes > MAX_EVENT_BYTES:
            return "maxEventBytes over platform maximum"
        if self.state_bytes > MAX_STATE_BYTES:
            return "stateBytes over platform maximum"
        return None


@dataclass
class Meter:
    """Token bucket, clock injectable for tests."""

    budget: Budget
    clock: object = field(default_factory=lambda: time.monotonic)

    def __post_init__(self) -> None:
        self._bytes = float(self.budget.burst_bytes)
        self._bcasts = float(self.budget.broadcast_events_per_second)
        self._last = self.clock()

    def _refill(self) -> None:
        now = self.clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._bytes = min(
            float(self.budget.burst_bytes),
            self._bytes + elapsed * self.budget.bytes_per_second,
        )
        self._bcasts = min(
            float(self.budget.broadcast_events_per_second),
            self._bcasts + elapsed * self.budget.broadcast_events_per_second,
        )

    def check_send(self, nbytes: int, is_broadcast: bool) -> Optional[str]:
        """Return None when the send is allowed, otherwise the violation."""
        if nbytes > self.budget.max_event_bytes:
            return f"event {nbytes}B over maxEventBytes {self.budget.max_event_bytes}B"
        self._refill()
        if nbytes > self._bytes:
            return "byte budget exhausted"
        if is_broadcast and self._bcasts < 1.0:
            return "broadcast budget exhausted"
        return None

    def charge(self, nbytes: int, is_broadcast: bool) -> None:
        self._bytes -= nbytes
        if is_broadcast:
            self._bcasts -= 1.0
