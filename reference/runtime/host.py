"""Deterministic app host (SPEC.md sections 5 and 8).

The host owns: event admission (idempotent, causally ordered), round
execution with a per-round event cap, state sealing, and effect emission
under budget. The app module is a stand-in for the future WASM module and
must expose the same ABI:

    init_state(seed: bytes) -> dict
    reduce(state: dict, event: AppEvent) -> (state, effects)
    render_state(state: dict) -> dict        # host-facing view

reduce is pure: no clock, no randomness, no I/O. The host enforces that by
convention here and by sandbox in the WASM runtime later.
"""

from __future__ import annotations

import hashlib
import json
import struct
from typing import List, Optional, Tuple

from meshkit.budgets import Meter
from meshkit.events import AppEvent, canonical_json
from meshkit.ordering import OrderBuffer
from runtime.manifest import Manifest


class AppInstance:
    def __init__(self, manifest: Manifest, core, peer_id: bytes, seed: bytes = b"",
                 clock=None):
        self.manifest = manifest
        self.core = core
        self.peer_id = peer_id
        self.seq = 0  # this node's per-app counter, contiguous from 1
        self.buffer = OrderBuffer()
        self.meter = Meter(budget=manifest.budget, clock=clock) if clock else Meter(budget=manifest.budget)
        self.state = core.init_state(seed)
        self.applied: List[Tuple[int, bytes, str]] = []  # (seq, sender, type)
        self.dropped: List[str] = []  # budget/policy rejections, for the log
        self._round_cap = 64

    # -- inbound -------------------------------------------------------------

    def admit(self, event: AppEvent) -> bool:
        """Accept an event from the mesh. False when duplicate or foreign app."""
        if event.app_id != self.manifest.app_id:
            return False
        event.sender = bytes(event.sender)
        if not self.buffer.push(event):
            return False  # duplicate event_id: idempotent drop
        return True

    def run_round(self, force_flush: bool = False) -> List[AppEvent]:
        """Apply deliverable events; return effects (outbound events).

        Effects are emitted as unsent AppEvents; the transport layer stamps
        event_id/lamport/packets. An event whose gap never closes is
        force-released once per round when force_flush is set (the host calls
        this after the 30 s grace window).
        """
        effects: List[AppEvent] = []
        batch = self.buffer.deliverable()
        if not batch and force_flush and self.buffer.held:
            batch = self.buffer.admit_late()
        if len(batch) > self._round_cap:
            for overflow in batch[self._round_cap :]:
                self.buffer.push(overflow)  # stays queued for the next round
            batch = batch[: self._round_cap]
        for event in batch:
            new_state, new_effects = self.core.reduce(self.state, event)
            self.state = new_state
            self.buffer.commit(event)
            self.applied.append((event.seq, event.sender, event.event_type))
            effects.extend(new_effects)
        return effects

    # -- outbound ------------------------------------------------------------

    def build_event(
        self,
        event_type: str,
        body: dict,
        recipient: Optional[bytes] = None,
        flags: int = 0,
    ) -> Tuple[Optional[AppEvent], Optional[str]]:
        """Stamp a local event and check it against the manifest budget."""
        if recipient is None and "mesh.broadcast" not in self.manifest.permissions:
            return None, "manifest lacks mesh.broadcast"
        if recipient is not None and "mesh.direct" not in self.manifest.permissions:
            return None, "manifest lacks mesh.direct"
        body_bytes = canonical_json(body) if body else b""
        self.seq += 1
        event = AppEvent(
            app_id=self.manifest.app_id,
            event_type=event_type,
            event_id=hashlib.sha256(
                self.peer_id + struct.pack(">Q", self.seq) + body_bytes
            ).digest()[:16],
            seq=self.seq,
            sender=self.peer_id,
            flags=flags,
            body=body_bytes,
        )
        try:
            encoded = event.encode()
        except ValueError as exc:
            return None, str(exc)
        violation = self.meter.check_send(len(encoded), recipient is None)
        if violation:
            self.dropped.append(f"{event_type}: {violation}")
            return None, violation
        self.meter.charge(len(encoded), recipient is None)
        event.state_root = self.state_root()
        return event, None

    # -- verification --------------------------------------------------------

    def state_root(self) -> bytes:
        return hashlib.sha256(canonical_json(_jsonable(self.state))).digest()

    def view(self) -> dict:
        return self.core.render_state(self.state)


def _jsonable(obj):
    """Bytes -> hex so canonical_json never chokes on binary fields."""
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj
