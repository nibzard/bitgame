"""BitChat wire codec — reference port.

Ported line-for-line from BitChat `BinaryProtocol.swift` and
`BLEReceivePipeline.swift` so meshkit appEvent (0x2D) packets are
byte-compatible with deployed BitChat relays.

Wire format (v1 header = 14 bytes, v2 = 16 bytes, big-endian):

    | version 1B | type 1B | ttl 1B | timestamp 8B | flags 1B |
    | payloadLen 2B (v1) / 4B (v2) |
    | senderID 8B |
    | recipientID 8B        if flags & 0x01 |
    | routeCount 1B + hops  if flags & 0x08 (v2 only) |
    | originalLen           if flags & 0x04 |
    | payload ...           payloadLen bytes |
    | signature 64B         if flags & 0x02 |

Standard library only.
"""

from __future__ import annotations

import hashlib
import struct
import time
import zlib
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Message types (MessageType.swift)
# ---------------------------------------------------------------------------


class MessageType:
    ANNOUNCE = 0x01
    MESSAGE = 0x02
    LEAVE = 0x03
    COURIER_ENVELOPE = 0x04
    NOISE_HANDSHAKE = 0x10
    NOISE_ENCRYPTED = 0x11
    FRAGMENT = 0x20
    REQUEST_SYNC = 0x21
    FILE_TRANSFER = 0x22
    BOARD_POST = 0x23
    PREKEY_BUNDLE = 0x24
    GROUP_MESSAGE = 0x25
    PING = 0x26
    PONG = 0x27
    NOSTR_CARRIER = 0x28
    VOICE_FRAME = 0x29
    ANNOUNCE_V2 = 0x2C
    # meshkit assignment (SPEC.md section 3.1)
    APP_EVENT = 0x2D


# ---------------------------------------------------------------------------
# PacketPayloadLimits.swift — receive-side ceilings per type
# ---------------------------------------------------------------------------

_FRAGMENT_BYTES = 1280
_CONTROL_BYTES = 4 * 1024
_REQUEST_SYNC_BYTES = 8 * 1024
_ENVELOPE_BYTES = 64 * 1024
_FILE_CEILING = 1024 * 1024  # FileTransferLimits.maxFramedFileBytes
_TWICE_V1_FRAME = 128 * 1024

_KNOWN_LIMITS = {
    MessageType.FILE_TRANSFER: _FILE_CEILING,
    MessageType.NOISE_ENCRYPTED: _FILE_CEILING,
    MessageType.MESSAGE: _TWICE_V1_FRAME,
    MessageType.GROUP_MESSAGE: _TWICE_V1_FRAME,
    MessageType.COURIER_ENVELOPE: _ENVELOPE_BYTES,
    MessageType.NOSTR_CARRIER: _ENVELOPE_BYTES,
    MessageType.REQUEST_SYNC: _REQUEST_SYNC_BYTES,
    MessageType.FRAGMENT: _FRAGMENT_BYTES,
    MessageType.ANNOUNCE: _CONTROL_BYTES,
    MessageType.ANNOUNCE_V2: _CONTROL_BYTES,
    MessageType.LEAVE: _CONTROL_BYTES,
    MessageType.BOARD_POST: _CONTROL_BYTES,
    MessageType.PREKEY_BUNDLE: _CONTROL_BYTES,
    MessageType.VOICE_FRAME: _CONTROL_BYTES,
    MessageType.NOISE_HANDSHAKE: _CONTROL_BYTES,
    MessageType.PING: _CONTROL_BYTES,
    MessageType.PONG: _CONTROL_BYTES,
}

_MAX_DEFLATE_RATIO = 1032
_COMPRESSION_THRESHOLD = 100  # Constants.compressionThresholdBytes


def max_payload_bytes(msg_type: int) -> int:
    """Decoded-payload ceiling for a type; unknown types stay relayable."""
    return _KNOWN_LIMITS.get(msg_type, _TWICE_V1_FRAME)


# ---------------------------------------------------------------------------
# Peer capabilities (PeerCapabilities.swift) — bits 0..10 owned by BitChat
# ---------------------------------------------------------------------------

CAP_PREKEYS = 1 << 0
CAP_WIFI_BULK = 1 << 1
CAP_GATEWAY = 1 << 2
CAP_GROUPS = 1 << 3
CAP_BOARD = 1 << 4
CAP_VOUCH = 1 << 5
CAP_MESH_DIAGNOSTICS = 1 << 6
CAP_BRIDGE = 1 << 7
CAP_PRIVATE_MEDIA = 1 << 8
CAP_PRIVATE_MEDIA_RECEIPTS = 1 << 9
CAP_NON_DESTRUCTIVE_NOISE = 1 << 10
# meshkit assignment (SPEC.md section 3.2)
CAP_MESHAPPS = 1 << 11


def encode_capabilities(raw: int) -> bytes:
    """Little-endian bytes, trailing zero bytes dropped, never empty."""
    if raw == 0:
        return b"\x00"
    out = bytearray()
    value = raw & 0xFFFFFFFFFFFFFFFF
    while value != 0:
        out.append(value & 0xFF)
        value >>= 8
    return bytes(out)


