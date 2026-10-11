# bitgame

A constrained app platform on the BitChat mesh: games, marketplaces, and
tools that run peer-to-peer over Bluetooth, with no servers and no
accounts. BitChat phones in radio range relay platform traffic unchanged.

The primitive: deterministic apps on a delay-tolerant local mesh with
bearer payments. Read `SPEC.md` first — it is the contract everything
else implements.

## Status

Walking skeleton, spec-first. What exists:

- `SPEC.md` — the platform contract: wire registry, event format,
  ordering, budgets, manifests, runtime.
- `ARCHITECTURE.md` — layering, the porting map from BitChat, roadmap.
- `reference/` — a runnable Python implementation of the platform core:
  wire codec (ported byte-for-byte from `BinaryProtocol.swift`), relay
  policy, ordering buffer, budget metering, manifest validation,
  deterministic app host.
- `reference/apps/market_sim/` — the first vertical: a marketplace
  trading sim that exercises listings, orders, escrow, reputation, and
  settlement.
- `reference/sim/mesh_sim.py` — a three-node mesh demo.
- `tests/` — 49 tests covering wire bytes, relay policy, budgets,
  determinism, idempotency, and the market core.

## Quickstart

```bash
# run the mesh demo (from reference/)
cd reference && python3 sim/mesh_sim.py

# run the test suite (from the repo root)
python3 -m unittest discover -s tests -t .
```

## The demo shows

1. Capability bit 11 (`meshapps`) advertisement.
2. A full marketplace round trip: LIST → BUY → HOLD → RELEASE.
3. Mesh-level dedup of a reinjected packet.
4. Host-level idempotency: a redelivered event changes no state root.
5. Causal ordering: an early RELEASE buffers until its HOLD applies.
6. Budget enforcement: burst sends are refused before transmit.
7. Convergence: all three nodes reach the same state root.

## Key decisions

- Wire type `0x2D` (`appEvent`) and capability bit 11 are the only new
  values; deployed BitChat relays forward them unchanged.
- Apply order is `(seq, sender)` — a per-sender contiguous counter with a
  deterministic tie-break. Convergence needs no coordination;
  cross-sender causality is not guaranteed (SPEC 5.4), so apps write
  order-tolerant handlers.
- Budgets are enforced, not requested: decode-time caps plus host-time
  token buckets per app.
- Apps are signed manifests plus content-addressed modules; the compute
  target is WASM, and the reference runtime implements the same ABI in
  Python (`docs/APP-MODEL.md`).
- The marketplace composes existing BitChat primitives: boards for
  listings, vouches for reputation, Cashu tokens for payment, a vouched
  third peer for escrow (`docs/MARKETPLACE.md`). Offline double-spend is
  not solved and is not claimed to be.

## License

Reference code follows the BitChat tree it ports: public domain
(Unlicense). See `LICENSE`.
