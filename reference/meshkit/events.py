"""AppEvent TLV codec — the meshkit payload format (SPEC.md section 4.1).

    | 0x01 appId     ≤64B  | 0x02 eventType ≤16B | 0x03 eventId 16B |
    | 0x04 seq       8B BE | 0x05 flags 1B       | 0x06 body ≤160B |
    | 0x07 stateRoot 32B   |

`seq` is the sender's per-app counter, contiguous from 1. The apply order
key is (seq, sender): intrinsic to the event, identical on every replica.

Total payload ≤ 200 bytes so one event fits a single 469-byte fragment.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Optional

APP_EVENT = 0x2D  # MessageType value from wire.py, repeated for convenience

MAX_APP_ID = 64
MAX_EVENT_TYPE = 16
MAX_BODY = 160
MAX_PAYLOAD = 200

TLV_APP_ID = 0x01
TLV_EVENT_TYPE = 0x02
TLV_EVENT_ID = 0x03
TLV_SEQ = 0x04
TLV_FLAGS = 0x05
TLV_BODY = 0x06
TLV_STATE_ROOT = 0x07

FLAG_ACK_REQUESTED = 0x01


@dataclass
class AppEvent:
    app_id: str
    event_type: str
    event_id: bytes  # 16 bytes random
    seq: int
    sender: bytes  # 8-byte peer ID, filled by the host from the packet
    flags: int = 0
    body: bytes = b""
    state_root: Optional[bytes] = None

    def encode(self) -> bytes:
        if len(self.app_id.encode()) > MAX_APP_ID:
            raise ValueError("appId over 64 bytes")
        if len(self.event_type.encode()) > MAX_EVENT_TYPE:
            raise ValueError("eventType over 16 bytes")
        if len(self.event_id) != 16:
            raise ValueError("eventId must be 16 bytes")
        if not 1 <= self.seq < 2**64:
            raise ValueError("seq must be a positive u64")
        if len(self.body) > MAX_BODY:
            raise ValueError("body over 160 bytes")
        if self.state_root is not None and len(self.state_root) != 32:
            raise ValueError("stateRoot must be 32 bytes")

        out = bytearray()
        out += _tlv(TLV_APP_ID, self.app_id.encode())
        out += _tlv(TLV_EVENT_TYPE, self.event_type.encode())
        out += _tlv(TLV_EVENT_ID, self.event_id)
        out += _tlv(TLV_SEQ, struct.pack(">Q", self.seq))
        if self.flags:
            out += _tlv(TLV_FLAGS, bytes([self.flags]))
        if self.body:
            out += _tlv(TLV_BODY, self.body)
        if self.state_root is not None:
            out += _tlv(TLV_STATE_ROOT, self.state_root)
        if len(out) > MAX_PAYLOAD:
            raise ValueError("encoded payload over 200 bytes")
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> Optional["AppEvent"]:
        app_id = event_type = event_id = seq = None
        flags = 0
        body = b""
        state_root = None
        offset = 0
        while offset + 2 <= len(data):
            t = data[offset]
            length = data[offset + 1]
            offset += 2
            if offset + length > len(data):
                return None
            value = data[offset : offset + length]
            offset += length
            if t == TLV_APP_ID and app_id is None:
                app_id = value.decode(errors="strict") if length else None
            elif t == TLV_EVENT_TYPE and event_type is None:
                event_type = value.decode(errors="strict") if length else None
            elif t == TLV_EVENT_ID and event_id is None:
                event_id = value if length == 16 else None
            elif t == TLV_SEQ and seq is None:
                seq = struct.unpack(">Q", value)[0] if length == 8 else None
            elif t == TLV_FLAGS and length == 1:
                flags = value[0]
            elif t == TLV_BODY and not body:
                body = value
            elif t == TLV_STATE_ROOT and state_root is None and length == 32:
                state_root = value
            # unknown TLVs are skipped (forward compatibility)
        if app_id is None or event_type is None or event_id is None or seq is None:
            return None
        return cls(
            app_id=app_id,
            event_type=event_type,
            event_id=event_id,
            seq=seq,
            sender=b"",
            flags=flags,
            body=body,
            state_root=state_root,
        )

    def body_json(self) -> dict:
        return json.loads(self.body.decode()) if self.body else {}


def _tlv(t: int, value: bytes) -> bytes:
    if len(value) > 255:
        raise ValueError("TLV value over 255 bytes")
    return bytes([t, len(value)]) + value


def canonical_json(obj) -> bytes:
    """Canonical form for signed/hashed content: sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
