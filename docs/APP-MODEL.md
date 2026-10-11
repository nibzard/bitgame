# Game model

**Status:** proposed interface for the first game.

Ship one cooperative game with the application. Choose its rules before
implementing the interface. The existing market simulation does not implement
this model.

## Responsibilities

The game defines legal moves, state transitions, completion, and presentation.
The session layer defines participants, authority, revisions, recovery, and
message limits. The transport adapter owns connections and packet delivery.

The coordinator is the only authority that accepts moves. Participants
render committed state and show unconfirmed requests as pending.

## Suggested interface

```text
initial_state(seed, roster) -> state
validate_move(state, participant, input) -> valid | rejection
apply_move(state, participant, input) -> new_state
render_state(state, viewer) -> view
```

These signatures describe responsibilities, not a fixed binary interface.
Keep validation and application within one serialized coordinator operation.
Rejected input must leave state unchanged.

Rules use explicit state and input. They do not read the network, local
clock, or ambient randomness. The coordinator records the initial seed.
If timed rules are needed, their transitions enter the ordered commit log.

## Participant behavior

1. Join and obtain the current snapshot.
2. Submit one move with a unique identifier and expected revision.
3. Show that move as pending until its recorded result arrives.
4. Apply consecutive commits or request missing state.
5. On rejection, show the reason and let the player choose again.

Disable move submission while reconnecting. After recovery, resolve an
uncertain move through its original identifier before allowing a replacement.

## State and recovery

Use one versioned serialization for snapshots. Bound its encoded size and
validate it before installation. Snapshots include the session identifier
and revision; subsequent commits start at the next revision.

A hash can compare state at the same revision. It cannot establish move
order, authorization, or fairness.

## Deferred runtime work

No downloadable modules, signed manifests, declarative rendering engine,
or WebAssembly sandbox are required for the first game. Reconsider a shared
runtime when a second game demonstrates a concrete need.
