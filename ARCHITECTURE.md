# bitgame architecture

## Design target

Build one nearby cooperative game with one session coordinator. Keep game
rules separate from session recovery and Bluetooth transport.

```text
Game screen
    |
Session client: join, submit, reconnect, display committed state
    |
Session protocol: authentication, revisions, retries, snapshots
    |
Transport adapter: nearby links and verified BitChat integration
```

The coordinator also runs the game rules and owns the accepted move log.
Each participant keeps a local copy of committed state for display.

## Authority and state

The coordinator serializes requests, validates moves, and assigns revisions.
A participant never commits its own move before acceptance. A missing
revision triggers recovery; elapsed time does not make it safe to skip.

Keep a bounded commit log for short disconnections. Use a complete snapshot
when the requested history is no longer available. Keep move results for
the session so retries cannot apply twice.

The first release keeps session state in memory. Coordinator process loss
ends the session. Persistent recovery and authority transfer require a
separate design if playtests show that they are necessary.

## Game code

Ship one game inside the host application. Use ordinary functions for
validation, state transitions, and rendering. There is no third-party
module loader or WebAssembly runtime in the first release.

Choose the game before implementing its rules. Prefer a short cooperative
activity that works with two players and becomes more interesting with three.
Small discrete moves make recovery and mobile interaction easier to inspect.

## Transport boundary

The session layer needs peer discovery, authenticated connections, bounded
message delivery, and connection status. It assumes messages can be lost,
duplicated, delayed, or reordered.

BitChat is the proposed integration, not a verified dependency contract.
Pin an upstream revision and test behavior on devices before claiming wire
compatibility. Verify unknown message handling, recipient routing,
authentication, payload limits, and background operation.

Start with direct nearby connections. Add relay support only when a measured
play scenario needs it and the transport can support recovery.

## Existing experiment

| Path | Current role |
|---|---|
| `reference/meshkit/wire.py` | Candidate packet codec; needs upstream comparison |
| `reference/meshkit/relay.py` | Relay policy experiment |
| `reference/meshkit/events.py` | Earlier application envelope |
| `reference/meshkit/ordering.py` | Earlier ordering experiment; does not establish convergence |
| `reference/meshkit/budgets.py` | Outbound metering experiment |
| `reference/runtime/` | Earlier manifest and host experiment |
| `reference/apps/market_sim/` | Historical marketplace simulation |
| `reference/sim/mesh_sim.py` | Scripted demonstration of that simulation |
| `tests/` | Tests for the earlier experiment |

Preserve these files as research material during the design reset. Reuse
code only after testing it against the new contract. The existing passing
tests do not establish delivery, authorization, or convergence guarantees.

## Implementation sequence

1. Choose the game and write one complete session scenario.
2. Implement its rules and coordinator in a local simulation.
3. Add move results, revisions, replay, and snapshot recovery.
4. Inject loss, duplicates, reordering, and participant disconnections.
5. Integrate authenticated nearby transport on real phones.
6. Run complete sessions with two or three people and measure failures.
7. Decide whether the experience merits another iteration.

Before playtesting, set thresholds for completion, setup time, and recovery.
Ask players whether they want another session. That evidence decides the
next product investment.

## Future extraction

A second useful game can reveal which session capabilities belong in a
shared library. Third-party hosting needs separate evidence of demand and
its own security model. Marketplace and payment work remain deferred.
