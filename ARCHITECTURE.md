# meshkit architecture

## Layering

```
┌──────────────────────────────────────────────────────────┐
│ host apps: chat (BitChat), games, marketplace, tools     │
├──────────────────────────────────────────────────────────┤
│ runtime: WASM host, metering, manifest, catalog          │  new
├──────────────────────────────────────────────────────────┤
│ value: cashu send/settle, escrow, reputation (vouches)   │  partial
├──────────────────────────────────────────────────────────┤
│ social: boards, presence, geohash channels               │  exists
├──────────────────────────────────────────────────────────┤
│ dtn: outbox, couriers, gossip sync, nostr bridge         │  exists
├──────────────────────────────────────────────────────────┤
│ sessions: noise XX, groups, prekeys                      │  exists
├──────────────────────────────────────────────────────────┤
│ identity: keys, fingerprints, announces, verification    │  exists
├──────────────────────────────────────────────────────────┤
│ wire: binary protocol, packets, TLV, caps, limits        │  exists (BitFoundation)
├──────────────────────────────────────────────────────────┤
│ transport: BLE link layer, flood control, fragmentation  │  exists (BLEService)
└──────────────────────────────────────────────────────────┘
```

"Exists" means working Swift in the BitChat tree. The platform work is
extraction plus the two new layers on top.

## Porting map

| meshkit layer | BitChat source | Reference port |
|---|---|---|
| wire | `BitFoundation/BinaryProtocol.swift`, `PacketPayloadLimits.swift`, `MessageType.swift`, `PeerCapabilities.swift` | `reference/meshkit/wire.py` |
| transport policy | `RelayController.swift`, `BLEReceivePipeline.swift` | `reference/meshkit/relay.py` |
| event codec | new (SPEC 4.1) | `reference/meshkit/events.py` |
| ordering | new (SPEC 5) | `reference/meshkit/ordering.py` |
| budgets | new, pattern from decode-time caps | `reference/meshkit/budgets.py` |
| runtime | new (SPEC 8) | `reference/runtime/host.py` |
| manifests | new (SPEC 7) | `reference/runtime/manifest.py` |
| first app | new | `reference/apps/market_sim/` |

The Python tree is a runnable specification, not the shipping code. Swift
(and Kotlin for Android parity) implementations must match its byte-level
behavior; the test suite in `tests/` is the conformance bar.

## What the reference implementation proves today

Run `python3 sim/mesh_sim.py` from `reference/`:

1. `0x2D` packets encode and decode byte-compatible with the BitChat
   format (v1 and v2 headers, compression, padding, signature coverage).
2. Relay policy treats app events as broadcasts; dedup drops reinjected
   copies at mesh level.
3. Host-level idempotency: a redelivered event changes no state root.
4. Ordering: an event that arrives before its predecessor is buffered and
   applied in (seq, sender) order.
5. Budgets: a spammy app is refused before transmit.
6. Convergence: three replicas apply the same eight events and reach
   identical state roots.

## Design decisions worth remembering

- **Ordering key is (seq, sender), not a lamport clock.** Lamport counters
  merge on receive, which makes them non-contiguous; gap-buffered delivery
  would stall. A per-sender contiguous seq plus a deterministic tie-break
  gives convergence with no coordination. Trade-off: cross-sender
  causality is not guaranteed — apps write order-tolerant reduce (SPEC
  5.4); market-sim demonstrates the pending-replay pattern.
- **Ledger apps broadcast; games may go directed.** Any event that feeds
  shared state must reach every replica. Directed events suit 1:1
  sessions and relay deterministically.
- **Budgets live at two points.** Decode-time hard caps (inherited,
  per type) and host-time per-app metering (token bucket). A manifest
  over platform maxima fails install, not runtime.
- **Origins apply their own events.** Without loopback application,
  senders diverge from replicas immediately.

## Roadmap

0. **Done — spec and skeleton.** SPEC, reference implementation, tests,
   market-sim demo.
1. **Swift MeshKit extraction.** Move BitFoundation plus a transport
   facade into a reusable package; port the reference tests.
2. **WASM runtime.** `wasmi`/`wasm3` host, the ABI in
   `docs/APP-MODEL.md`, tick metering.
3. **Catalog and install.** `meshapp:` board posts, content-addressed
   fetch, signature verification, permission UI.
4. **Value.** Cashu send flow in directed events; escrow agent role;
   vouch aggregation.
5. **Android parity.** Kotlin wire layer against the same test vectors.

## Risks carried forward

- Battery: every relayed byte spends a stranger's battery. Metering is a
  platform duty, not an app courtesy.
- The 6-hour gossip window fits chat, not a catalog; distribution needs a
  longer-retention blob class.
- Malware: signatures prove authorship, not safety. Vouch-weighted
  install warnings are the decentralized answer.
- Namespace governance: type values and capability bits need a registry
  (SPEC 3) that forks actually consult.