def decode_capabilities(data: bytes) -> int:
    value = 0
    for index, byte in enumerate(data[:8]):
        value |= byte << (8 * index)
    return value


# ---------------------------------------------------------------------------
# Packet
# ---------------------------------------------------------------------------

FLAGS_HAS_RECIPIENT = 0x01
FLAGS_HAS_SIGNATURE = 0x02
FLAGS_COMPRESSED = 0x04
FLAGS_HAS_ROUTE = 0x08
FLAGS_IS_RSR = 0x10

V1_HEADER = 14
V2_HEADER = 16
SENDER_ID_SIZE = 8
RECIPIENT_ID_SIZE = 8
SIGNATURE_SIZE = 64

_PADDING_BUCKETS = (256, 512, 1024, 2048)


@dataclass
class Packet:
    type: int
    sender_id: bytes
    timestamp: int  # ms since epoch
    payload: bytes
    ttl: int = 7
    recipient_id: Optional[bytes] = None
    signature: Optional[bytes] = None
    version: int = 1
    route: Optional[list] = None
    is_rsr: bool = False

    def __post_init__(self) -> None:
        if len(self.sender_id) != SENDER_ID_SIZE:
            raise ValueError("sender_id must be 8 bytes")

    # -- encoding -----------------------------------------------------------

    def encode(self, padding: bool = False) -> bytes:
        version = self.version
        if version not in (1, 2):
            raise ValueError("version must be 1 or 2")

        payload = self.payload
        original_size: Optional[int] = None
        if len(payload) > _COMPRESSION_THRESHOLD:
            # Only compress when the original length fits the frame's length
            # field (BinaryProtocol.swift: "maxRepresentable" guard).
            max_representable = 0xFFFF if version == 1 else 0xFFFFFFFF
            if len(payload) <= max_representable:
                compressed = zlib.compress(payload)
                if len(compressed) < len(payload):
                    original_size = len(payload)
                    payload = compressed

        length_field = 4 if version == 2 else 2
        route = [self._norm_hop(h) for h in (self.route or [])] if version >= 2 else []
        if len(route) > 255:
            raise ValueError("route too long")

        flags = 0
        if self.recipient_id is not None:
            flags |= FLAGS_HAS_RECIPIENT
        if self.signature is not None:
            flags |= FLAGS_HAS_SIGNATURE
        if original_size is not None:
            flags |= FLAGS_COMPRESSED
        if route:
            flags |= FLAGS_HAS_ROUTE
        if self.is_rsr:
            flags |= FLAGS_IS_RSR

        payload_data_size = len(payload) + (length_field if original_size is not None else 0)
        if version == 1 and payload_data_size > 0xFFFF:
            raise ValueError("v1 payload over 64 KiB")

        out = bytearray()
        out.append(version)
        out.append(self.type)
        out.append(self.ttl & 0xFF)
        out += struct.pack(">Q", self.timestamp)
        out.append(flags)
        if version == 2:
            out += struct.pack(">I", payload_data_size)
        else:
            out += struct.pack(">H", payload_data_size)
        out += self._norm_hop(self.sender_id)
        if self.recipient_id is not None:
            out += self._norm_hop(self.recipient_id)
        if route:
            out.append(len(route))
            for hop in route:
                out += hop
        if original_size is not None:
            if version == 2:
                out += struct.pack(">I", original_size)
            else:
                out += struct.pack(">H", original_size)
        out += payload
        if self.signature is not None:
            out += self.signature[:SIGNATURE_SIZE]

        if padding:
            return pad_frame(bytes(out))
        return bytes(out)

    def encode_for_signing(self) -> bytes:
        """Signature covers the packet with signature removed and TTL zeroed.

        Port of BitchatPacket.toBinaryDataForSigning(): relays decrement TTL
        without invalidating the signature.
        """
        unsigned = Packet(
            type=self.type,
            sender_id=self.sender_id,
            timestamp=self.timestamp,
            payload=self.payload,
            ttl=0,
            recipient_id=self.recipient_id,
            signature=None,
            version=self.version,
            route=self.route,
            is_rsr=False,
        )
        return unsigned.encode(padding=False)

    @staticmethod
    def _norm_hop(hop: bytes) -> bytes:
        hop = bytes(hop[:SENDER_ID_SIZE])
        if len(hop) < SENDER_ID_SIZE:
            hop += b"\x00" * (SENDER_ID_SIZE - len(hop))
        return hop

    # -- decoding -----------------------------------------------------------

    @classmethod
    def decode(cls, data: bytes) -> "Packet":
        raw = bytes(data)
        pkt = cls._decode_core(raw)
        if pkt is not None:
            return pkt
        unpadded = unpad_frame(raw)
        if unpadded == raw:
            return None
        return cls._decode_core(unpadded)  # type: ignore[return-value]

    @classmethod
    def _decode_core(cls, raw: bytes) -> Optional["Packet"]:
        if len(raw) < V1_HEADER + SENDER_ID_SIZE:
            return None
        version = raw[0]
        if version not in (1, 2):
            return None
        header_size = V2_HEADER if version == 2 else V1_HEADER
        if len(raw) < header_size + SENDER_ID_SIZE:
            return None

        msg_type = raw[1]
        ttl = raw[2]
        timestamp = struct.unpack(">Q", raw[3:11])[0]
        flags = raw[11]
        length_field = 4 if version == 2 else 2
        offset = header_size

        has_recipient = bool(flags & FLAGS_HAS_RECIPIENT)
        has_signature = bool(flags & FLAGS_HAS_SIGNATURE)
        is_compressed = bool(flags & FLAGS_COMPRESSED)
        has_route = version >= 2 and bool(flags & FLAGS_HAS_ROUTE)
        is_rsr = bool(flags & FLAGS_IS_RSR)

        # The length field sits at bytes 12..(12+lengthField) — inside the
        # header, after version(1) type(1) ttl(1) timestamp(8) flags(1).
        if version == 2:
            (payload_length,) = struct.unpack(">I", raw[12:16])
        else:
            (payload_length,) = struct.unpack(">H", raw[12:14])
        offset = header_size

        cap = max_payload_bytes(msg_type)
        if payload_length > cap + length_field:
            return None  # over budget for this type

        sender_id = raw[offset : offset + SENDER_ID_SIZE]
        offset += SENDER_ID_SIZE

        recipient_id = None
        if has_recipient:
            recipient_id = raw[offset : offset + RECIPIENT_ID_SIZE]
            if len(recipient_id) < RECIPIENT_ID_SIZE:
                return None
            offset += RECIPIENT_ID_SIZE

        route = None
        if has_route:
            count = raw[offset]
            offset += 1
            hops = []
            for _ in range(count):
                hop = raw[offset : offset + SENDER_ID_SIZE]
                if len(hop) < SENDER_ID_SIZE:
                    return None
                hops.append(hop)
                offset += SENDER_ID_SIZE
            route = hops

        if is_compressed:
            if payload_length < length_field:
                return None
            if version == 2:
                (original_size,) = struct.unpack(">I", raw[offset : offset + 4])
            else:
                (original_size,) = struct.unpack(">H", raw[offset : offset + 2])
            offset += length_field
            if original_size > cap:
                return None
            compressed_size = payload_length - length_field
            if compressed_size <= 0 or offset + compressed_size > len(raw):
                return None
            compressed = raw[offset : offset + compressed_size]
            if original_size > compressed_size * _MAX_DEFLATE_RATIO:
                return None  # impossible ratio, reject before allocating
            try:
                payload = zlib.decompress(compressed)
            except zlib.error:
                return None
            if len(payload) != original_size:
                return None
        else:
            if payload_length > cap:
                return None
            if offset + payload_length > len(raw):
                return None
            payload = raw[offset : offset + payload_length]

        offset += payload_length - (length_field if is_compressed else 0)
        # offset now points just past the on-wire payload block.

        signature = None
        if has_signature:
            signature = raw[offset : offset + SIGNATURE_SIZE]
            if len(signature) < SIGNATURE_SIZE:
                return None

        return Packet(
            type=msg_type,
            sender_id=sender_id,
            timestamp=timestamp,
            payload=payload,
            ttl=ttl,
            recipient_id=recipient_id,
            signature=signature,
            version=version,
            route=route,
            is_rsr=is_rsr,
        )


