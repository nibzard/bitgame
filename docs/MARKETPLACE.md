# Local marketplace on meshkit

The marketplace is not a separate system. It is an app that composes
platform primitives that already ship in BitChat.

## Primitive mapping

| Need | Primitive | BitChat source |
|---|---|---|
| Listing | Signed geohash board post (7-day expiry, tombstones) | `boardPost` 0x23, `BoardPackets.swift` |
| Presence | Geohash channels and heartbeats | Nostr kinds 20000/20001 |
| Reputation | Vouch attestations, web of trust | `VouchAttestation.swift` |
| Payment | Cashu ecash token in a directed event | `CashuTokenDecoder.swift` |
| Escrow | Mutually vouched third peer holds the token | courier pattern |
| Dispute | Board tombstone plus vouch withdrawal | boards + vouches |

## Trade flow (as the market-sim app runs it)

```
seller            buyer             escrow agent        mesh
  |                 |                    |               |
  |-- LIST ------ broadcast ----------->|--------------->|  listing visible
  |                 |                    |               |
  |                 |<------ geohash board / channel ---- |
  |                 |                    |               |
  |<---------- BUY (order) ------------>|--------------->|  order created
  |                 |                    |               |
  |                 |== cashu token == hands agent =====>|  off-mesh or DM
  |                 |                    |               |
  |                 |-- HOLD --------- broadcast ------->|  token held, order held
  |                 |                    |               |
  |                 |<===== goods delivered =============|  physical handover
  |                 |                    |               |
  |                 |-- RELEASE ------ broadcast ------->|  token released to seller
  |                 |                    |               |
  |                 |   reputation: agent +1 on delivered, -1 on refunded
```

Every event is an appEvent (0x2D) with a (seq, sender) key; every replica
applies the same order and computes the same state root. The escrow agent
is any peer both sides vouched for; their incentive is the reputation
delta, which the ledger itself tracks.

## The double-spend problem, stated plainly

Offline Cashu is bearer ecash. A buyer can hand the same token to two
sellers; only the mint can detect the second spend. No mesh protocol
removes this without a mint. Mitigations, in the order the platform
applies them:

1. **Amount caps tied to reputation.** A new buyer trades small amounts
   until vouches accumulate.
2. **Escrow settlement.** The agent verifies the token when any link to
   the internet exists (one peer online is enough) before RELEASE.
3. **Mint verification when reachable.** Nostr bridges make mints
   reachable over Tor; the envelope format already exists.
4. **Local knowledge.** The strongest offline trust signal is physical:
   you can find the seller. Geohash presence is the trust anchor.

The platform claims eventual, reputation-weighted settlement — not
cryptographic double-spend prevention. Apps that need the latter must wait
for a mint round trip.

## Catalog

Apps themselves distribute the same way: a manifest board post
(`meshapp:` prefix, see `docs/WIRE.md`), fetch by content hash, verify,
install. A marketplace of apps and a marketplace of goods share one
mechanism.
