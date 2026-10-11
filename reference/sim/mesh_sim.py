"""Three-node mesh simulation: the walking-skeleton demo.

Topology (chain, so relaying matters):

    alice (seller) --- carol (relay + escrow) --- bob (buyer)

Run:  python3 sim/mesh_sim.py   (from reference/)

Demonstrates, in order:
  1. Announce with capability bit 11 (meshapps).
  2. A full marketplace round trip: LIST -> BUY -> HOLD -> RELEASE.
     Ledger events are broadcast: every replica applies every event and
     converges. Directed events are for 1:1 sessions (relay test covers
     their deterministic forwarding).
  3. Mesh-level dedup: a reinjected duplicate packet never re-applies.
  4. Host-level idempotency: a redelivered event changes no state root.
  5. Ordering: a RELEASE that arrives before its HOLD is buffered until
     the predecessor applies.
  6. Budget enforcement: a spammy app event is refused before transmit.
  7. Convergence: all nodes compute the same state root.
"""

from __future__ import annotations

import importlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshkit import wire
from meshkit.events import APP_EVENT, AppEvent
from meshkit.relay import DedupCache, relay_decision_for_packet
from runtime.host import AppInstance
from runtime.manifest import parse_manifest

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "apps" / "market_sim" / "manifest.json"


class Node:
    def __init__(self, name: str, peer_id: bytes, rng: random.Random):
        self.name = name
        self.peer_id = peer_id
        self.rng = rng
        self.dedup = DedupCache()
        self.app: AppInstance | None = None
        self.log: list[str] = []

    def load_app(self, manifest_data: dict, core, seed: bytes, clock=None) -> None:
        manifest = parse_manifest(manifest_data)
        self.app = AppInstance(manifest, core, self.peer_id, seed=seed, clock=clock)

    # -- mesh side -----------------------------------------------------------

    def receive(self, from_peer: bytes, packet: wire.Packet, mesh) -> None:
        message_id = wire.packet_message_id(packet)
        if self.dedup.seen(message_id):
            self.log.append(f"{self.name}: mesh dedup dropped reinjected {packet.type:#04x}")
            return
        if not wire.is_fresh(packet, now_ms=mesh.now_ms):
            self.log.append(f"{self.name}: stale packet dropped")
            return

        # relay first: unknown 0x2D takes the broadcast policy
        decision = relay_decision_for_packet(
            packet, local_sender=self.peer_id, degree=len(mesh.links[self.name]), rng=self.rng
        )
        if decision.should_relay:
            relayed = wire.Packet(
                type=packet.type,
                sender_id=packet.sender_id,
                timestamp=packet.timestamp,
                payload=packet.payload,
                ttl=decision.new_ttl,
                recipient_id=packet.recipient_id,
                signature=packet.signature,
                version=packet.version,
                route=packet.route,
                is_rsr=packet.is_rsr,
            )
            for neighbor in mesh.links[self.name]:
                if neighbor == from_peer.hex():
                    continue  # split horizon
                mesh.inject(self, mesh.nodes[neighbor], relayed)

        if packet.recipient_id is None or packet.recipient_id == self.peer_id:
            self.deliver_to_app(packet)

    def deliver_to_app(self, packet: wire.Packet) -> None:
        if self.app is None or packet.type != APP_EVENT:
            return
        event = AppEvent.decode(packet.payload)
        if event is None:
            self.log.append(f"{self.name}: malformed appEvent dropped")
            return
        event.sender = packet.sender_id
        before = self.app.state_root().hex()[:12]
        if not self.app.admit(event):
            self.log.append(
                f"{self.name}: duplicate {event.event_type}@{event.seq} ignored, "
                f"root {before} unchanged"
            )
            return
        self.app.run_round(force_flush=False)
        after = self.app.state_root().hex()[:12]
        note = f" (still held: {self.app.buffer.held})" if self.app.buffer.held else ""
        if before != after:
            self.log.append(
                f"{self.name}: applied {event.event_type}@{event.seq} from "
                f"{packet.sender_id.hex()[:4]} root {before} -> {after}{note}"
            )
        else:
            self.log.append(
                f"{self.name}: {event.event_type}@{event.seq} committed, "
                f"root unchanged{note}"
            )

    # -- app side ------------------------------------------------------------

    def emit(self, event: AppEvent, recipient: bytes | None, mesh) -> None:
        packet = wire.Packet(
            type=APP_EVENT,
            sender_id=self.peer_id,
            timestamp=mesh.now_ms,
            payload=event.encode(),
            ttl=7,
            recipient_id=recipient,
        )
        self.log.append(
            f"{self.name}: send {event.event_type}@{event.seq} "
            f"({'broadcast' if recipient is None else 'directed'}) "
            f"{len(packet.payload)}B payload"
        )
        for neighbor in mesh.links[self.name]:
            mesh.inject(self, mesh.nodes[neighbor], packet)
        self.deliver_to_app(packet)  # origins apply their own events