# ---------------------------------------------------------------------------
# Padding (MessagePadding.swift): PKCS#7-style, buckets 256/512/1024/2048.
# A frame needing more than 255 bytes of pad goes out unpadded.
# ---------------------------------------------------------------------------


def pad_frame(data: bytes) -> bytes:
    target = next((b for b in _PADDING_BUCKETS if len(data) <= b), None)
    if target is None or target - len(data) > 255:
        return data
    pad_len = target - len(data)
    return data + bytes([pad_len]) * pad_len


def unpad_frame(data: bytes) -> bytes:
    if not data:
        return data
    pad_len = data[-1]
    if pad_len == 0 or pad_len > 255 or pad_len > len(data):
        return data
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        return data
    return data[:-pad_len]


# ---------------------------------------------------------------------------
# Dedup identity (BLEReceivePipeline.context)
# ---------------------------------------------------------------------------


def packet_message_id(pkt: Packet) -> str:
    digest = hashlib.sha256(pkt.payload).hexdigest()[:8]
    return f"{pkt.sender_id.hex()}-{pkt.timestamp}-{pkt.type}-{digest}"


def fresh_now_ms() -> int:
    return int(time.time() * 1000)


def is_fresh(pkt: Packet, now_ms: Optional[int] = None, max_skew_ms: int = 120_000) -> bool:
    """BLEIngressPacketGuard freshness gate."""
    now = now_ms if now_ms is not None else fresh_now_ms()
    return abs(now - pkt.timestamp) <= max_skew_ms
