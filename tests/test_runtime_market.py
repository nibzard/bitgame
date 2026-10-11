"""Runtime, manifest, ordering, and market-sim conformance tests."""

import copy
import json
import random
import unittest
from pathlib import Path

from meshkit.events import AppEvent, canonical_json
from meshkit.ordering import OrderBuffer
from runtime.host import AppInstance
from runtime.manifest import parse_manifest, validate_manifest
from apps.market_sim import core

MANIFEST = json.loads(
    (Path(__file__).resolve().parents[1] / "reference" / "apps" / "market_sim" / "manifest.json").read_text()
)

ALICE = b"\x11" * 8
CAROL = b"\x22" * 8
BOB = b"\x33" * 8


def make_host(peer=ALICE):
    manifest = parse_manifest(copy.deepcopy(MANIFEST))
    return AppInstance(manifest, core, peer, seed=b"test")


def event(app_id="im.bitgame.market-sim", event_type="LIST", seq=1, sender=ALICE,
          body=None, event_id=None):
    import hashlib
    unique = hashlib.sha256(
        bytes([seq]) + sender + event_type.encode() + canonical_json(body or {})
    ).digest()[:16]
    return AppEvent(
        app_id=app_id,
        event_type=event_type,
        event_id=event_id or unique,
        seq=seq,
        sender=sender,
        body=canonical_json(body or {}),
    )


class TestManifest(unittest.TestCase):
    def test_reference_manifest_valid(self):
        self.assertIsNone(validate_manifest(MANIFEST))

    def test_unknown_permission_rejected(self):
        bad = copy.deepcopy(MANIFEST)
        bad["permissions"].append("radio.raw")
        self.assertIn("unknown permission", validate_manifest(bad))

    def test_budget_over_maximum_rejected(self):
        bad = copy.deepcopy(MANIFEST)
        bad["budget"]["bytesPerSecond"] = 999_999
        self.assertIn("maximum", validate_manifest(bad))

    def test_canonical_form_is_stable(self):
        m1 = parse_manifest(copy.deepcopy(MANIFEST))
        shuffled = copy.deepcopy(MANIFEST)
        shuffled["name"] = shuffled.get("name")
        items = list(shuffled.items())[::-1]
        shuffled = dict(items)
        m2 = parse_manifest(shuffled)
        self.assertEqual(m1.canonical_bytes(), m2.canonical_bytes())


class TestOrderingBuffer(unittest.TestCase):
    def test_gap_holds_and_flushes(self):
        buf = OrderBuffer()
        base = event(seq=1, event_type="LIST")
        buf.push(base)
        self.assertEqual([e.seq for e in buf.deliverable()], [1])  # baseline
        e3 = event(seq=3, event_type="HOLD")
        e2 = event(seq=2, event_type="BUY")
        buf.push(e3)
        self.assertEqual(buf.deliverable(), [])  # gap: seq 2 missing
        buf.push(e2)
        self.assertEqual([e.seq for e in buf.deliverable()], [2, 3])

    def test_fresh_replica_accepts_midstream(self):
        """Documented behavior: a sender's first observed event establishes
        the baseline, so a replica joining mid-stream applies what it sees.
        Catch-up is reconciliation's job (SPEC section 5.4)."""
        buf = OrderBuffer()
        buf.push(event(seq=41, event_type="LIST"))
        self.assertEqual([e.seq for e in buf.deliverable()], [41])

    def test_duplicates_dropped(self):
        buf = OrderBuffer()
        self.assertTrue(buf.push(event(seq=1)))
        self.assertFalse(buf.push(event(seq=1)))

    def test_late_force_flush(self):
        buf = OrderBuffer()
        buf.push(event(seq=9))
        self.assertEqual([e.seq for e in buf.admit_late()], [9])

    def test_ordering_key_intrinsic(self):
        """Same set, different arrival order -> same apply order."""
        events = [
            event(seq=1, sender=ALICE, event_type="LIST"),
            event(seq=2, sender=ALICE, event_type="BUY"),
            event(seq=1, sender=BOB, event_type="LIST"),
            event(seq=2, sender=BOB, event_type="CANCEL"),
        ]
        orders = []
        for perm in ([0, 1, 2, 3], [3, 2, 1, 0], [2, 0, 3, 1]):
            buf = OrderBuffer()
            for i in perm:
                buf.push(events[i])
                buf.deliverable()
            buf.admit_late()
            applied = []
            while buf.held:
                applied += [e.seq for e in buf.admit_late()]
            orders.append(applied)
        self.assertEqual(orders[0], orders[1])
        self.assertEqual(orders[1], orders[2])