class SimMesh:
    """Shared injectable clock: scenario steps advance simulated time so
    per-app budget metering behaves as it would across real seconds."""

    def __init__(self, seed: int = 42):
        self._monotonic = 0.0
        self.nodes: dict[str, Node] = {
            "alice": Node("alice", b"\x11" * 8, random.Random(seed + 1)),
            "carol": Node("carol", b"\x22" * 8, random.Random(seed + 2)),
            "bob": Node("bob", b"\x33" * 8, random.Random(seed + 3)),
        }
        self.links = {"alice": ["carol"], "carol": ["alice", "bob"], "bob": ["carol"]}
        self.now_ms = 1_800_000_000_000
        self.transmissions = 0

    def clock(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        self._monotonic += seconds
        self.now_ms += int(seconds * 1000)

    def inject(self, src: Node, dst: Node, packet: wire.Packet) -> None:
        self.transmissions += 1
        dst.receive(src.peer_id, packet, self)

    def settle(self) -> None:
        """Run rounds everywhere until quiet (the sim has no real timers)."""
        for _ in range(4):
            for node in self.nodes.values():
                if node.app:
                    node.app.run_round(force_flush=False)


def main() -> int:
    manifest_data = json.loads(MANIFEST_PATH.read_text())
    core = importlib.import_module("apps.market_sim.core")
    mesh = SimMesh()
    for node in mesh.nodes.values():
        node.load_app(manifest_data, core, seed=b"market-sim-genesis", clock=mesh.clock)

    alice, carol, bob = mesh.nodes["alice"], mesh.nodes["carol"], mesh.nodes["bob"]

    def build(node, event_type, body, recipient=None):
        event, problem = node.app.build_event(event_type, body, recipient=recipient)
        return event, problem

    print("== 1. announces carry capability bit 11 (meshapps) ==")
    caps = wire.encode_capabilities(wire.CAP_BOARD | wire.CAP_VOUCH | wire.CAP_MESHAPPS)
    print(f"   alice advertises caps {caps.hex()} -> bitfield {wire.decode_capabilities(caps):#x}")

    print("\n== 2. marketplace round trip (all ledger events broadcast) ==")
    mesh.advance(1.0)
    listing, _ = build(alice, "LIST", {"listingId": "L1", "item": "mesh sticker", "qty": 2, "priceSats": 500})
    alice.emit(listing, None, mesh)
    order, _ = build(bob, "BUY", {"orderId": "O1", "listingId": "L1", "qty": 1})
    bob.emit(order, None, mesh)
    hold, _ = build(carol, "HOLD", {"orderId": "O1", "token": "cashuAeyJwcm9vZnMiOls…"})
    carol.emit(hold, None, mesh)
    release, _ = build(carol, "RELEASE", {"orderId": "O1", "outcome": "delivered"})
    carol.emit(release, None, mesh)
    mesh.settle()

    print("\n== 3. mesh-level dedup: reinject alice's LIST packet ==")
    mesh.advance(1.0)
    dup = wire.Packet(type=APP_EVENT, sender_id=alice.peer_id, timestamp=mesh.now_ms,
                      payload=listing.encode(), ttl=7)
    mesh.inject(alice, carol, dup)
    mesh.settle()

    print("\n== 4. host-level idempotency: redeliver the BUY event to alice ==")
    alice.deliver_to_app(wire.Packet(type=APP_EVENT, sender_id=bob.peer_id, timestamp=mesh.now_ms,
                                     payload=order.encode(), ttl=7))
    mesh.settle()

    print("\n== 5. ordering: RELEASE arrives before its HOLD ==")
    # second trade, then deliver carol's pair to bob reversed
    mesh.advance(1.0)
    listing2, _ = build(alice, "LIST", {"listingId": "L2", "item": "mesh mug", "qty": 1, "priceSats": 1200})
    alice.emit(listing2, None, mesh)
    order2, _ = build(bob, "BUY", {"orderId": "O2", "listingId": "L2", "qty": 1})
    bob.emit(order2, None, mesh)
    hold2, _ = build(carol, "HOLD", {"orderId": "O2", "token": "cashuA…"})
    release2, _ = build(carol, "RELEASE", {"orderId": "O2", "outcome": "delivered"})
    # reversed delivery to bob only
    bob.deliver_to_app(wire.Packet(type=APP_EVENT, sender_id=carol.peer_id, timestamp=mesh.now_ms,
                                   payload=release2.encode(), ttl=7))
    bob.log.append(f"bob: RELEASE@{release2.seq} buffered ({bob.app.buffer.held} held)")
    bob.deliver_to_app(wire.Packet(type=APP_EVENT, sender_id=carol.peer_id, timestamp=mesh.now_ms,
                                   payload=hold2.encode(), ttl=7))
    # normal broadcast for every other replica; bob drops the duplicates
    carol.emit(hold2, None, mesh)
    carol.emit(release2, None, mesh)
    mesh.settle()

    print("\n== 6. budget enforcement: burst of LIST events ==")
    mesh.advance(1.0)  # refill, then spam within one simulated second
    for i in range(40):
        _, problem = build(alice, "LIST", {"listingId": f"spam{i}", "item": "x", "qty": 1, "priceSats": 1})
        if problem:
            alice.log.append(f"alice: LIST spam{i} refused: {problem}")
            break

    print("\n== 7. convergence ==")
    roots = {n.name: n.app.state_root().hex() for n in mesh.nodes.values()}
    for name, root in roots.items():
        print(f"   {name}: {root[:32]}…")

    print("\n-- transcript --")
    for node in (alice, carol, bob):
        for line in node.log:
            print(f"   {line}")
    print(f"\ntransmissions: {mesh.transmissions}")
    same = len(set(roots.values())) == 1
    applied = [len(n.app.applied) for n in mesh.nodes.values()]
    print(f"applied events per node: {applied}")
    print(f"convergence: {'OK — all roots identical' if same else 'MISMATCH'}")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
