# Wire format reference

This document shows exact bytes. The codec in `reference/meshkit/wire.py`
ports `BinaryProtocol.swift`; the appEvent payload codec in
`reference/meshkit/events.py` implements SPEC section 4.1.

## Packet header (v1)

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+---------------+---------------+---------------+-----------------------+
| version (0x01)| type (0x2D)   | ttl (7)       | timestamp (8B BE)     |
+---------------+---------------+---------------+-----------------------+
| timestamp cont.                                       | flags (1B)    |
+-------------------------------------------------------+---------------+
| payload length (2B BE, v1; 4B BE, v2)                                 |
+-----------------------------------------------------------------------+
| senderID (8B)                                                         |
+-----------------------------------------------------------------------+
| recipientID (8B, only when flags & 0x01)                              |
+-----------------------------------------------------------------------+
| route count (1B) + hops (8B each, only when flags & 0x08, v2+)        |
+-----------------------------------------------------------------------+
| original length (2B/4B, only when flags & 0x04)                       |
+-----------------------------------------------------------------------+
| payload ...                                                           |
+-----------------------------------------------------------------------+
| signature (64B, only when flags & 0x02)                               |
+-----------------------------------------------------------------------+
```

Flag bits: `0x01` has recipient, `0x02` has signature, `0x04` compressed
(zlib), `0x08` has route (v2), `0x10` route-stripping. Signatures cover the
packet with TTL fixed at 0, so relays may decrement TTL freely.

## Worked example: broadcast LIST event

Produced by the reference codec. Sender `11…11`, TTL 7, no recipient, no
signature, payload not compressed (under the 100-byte threshold):

```
full packet (136 bytes):
012d07 00006801a2c31e00 00 0072 1111111111111111
0115696d2e62697467616d652e6d61726b65742d73696d
02044c495354
0310000102030405060708090a0b0c0d0e0f
04080000000000000001
06377b226974656d223a226d7567222c226c697374696e674964223a224c31222c
22707269636553617473223a3530302c22717479223a317d
```

Field walk:

| Bytes | Meaning |
|---|---|
| `01` | version 1 |
| `2d` | type appEvent |
| `07` | TTL 7 |
| `00006801a2c31e00` | timestamp (ms) |
| `00` | flags: none |
| `0072` | payload length 114 |
| `1111111111111111` | sender peer ID |

Payload TLVs:

| Bytes | TLV | Value |
|---|---|---|
| `01 15 …` | appId | `im.bitgame.market-sim` |
| `02 04 4c495354` | eventType | `LIST` |
| `03 10 000102…0f` | eventId | 16 random bytes |
| `04 08 …01` | seq | 1 |
| `06 37 {"item":…}` | body | canonical JSON, 55 bytes |

Mesh dedup key for this packet:
`1111111111111111-114356234952192-45-538a4626`
(sender, timestamp, type, first 4 bytes of the payload digest).

## Relay treatment

A `0x2D` packet is unknown to deployed BitChat relays, so it takes the
broadcast branch of `RelayController.decide`:

| Local degree | TTL limit | Jitter (ms) |
|---|---|---|
| ≥ 6 links | min(incoming, 5) | 80–220 |
| 3–5 links | min(incoming, 6) | 60–150 |
| ≤ 2 links | incoming (full) | 10–40 |

The ingress link is always excluded (split horizon). Broadcast fanout is
subset to ~log2(degree) links at the radio layer; directed traffic (with a
recipient ID) relays on every link with TTL − 1 and 20–60 ms jitter.

## Catalog manifest on the board

A manifest travels as the content of a signed `boardPost` (0x23):

```
meshapp:{"appId":"im.bitgame.market-sim","budget":{…},…}
```

Board limits bound it: content ≤ 512 bytes, lifetime ≤ 7 days, deletion by
signed tombstone. The `meshapp:` prefix separates catalog entries from
human posts.
