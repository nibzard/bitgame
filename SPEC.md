# meshkit platform specification

**Version:** 0.1 (draft)
**Date:** 2026-10-11
**Status:** walking skeleton; reference implementation in `reference/`

---

## 1. Scope

meshkit is a platform for constrained third-party applications that run over
the BitChat mesh. One binary protocol carries chat, games, and marketplace
traffic. This document is the contract between the platform and an app.

Terms:

- **Host** — the app that owns the radio, the keys, and the runtime. BitChat
  is the reference host.
- **App** — a signed, content-addressed module plus a manifest. It computes
  state. It never touches the radio.
- **Event** — one app message on the mesh, carried in an `appEvent` packet.

The platform inherits the BitChat transport unchanged. This spec only adds:

1. One wire type (`0x2D`), one capability bit, and their namespaces.
2. A manifest format and an install flow.
3. A deterministic runtime contract.
4. Per-app resource budgets.

## 2. Network model (inherited)

Apps run on the physics the mesh already provides. Measured constants that
bound every design decision:

| Constant | Value | Source in BitChat |
|---|---|---|
| Default TTL | 7 hops | `TransportConfig.messageTTLDefault` |
| Fragment size | 469 bytes | `TransportConfig.bleDefaultFragmentSize` |
| Practical throughput | ~2–6 KiB/s | voice budget 6 KiB/s inbound |
| Directed relay jitter | 20–60 ms per relay hop | `RelayController` |
| Broadcast relay jitter | 10–220 ms per relay hop | `RelayController` |
| Central links per device | 6 | `TransportConfig.bleMaxCentralLinks` |
| Timestamp freshness gate | ±120 s | `TransportConfig.bleMaxTimestampSkewMs` |
| Dedup cache | 1000 entries, 5 min | mesh seen-set |
| Unknown-type payload cap | 128 KiB | `PacketPayloadLimits` |

Unknown wire types relay as broadcasts through deployed BitChat nodes. An
`appEvent` packet from a meshkit host reaches any BitChat phone in range and
travels up to seven hops with no changes to those devices.

## 3. Registry

All assigned values live here. Forks must not invent collisions.

### 3.1 Wire type

| Value | Name | Owner |
|---|---|---|
| `0x2D` | `appEvent` | meshkit (first free value after `0x2C` `announceV2`; `0x2A`/`0x2B` are reserved by BitChat courier spray-ack work; `0x05` is poisoned — recycled twice historically) |

Payload of an `appEvent` packet is a TLV block (section 4). `0x2D` packets
relay under the broadcast policy: dense graphs clamp TTL to 5, fanout is
subset, ingress link excluded, dedup applies.

### 3.2 Capability bit

| Bit | Name | Meaning |
|---|---|---|
| 11 (`1 << 11`) | `meshapps` | This peer hosts meshkit apps; it accepts, relays, and renders `appEvent` traffic |

Bits 0–10 are assigned by BitChat (`PeerCapabilities`). Bits 12+ remain
unassigned in this spec.

### 3.3 Board content prefix

Catalog manifests travel as geohash board posts (`0x23`) whose content field
starts with `meshapp:` followed by canonical JSON of the manifest. Board
limits apply: 512 bytes total, 7-day expiry, signed, tombstoneable.

## 4. App events

### 4.1 Envelope

An app event is a BitChat v1 or v2 packet with `type = 0x2D`. The packet
header, flags, and relay treatment are unchanged. The payload is a TLV block
(1-byte type, 1-byte length):

| TLV | Name | Size | Required | Notes |
|---|---|---|---|---|
| `0x01` | appId | ≤ 64 B UTF-8 | yes | Reverse-DNS, for example `im.bitgame.market-sim` |
| `0x02` | eventType | ≤ 16 B UTF-8 | yes | App-defined verb, for example `LIST` |
| `0x03` | eventId | 16 B | yes | Random per event; the idempotency key |
| `0x04` | seq | 8 B big-endian u64 | yes | Sender's counter, contiguous per sender per app, starts at 1 |
| `0x05` | flags | 1 B | no | bit 0: ack requested |
| `0x06` | body | ≤ 160 B | no | App-defined, canonical JSON or app bytes |
| `0x07` | stateRoot | 32 B | no | Sender's state hash after applying; enables catch-up verification |

Total payload must stay ≤ 200 bytes so one event fits a single fragment.
Unknown TLVs are skipped, matching BitChat decoders.

### 4.2 Addressing

- **Broadcast events** carry no recipient ID. They flood; delivery is
  at-least-once with duplicates expected.
- **Directed events** carry a recipient ID. They relay deterministically
  (never subset, TTL − 1 per hop) and are the fast path for two-player or
  escrow traffic.

### 4.3 Delivery contract

The network gives at-least-once delivery with no ordering. Therefore:

- Handlers must be idempotent. The `eventId` is the key: a repeat is dropped
  before `reduce`.
- The mesh dedup key is `sender + timestamp(ms) + type + payload digest`. An
  identical retransmit within the same millisecond collapses at relay level.
  That is acceptable; a retransmit SHOULD mutate nothing anyway.
