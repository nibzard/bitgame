# bitgame session specification

**Version:** 0.2 draft
**Status:** proposed behavior; not implemented by the existing reference code

## 1. Purpose

Support one cooperative game for two or three nearby players. The group
starts and completes a session without internet access or accounts.

The first release ships the game with the application. It has no module
catalog, payment system, or general application runtime.

The exact game rules remain an open product decision. Choose rules that
support short sessions, small moves, and pauses during disconnection.

## 2. Terms and authority

- **Player:** a person participating through a phone.
- **Coordinator:** the phone that creates the session and owns its state.
- **Move:** a player's request to change the game state.
- **Commit:** an accepted move with a revision assigned by the coordinator.
- **Revision:** a counter that increases by one for each accepted move.
- **Snapshot:** the complete game state at a particular revision.

The coordinator validates moves and decides their order. Other phones
submit moves and display committed state. They do not independently decide
which competing move wins.

The group trusts the coordinator to run the game. This design does not
provide outcomes that resist a dishonest coordinator.

## 3. Session lifecycle

1. A player creates a session with a fresh session identifier.
2. Nearby players discover it and request to join.
3. The coordinator approves players and assigns stable participant identifiers.
4. Each joining phone receives the rules version, roster, and a snapshot.
5. The coordinator starts the game after the group is ready.
6. Players submit moves until the game finishes or the group ends it.

A session has at most three players, including the coordinator. A join
control must make it clear which group the player is joining. Its exact
presentation belongs to the first device prototype.

Session messages bind to the session identifier and rules version. A phone
rejects messages for another session or incompatible rules.

## 4. Move processing

Each move contains the session identifier, participant identifier, unique
move identifier, expected revision, and game-specific input.

The coordinator processes requests serially:

1. Authenticate the participant and check session membership.
2. Look up the move identifier and return any recorded result.
3. Check message limits, expected revision, and game rules.
4. Reject an invalid or stale move with a reason and the current revision.
5. Apply an accepted move and assign the next revision.
6. Record its result and publish the commit to all participants.

A move identifier is scoped to its participant and session. Reusing an
identifier with different input is rejected. A retry keeps the original
identifier and input. A rejected request does not consume a revision.

A player shows a move as pending until the coordinator returns its result.
Sending a packet is not evidence that a move was accepted. After a stale
move rejection, refresh the state and let the player choose a new move.

## 5. Replication and recovery

Delivery is best effort. Messages can be lost, duplicated, delayed, or
received in a different order. Recovery belongs to the session protocol.

A participant applies commits in consecutive revision order. It ignores
already applied revisions. A conflicting commit at an existing revision
is a protocol error: pause and request an authenticated snapshot.

When a revision is missing, request commits after the last applied revision.
Use bounded retries. If the coordinator no longer retains those commits,
request a snapshot instead. Do not apply later commits across a gap.

A snapshot includes the session identifier, rules version, roster,
revision, and complete state. Verify its source and limits before replacing
local state. Apply subsequent commits from the next revision.

State hashes can detect a mismatch at the same revision. They do not prove
fairness or replace missing history. Define one state serialization before
using hashes across implementations.

## 6. Disconnection and finality

A player who loses contact sees a reconnecting state. Move submission is
disabled until the phone restores a current snapshot or commit sequence.
The coordinator pauses play when a required player is disconnected.

An accepted move remains accepted for the lifetime of the coordinator's
session. A reconnecting participant retrieves the recorded result of an
uncertain move before submitting a replacement.

The first release has no coordinator election or automatic handover.
When the coordinator is unavailable, play pauses. If its session state is
lost, the session ends and the group starts a new one. Restart recovery is
outside the first release; the interface must state this limitation.

A timeout indicates loss of contact. It does not authorize another phone
to create commits for the same session.

## 7. Identity and message protection

Use authenticated transport sessions where the integration supports them.
Bind participant identifiers to authenticated peers during joining. A
claimed sender identifier in a packet is not sufficient authentication.

Participants accept commits and snapshots only from the bound coordinator.
The coordinator accepts moves only from admitted participants. Reject
messages from previous sessions, even if a participant identifier matches.

The exact handshake and transport API must be verified during integration.
Until then, authentication is a requirement rather than an implemented feature.

## 8. Resource limits

These are initial application limits to validate on devices:

| Resource | Initial limit |
|---|---|
| Players per session | 3 |
| Encoded move or commit | 200 bytes |
| Encoded snapshot | 16 KiB |
| Retained commits | 128 |
| Move results retained per participant | 256 |
| Pending move per participant | 1 |
| Accepted moves per participant | 2 per second, burst of 2 |
| Session duration | 20 minutes |

Reject oversized input before parsing game data. Bound snapshot assembly,
queues, retries, and decompression. Include the entire application envelope
when checking message sizes. Transport overhead must also be measured.

Keep accepted and rejected move results for the session. End the session
with a clear limit message before its result capacity is exhausted. Do not
evict results and then treat an old retry as a new move.

Snapshots need a bounded transfer protocol; they do not fit in a move
packet. Define transfer identifiers, chunk limits, integrity checks, and
expiry during transport integration.

## 9. Game interface

Game rules are deterministic for a given state and accepted move. The
coordinator supplies any initial random seed in the session state.

Game logic does not access the radio or wall clock. If a game needs a timed
transition, the coordinator records that transition as a commit. Local
clocks may animate the interface but do not decide outcomes.

Malformed input must return a rejection without changing state. Rendering
must not change game state. See `docs/APP-MODEL.md`.

## 10. Acceptance checks

The first playable release must demonstrate:

- Two or three people join and finish a game without internet access.
- Duplicate moves produce one state change and the same recorded result.
- Competing moves receive an explicit acceptance or rejection.
- Lost and reordered commits recover through replay or a snapshot.
- A disconnected player reconnects without losing accepted moves.
- Coordinator loss pauses play without creating a second authority.
- Malformed, oversized, and unauthorized input cannot change game state.

Test message loss, duplication, reordering, and disconnection in simulation.
Repeat the complete session on real phones. Record setup failures and ask
whether players want another session.

## 11. Deferred work

General application hosting, third-party modules, catalogs, payments,
escrow, reputation, coordinator handover, and recovery after coordinator
restart are outside this release.

The existing `reference/` code implements an earlier experiment. Its wire
values and tests are not a completed implementation of this specification.