class TestDeterminism(unittest.TestCase):
    def test_same_events_same_root(self):
        events = [
            event(seq=1, sender=ALICE, event_type="LIST",
                  body={"listingId": "L1", "item": "sticker", "qty": 2, "priceSats": 500}),
            event(seq=1, sender=BOB, event_type="BUY",
                  body={"orderId": "O1", "listingId": "L1", "qty": 1}),
            event(seq=1, sender=CAROL, event_type="HOLD",
                  body={"orderId": "O1", "token": "cashuA…"}),
            event(seq=2, sender=CAROL, event_type="RELEASE",
                  body={"orderId": "O1", "outcome": "delivered"}),
        ]
        roots = []
        for permutation in (events, events[::-1], [events[1], events[0], events[3], events[2]]):
            host = make_host()
            for e in permutation:
                host.admit(copy.deepcopy(e))
            host.run_round(force_flush=True)
            roots.append(host.state_root())
        self.assertEqual(roots[0], roots[1])
        self.assertEqual(roots[1], roots[2])

    def test_idempotent_redelivery(self):
        host = make_host()
        e = event(seq=1, sender=BOB, event_type="LIST",
                  body={"listingId": "L9", "item": "mug", "qty": 1, "priceSats": 100})
        host.admit(copy.deepcopy(e))
        host.run_round()
        root = host.state_root()
        self.assertFalse(host.admit(copy.deepcopy(e)))  # duplicate refused
        self.assertEqual(host.state_root(), root)


class TestMarketCore(unittest.TestCase):
    def test_full_trade_flow(self):
        host = make_host()
        flow = [
            (1, ALICE, "LIST", {"listingId": "L1", "item": "sticker", "qty": 2, "priceSats": 500}),
            (1, BOB, "BUY", {"orderId": "O1", "listingId": "L1", "qty": 1}),
            (1, CAROL, "HOLD", {"orderId": "O1", "token": "cashuA…"}),
            (2, CAROL, "RELEASE", {"orderId": "O1", "outcome": "delivered"}),
        ]
        for seq, sender, etype, body in flow:
            host.admit(event(seq=seq, sender=sender, event_type=etype, body=body))
        host.run_round(force_flush=True)
        state = host.state
        # qty 2 listed, 1 bought: listing stays open with 1 left
        self.assertEqual(state["listings"]["L1"]["status"], "open")
        self.assertEqual(state["listings"]["L1"]["qty"], 1)
        self.assertEqual(state["orders"]["O1"]["status"], "settled")
        self.assertEqual(state["reputation"][CAROL.hex()], 1)
        view = host.view()
        self.assertEqual(view["listings"]["L1"]["status"], "open")

    def test_only_seller_cancels(self):
        host = make_host()
        host.admit(event(seq=1, sender=ALICE, event_type="LIST",
                         body={"listingId": "L1", "item": "x", "qty": 1, "priceSats": 5}))
        host.admit(event(seq=1, sender=BOB, event_type="CANCEL", body={"listingId": "L1"}))
        host.run_round(force_flush=True)
        self.assertEqual(host.state["listings"]["L1"]["status"], "open")  # bob cannot cancel

    def test_refund_hurts_reputation(self):
        host = make_host()
        for seq, sender, etype, body in [
            (1, ALICE, "LIST", {"listingId": "L1", "item": "x", "qty": 1, "priceSats": 5}),
            (1, BOB, "BUY", {"orderId": "O1", "listingId": "L1", "qty": 1}),
            (1, CAROL, "HOLD", {"orderId": "O1", "token": "t"}),
            (2, CAROL, "RELEASE", {"orderId": "O1", "outcome": "refunded"}),
        ]:
            host.admit(event(seq=seq, sender=sender, event_type=etype, body=body))
        host.run_round(force_flush=True)
        self.assertEqual(host.state["orders"]["O1"]["status"], "refunded")
        self.assertEqual(host.state["reputation"][CAROL.hex()], -1)

    def test_buy_missing_listing_is_noop(self):
        """Order tolerance: cross-sender causality is not guaranteed (SPEC 5.4)."""
        host = make_host()
        host.admit(event(seq=1, sender=BOB, event_type="BUY",
                         body={"orderId": "O1", "listingId": "nope", "qty": 1}))
        host.run_round(force_flush=True)
        self.assertEqual(host.state["orders"], {})

    def test_foreign_app_events_refused(self):
        host = make_host()
        self.assertFalse(host.admit(event(app_id="evil.app", seq=1)))


class TestHostBudget(unittest.TestCase):
    def test_permission_gates_broadcast(self):
        host = make_host()
        self.assertIn("mesh.broadcast", host.manifest.permissions)
        event_out, problem = host.build_event("LIST", {"listingId": "L1", "item": "i", "qty": 1, "priceSats": 1})
        self.assertIsNone(problem)
        self.assertIsNotNone(event_out)

    def test_oversize_body_refused(self):
        host = make_host()
        big = {"listingId": "L1", "item": "x" * 300, "qty": 1, "priceSats": 1}
        _, problem = host.build_event("LIST", big)
        self.assertIsNotNone(problem)


class TestSimConvergence(unittest.TestCase):
    def test_sim_converges(self):
        import sys
        from pathlib import Path
        reference = str(Path(__file__).resolve().parents[1] / "reference")
        if reference not in sys.path:
            sys.path.insert(0, reference)
        from sim import mesh_sim

        code = mesh_sim.main()
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
