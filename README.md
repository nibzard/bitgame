# bitgame

A small cooperative game for two or three people nearby. Players join a
shared session over Bluetooth, without an internet connection or accounts.
One player's phone coordinates the session.

The first goal is simple: people can start a game, finish it together, and
want to play again. The game rules are still to be chosen.

## Product scope

- One game shipped with the host application.
- Two or three players who deliberately join the same session.
- One coordinator that validates moves and publishes the shared state.
- Reconnection after a player briefly loses contact.
- A clear pause when the coordinator is unavailable.

BitChat is the proposed transport. Direct nearby connections are the first
integration target. Relay support must be verified on actual devices before
it becomes a product promise.

## Status

Design reset. The repository contains an earlier platform experiment,
not an implementation of the game described above.

- `SPEC.md` defines the proposed game session contract.
- `ARCHITECTURE.md` describes the implementation plan and its limits.
- `docs/APP-MODEL.md` describes the proposed game interface.
- `docs/WIRE.md` records the existing codec and transport questions.
- `docs/MARKETPLACE.md` explains why the marketplace is deferred.
- `reference/` contains the earlier Python platform and market simulation.
- `tests/` exercises that experiment. It does not establish conformance
  with the new session contract.

The existing ordering buffer does not guarantee replica convergence.
The experiment also has known gaps in admission limits, event retention,
and input validation. It is useful for investigation, not session authority.

## Run the existing experiment

From the repository root:

```bash
python3 reference/sim/mesh_sim.py
python3 -m unittest discover -s tests -t .
```

The demo runs a scripted marketplace exchange. Matching state hashes in
that scenario do not prove convergence under arbitrary delivery orders.

## First milestone

Choose one cooperative game whose moves fit in small messages. Build a
complete session for two or three players. Verify joining, playing,
reconnecting, and finishing on real phones without internet access.

Measure whether groups finish and choose to play again. Record setup time,
failed joins, and recovery after disconnection. Set success thresholds
before the playtest.

## Later decisions

Extract a reusable library when a second game needs the same capabilities.
Consider third-party modules only after there is evidence that authors and
players need them. Payments, escrow, reputation, and an app marketplace are
outside the first release.

## License

The reference code uses the Unlicense. See `LICENSE`.
