"""Relay policy, dedup cache, and budget tests (SPEC conformance)."""

import random
import unittest

from meshkit import wire
from meshkit.budgets import Budget, Meter
from meshkit.relay import DedupCache, decide_relay


def decide(**kwargs):
    defaults = dict(
        ttl=7,
        sender_is_self=False,
        recipient_is_self=False,
        is_directed_encrypted=False,
        is_fragment=False,
        is_handshake=False,
        is_announce=False,
        is_request_sync=False,
        degree=3,
        rng=random.Random(1),
    )
    defaults.update(kwargs)
    return decide_relay(**defaults)


class TestRelayPolicy(unittest.TestCase):
    def test_unknown_type_relays_as_broadcast(self):
        """The property the whole platform rides on: 0x2D relays."""
        d = decide(degree=3)
        self.assertTrue(d.should_relay)
        self.assertEqual(d.new_ttl, 5)  # preferred 6 for non-announce, minus 1

    def test_dense_graph_clamps_ttl(self):
        d = decide(degree=6)
        self.assertTrue(d.should_relay)
        self.assertEqual(d.new_ttl, 4)  # cap 5, minus 1

    def test_thin_chain_full_depth(self):
        d = decide(degree=2)
        self.assertEqual(d.new_ttl, 6)  # full TTL 7, minus 1

    def test_ttl_one_stops(self):
        self.assertFalse(decide(ttl=1).should_relay)

    def test_self_traffic_stops(self):
        self.assertFalse(decide(sender_is_self=True).should_relay)
        self.assertFalse(decide(recipient_is_self=True).should_relay)

    def test_directed_reliably_relayed(self):
        d = decide(is_directed_encrypted=True, degree=9)
        self.assertTrue(d.should_relay)
        self.assertEqual(d.new_ttl, 6)  # no broadcast cap for directed
        self.assertLessEqual(d.delay_ms, 60)

    def test_request_sync_never_relays(self):
        self.assertFalse(decide(is_request_sync=True, ttl=7).should_relay)

    def test_announce_gets_headroom(self):
        d = decide(is_announce=True, degree=4)
        self.assertEqual(d.new_ttl, 6)  # preferred 7, minus 1


class TestDedupCache(unittest.TestCase):
    def test_second_sighting_dropped(self):
        cache = DedupCache()
        self.assertFalse(cache.seen("a"))
        self.assertTrue(cache.seen("a"))

    def test_capacity_eviction(self):
        now = [0.0]
        cache = DedupCache(capacity=3, clock=lambda: now[0])
        for i in range(3):
            cache.seen(f"e{i}")
        now[0] += 1.0
        self.assertFalse(cache.seen("new"))  # evicts the oldest
        now[0] += 1.0
        self.assertFalse(cache.seen("e0"))  # was evicted; re-seen as new


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestBudgets(unittest.TestCase):
    def test_manifest_maxima(self):
        over = Budget(bytes_per_second=10_000)
        self.assertIsNotNone(over.validate())
        ok = Budget()
        self.assertIsNone(ok.validate())

    def test_meter_blocks_burst_overrun(self):
        clock = FakeClock()
        meter = Meter(budget=Budget(burst_bytes=300, bytes_per_second=100), clock=clock)
        self.assertIsNone(meter.check_send(200, False))
        meter.charge(200, False)
        violation = meter.check_send(200, False)  # only 100 of 300 left
        self.assertIsNotNone(violation)  # burst exhausted
        clock.t += 10.0  # refill to the 300-byte burst cap
        self.assertIsNone(meter.check_send(200, False))

    def test_broadcast_rate(self):
        clock = FakeClock()
        meter = Meter(budget=Budget(broadcast_events_per_second=2), clock=clock)
        for _ in range(2):
            self.assertIsNone(meter.check_send(10, True))
            meter.charge(10, True)
        self.assertIsNotNone(meter.check_send(10, True))
        clock.t += 1.0
        self.assertIsNone(meter.check_send(10, True))

    def test_event_size_cap(self):
        meter = Meter(budget=Budget(max_event_bytes=200))
        self.assertIsNotNone(meter.check_send(201, False))


if __name__ == "__main__":
    unittest.main()
