"""Causal ordering for deterministic app state (SPEC.md section 5).

Events apply in (seq, senderPeerId) order per app. The apply key is
intrinsic to each event, so every replica that observes the same event set
applies in the same order and converges. The buffer holds events whose
per-sender seq runs ahead of its predecessor and flushes when the gap
closes, or after a grace window (latecomers apply in order, never dropped
silently).
"""

from __future__ import annotations

from typing import List, Tuple


class OrderBuffer:
    """Per-app reordering buffer keyed by (seq, sender).

    Idempotent: an event_id already delivered is dropped here, before the
    app's reduce ever sees it.
    """

    def __init__(self, grace_events: int = 64):
        self._pending: List[Tuple[int, bytes, object]] = []  # (seq, sender, event)
        self._seen: set = set()  # event ids ever pushed (pending or applied)
        self._next_per_sender: dict = {}
        self.grace_events = grace_events

    def deliverable(self) -> list:
        """Pop all events now apply-able in (seq, sender) order."""
        ready = []
        while True:
            self._pending.sort(key=lambda item: (item[0], item[1]))
            progressed = False
            for index, (seq, sender, event) in enumerate(self._pending):
                expected = self._next_per_sender.get(sender)
                # A sender's first observed event establishes its baseline;
                # later events must not go backwards.
                if expected is None or seq <= expected:
                    ready.append(event)
                    self._next_per_sender[sender] = max(expected or 0, seq) + 1
                    self._pending.pop(index)
                    progressed = True
                    break
            if not progressed:
                break
        return ready

    def admit_late(self) -> list:
        """Force-flush the oldest held event when a gap will not close.

        Called by the host after the grace budget is exhausted. The event
        with the smallest (seq, sender) is released and the sender's
        expectation is advanced past it.
        """
        if not self._pending:
            return []
        self._pending.sort(key=lambda item: (item[0], item[1]))
        seq, sender, event = self._pending.pop(0)
        expected = self._next_per_sender.get(sender)
        self._next_per_sender[sender] = max(expected or 0, seq) + 1
        return [event]

    def push(self, event) -> bool:
        """Returns False when the event is a duplicate.

        Tracks every id ever pushed — pending or applied — so a duplicate
        that arrives between push and commit cannot double-apply.
        """
        key = bytes(event.event_id)
        if key in self._seen:
            return False
        self._seen.add(key)
        self._pending.append((event.seq, bytes(event.sender), event))
        return True

    def commit(self, event) -> None:
        """Bookkeeping hook for the host; dedup lives in push."""
        self._seen.add(bytes(event.event_id))

    @property
    def held(self) -> int:
        return len(self._pending)
