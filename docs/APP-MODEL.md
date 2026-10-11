# App model: WASM plus manifests

Status: design. The Python runtime in `reference/runtime/` implements the
same contract; the WASM boundary is the production target.

## Why WebAssembly (WASM)

1. **Determinism.** Same inputs, same state, on every device. This gives
   lockstep multiplayer without a server and verifiable outcomes when
   value moves. State roots make two replicas provable-equal.
2. **Sandbox.** A WASM module has no ambient authority: no radio, no
   filesystem, no clock, no network. Every capability flows through the
   host, which enforces the manifest.
3. **Size.** A small game compiles to 50–300 KiB — one mesh file transfer
   (1 MiB cap), syncable by gossip.
4. **Toolchain reach.** Rust, C, AssemblyScript, Go all target WASM.

## Module ABI

The module exports three functions; the host imports one:

```wasm
export init(seed: bytes) -> handle        ; called once per install
export reduce(handle, event: bytes) -> {state_delta, effects}
export render(handle) -> view: bytes      ; host-facing UI description
import  host.emit(effect: bytes)          ; subject to manifest budget
```

`event` is the decoded appEvent payload (the TLV block, SPEC 4.1). Effects
are app-defined bytes the host transmits as new appEvents after metering.

## Determinism rules

- No `time`, no randomness, no float unless the app pins an IEEE 754
  policy in its manifest. Integer math is the default.
- All ordering comes from the host's (seq, sender) admission order.
- State is a byte blob the module owns; the host seals it (SPEC 8) and
  hashes it for `stateRoot`.

## Rounds

The host runs rounds (SPEC 8): drain ordered events, call `reduce` up to
the per-round cap, seal state, emit metered effects. A module that exceeds
its tick budget is interrupted and resumed next round — never killed; slow
apps degrade, they do not stall the mesh.

## Manifest and install

See SPEC section 7. Install = fetch by content hash (board catalog or
direct transfer), verify Ed25519 signature, clamp budgets, show requested
permissions, activate. Uninstall = delete sealed state; panic wipe removes
everything at once.

## Host rendering

The module returns a declarative view (JSON), and the host renders it with
platform UI. This keeps modules small and lets the host enforce platform
look-and-feel, accessibility, and localization. Games needing richer
graphics get a canvas-style view type later; the compute contract does not
change.

## Reference runtime mapping

| Production concept | Reference (Python) |
|---|---|
| WASM module | `reference/apps/market_sim/core.py` |
| `init` | `init_state(seed)` |
| `reduce` | `reduce(state, event) -> (state, effects)` |
| `render` | `render_state(state)` |
| Host round | `AppInstance.run_round` |
| Sealed state | in-memory dict plus `state_root()` hash |
