"""Relay policy and dedup — ported from RelayController.swift and the
BLEReceivePipeline relay decision.

Unknown wire types (including meshkit 0x2D appEvent) take the broadcast
branch: degree-based TTL clamps, wide jitter, fanout subsetting at the
radio layer (not modeled here).
"""

from __future__ import annotations

import random
import time
from collections import OrderedDict
from dataclasses import dataclass

from .wire import MessageType

DEFAULT_TTL = 7
HIGH_DEGREE_THRESHOLD = 6  # TransportConfig.bleHighDegreeThreshold


@dataclass
class RelayDecision:
    should_relay: bool
    new_ttl: int
    delay_ms: int


def decide_relay(
    ttl: int,
    sender_is_self: bool,
    recipient_is_self: bool,
    is_directed_encrypted: bool,
    is_fragment: bool,
    is_handshake: bool,
    is_announce: bool,
    is_request_sync: bool,
    degree: int,
    rng: random.Random,
) -> RelayDecision:
    """Deterministic port of RelayController.decide for the type classes an
    appEvent platform exercises. Broadcast/unknown types hit the final branch.
    """
    ttl_cap = min(ttl, DEFAULT_TTL)

    if is_request_sync:
        return RelayDecision(False, ttl_cap, 0)
    if ttl_cap <= 1 or sender_is_self or recipient_is_self:
        return RelayDecision(False, ttl_cap, 0)
    if is_handshake or is_directed_encrypted or is_fragment:
        new_ttl = ttl_cap - 1
        delay = rng.randint(10, 35) if is_handshake else rng.randint(20, 60)
        return RelayDecision(True, new_ttl, delay)

    # Broadcast policy: this is where unknown types (0x2D) land.
    if degree >= HIGH_DEGREE_THRESHOLD:
        ttl_limit = max(2, min(ttl_cap, 5))
    elif degree <= 2:
        ttl_limit = ttl_cap
    else:
        preferred = 7 if is_announce else 6
        ttl_limit = max(2, min(ttl_cap, preferred))
    new_ttl = ttl_limit - 1

    if degree <= 2:
        delay = rng.randint(10, 40)
    elif degree <= 5:
        delay = rng.randint(60, 150)
    elif degree <= 9:
        delay = rng.randint(80, 180)
    else:
        delay = rng.randint(100, 220)
    return RelayDecision(True, new_ttl, delay)


def relay_decision_for_packet(pkt, local_sender: bytes, degree: int, rng: random.Random) -> RelayDecision:
    """Map a decoded Packet onto decide_relay, matching
    BLEReceivePipeline.relayDecision for an unknown 0x2D type: every class
    flag is false, so the packet relays under broadcast policy.
    """
    return decide_relay(
        ttl=pkt.ttl,
        sender_is_self=pkt.sender_id == local_sender,
        recipient_is_self=pkt.recipient_id == local_sender,
        is_directed_encrypted=False,
        is_fragment=pkt.type == MessageType.FRAGMENT,
        is_handshake=pkt.type == MessageType.NOISE_HANDSHAKE,
        is_announce=pkt.type == MessageType.ANNOUNCE,
        is_request_sync=pkt.type == MessageType.REQUEST_SYNC,
        degree=degree,
        rng=rng,
    )


class DedupCache:
    """LRU seen-set, 1000 entries, 5-minute expiry (mesh seen-set)."""

    def __init__(self, capacity: int = 1000, ttl_seconds: float = 300.0, clock=time.monotonic):
        self.capacity = capacity
        self.ttl = ttl_seconds
        self.clock = clock
        self._entries: OrderedDict[str, float] = OrderedDict()

    def seen(self, message_id: str) -> bool:
        """Record and report: True when the id was already present."""
        now = self.clock()
        while self._entries:
            oldest_key = next(iter(self._entries))
            if now - self._entries[oldest_key] > self.ttl:
                self._entries.popitem(last=False)
            else:
                break
        if message_id in self._entries:
            self._entries.move_to_end(message_id)
            return True
        self._entries[message_id] = now
        if len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
        return False