- Wall-clock time never decides game or market outcomes. The ±120 s
  freshness gate exists for replay rejection only. Ordering comes from the
  per-sender `seq` counter.

## 5. Ordering and determinism

Every app is a deterministic state machine:

```
state' , effects = reduce(state, event)
```

Rules:

1. The host applies events per app in `(seq, senderPeerId)` order. The key
   is intrinsic to each event, so every replica that observes the same
   event set applies them in the same order and reaches the same state.
2. A sender's `seq` is contiguous per app: 1, 2, 3, … A receiver buffer
   holds an event whose `seq` runs ahead of its predecessor and flushes
   when the gap closes, or after a 30 s grace window (latecomers apply in
   order, never dropped silently).
3. `reduce` sees no wall clock, no randomness, no radio, no floats unless
   the app pins an IEEE 754 policy. Integer math is the default.
4. Cross-sender causality is NOT guaranteed in v0.1: an event from sender
   A may apply before the sender-B event it replies to. Apps must write
   order-tolerant handlers (a `BUY` against a missing listing is a no-op,
   not a crash). Reconciliation on top of state roots is future work.
5. State changes only through events. Persisted state is sealed under a
   per-app key and dies with the panic wipe.

## 6. Budgets

Budgets protect the mesh — every relayed byte spends someone's battery.
Budgets are declared in the manifest, capped by the platform, and enforced
at two points: decode time (hard caps, inherited) and host time (per-app
metering).

| Resource | Platform maximum | Default |
|---|---|---|
| Event payload | 200 B | 200 B |
| Sustained bytes per app | 1 KiB/s | 300 B/s |
| Burst | 4 KiB | 1 KiB |
| Broadcast events per app | 4 / s | 2 / s |
| Persisted state | 64 KiB | 16 KiB |
| Events applied per round | 128 | 64 |

An event over budget is dropped before transmit and logged locally. A
manifest requesting more than the maximum fails install.

## 7. Manifest

A manifest is a JSON object; its canonical serialization (sorted keys, no
whitespace) is what is signed and hashed.

```json
{
  "manifestVersion": 1,
  "appId": "im.bitgame.market-sim",
  "version": "0.1.0",
  "name": "Mesh Market Sim",
  "description": "Local marketplace trading simulation",
  "entry": "core.wasm",
  "moduleHash": "sha256:<hex of module bytes>",
  "signer": "ed25519:<hex public key>",
  "signature": "ed25519:<hex over canonical manifest minus signature field>",
  "budget": {
    "bytesPerSecond": 300,
    "burstBytes": 1024,
    "broadcastEventsPerSecond": 2,
    "maxEventBytes": 200,
    "stateBytes": 16384
  },
  "permissions": ["mesh.broadcast", "mesh.direct", "state.persist"]
}
```

Permission set (closed; unknown permissions fail install):

| Permission | Grants |
|---|---|
| `mesh.broadcast` | Send broadcast `appEvent` packets |
| `mesh.direct` | Send directed `appEvent` packets |
| `state.persist` | Seal state across restarts |
| `board.post` | Publish board posts (catalog listings) |
| `value.cashu.receive` | Parse Cashu tokens in directed events |
| `value.cashu.send` | Attach Cashu tokens to directed events |

Install flow: fetch module by hash → verify signature → check budget maxima
→ show permissions to the user → activate. Apps are disabled, not deleted,
when a newer manifest tombstones the catalog entry.

## 8. Runtime

The host runs each app in rounds:

1. Collect ordered events from the buffer.
2. Call `reduce` per event, up to the round cap. Overflow stays queued.
3. Seal state; emit effects (outbound events) subject to budgets.

The reference runtime in `reference/runtime/host.py` implements this with a
Python module standing in for the future WASM module. The WASM ABI is in
`docs/APP-MODEL.md`; the reduce signature is identical.

## 9. Value and marketplace primitives

Mapping onto existing BitChat mechanisms:

| Primitive | Mechanism |
|---|---|
| Listing | Board post in a geohash cell, signed, ≤ 7 day expiry |
| Discovery | Geohash presence and channels |
| Reputation | Vouch attestations (web of trust) |
| Payment | Cashu ecash token inside a directed event |
| Escrow | A mutually vouched third peer holds a sealed envelope until delivery confirmation |
| Dispute | Signed board tombstone plus vouch withdrawal |

Known hard problem, stated plainly: offline Cashu is bearer ecash and can be
double-spent to two sellers. Mitigations in priority order: small amounts
plus vouch-weighted trust; mint verification whenever any peer has internet;
escrow agents with deferred settlement. This spec does not claim to solve it.

## 10. Conformance

An implementation of meshkit must:

1. Encode and decode `0x2D` per section 4, byte-compatible with
   `BinaryProtocol.swift`.
2. Relay per the broadcast policy and dedup per the mesh seen-set.
3. Apply events idempotently, ordered by `(lamport, sender)`.
4. Enforce budgets at decode time and host time.
5. Validate manifests per section 7.

The test suite in `tests/` demonstrates each requirement against the
reference implementation.
