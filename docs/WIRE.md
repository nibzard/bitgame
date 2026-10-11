# Transport notes

**Status:** existing codec experiment and requirements for future integration.

## Existing code

`reference/meshkit/wire.py` contains a Python packet codec derived from the
earlier BitChat investigation. `reference/meshkit/events.py` defines the
old application event envelope. These files remain research material.

The experiment uses packet type `0x2D` and capability bit 11. Treat these
as local experimental values. This repository does not establish an upstream
allocation or compatibility with installed BitChat versions.

The codec handles versioned headers, optional recipients, compression,
signature bytes, and padding. Carrying signature bytes does not verify the
sender. The current session receive path does not implement authentication.

The relay adapter sends unknown application packets through its broadcast
policy. A recipient field alone does not select the directed encrypted
policy in this implementation.

## Required session messages

The new session protocol needs these logical messages:

| Message | Purpose |
|---|---|
| Join request and result | Establish approved membership |
| Move | Request a transition at an expected revision |
| Move result | Report acceptance or rejection for a move identifier |
| Commit | Publish a transition with a coordinator revision |
| Recovery request | Request commits after a known revision |
| Snapshot transfer | Replace state when retained history is insufficient |
| Session status | Report readiness, pause, completion, or closure |

Every message binds to its session and authenticated source. The concrete
encoding is not assigned yet. Do not reuse the old per-sender counter as
an authoritative session revision.

## Integration checks

1. Pin the upstream source revision and identify supported mobile versions.
2. Verify discovery and authenticated peer connections on actual devices.
3. Check routing for each selected message type and recipient mode.
4. Compare codec output against independent upstream packet fixtures.
5. Measure payload limits, overhead, fragmentation, and background behavior.
6. Test bounded snapshot transfer with interrupted connections.
7. Verify recovery under loss, duplication, and reordered delivery.

Enforce decoded size limits and bound decompression before allocating large
buffers. Validate optional fields before reading them. A malformed packet
must produce a rejection rather than an uncaught exception.

The session protocol assumes best-effort delivery. Retries, recorded move
results, revision recovery, and snapshots provide application-level recovery.
No catalog or manifest distribution protocol is required for the first game.
