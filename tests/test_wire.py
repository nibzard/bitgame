"""Wire codec tests: byte layout against BinaryProtocol.swift, round trips,
compression, padding, freshness, payload caps, capabilities.
"""

import struct
import unittest

from meshkit import wire
from meshkit.events import APP_EVENT


class TestHeaderLayout(unittest.TestCase):
    def test_v1_header_bytes(self):
        """Hand-checked against the layout in BinaryProtocol.swift."""
        pkt = wire.Packet(
            type=0x2D,
            sender_id=b"\x01" * 8,
            timestamp=0x1122334455667788,
            payload=b"hi",
            ttl=7,
        )
        raw = pkt.encode()
        self.assertEqual(raw[0], 1)            # version
        self.assertEqual(raw[1], 0x2D)         # type
        self.assertEqual(raw[2], 7)            # ttl
        self.assertEqual(raw[3:11], bytes.fromhex("1122334455667788"))
        self.assertEqual(raw[11], 0x00)        # no flags
        self.assertEqual(raw[12:14], struct.pack(">H", 2))  # payload len
        self.assertEqual(raw[14:22], b"\x01" * 8)           # sender
        self.assertEqual(raw[22:24], b"hi")
        self.assertEqual(len(raw), 24)

    def test_flags_set_correctly(self):
        pkt = wire.Packet(
            type=0x2D,
            sender_id=b"\x01" * 8,
            timestamp=1,
            payload=b"x",
            recipient_id=b"\x02" * 8,
            signature=b"\x05" * 64,
        )
        raw = pkt.encode()
        self.assertEqual(raw[11], wire.FLAGS_HAS_RECIPIENT | wire.FLAGS_HAS_SIGNATURE)

    def test_v2_header_is_16_bytes_with_4_byte_length(self):
        pkt = wire.Packet(
            type=0x2D, sender_id=b"\x01" * 8, timestamp=1, payload=b"abc", version=2
        )
        raw = pkt.encode()
        self.assertEqual(len(raw), 16 + 8 + 3)
        self.assertEqual(raw[12:16], struct.pack(">I", 3))


class TestRoundTrip(unittest.TestCase):
    def test_v1_v2_round_trip(self):
        for version in (1, 2):
            pkt = wire.Packet(
                type=APP_EVENT,
                sender_id=b"\xab" * 8,
                timestamp=1_700_000_000_123,
                payload=b"\x00\x01payload" * 20,
                ttl=5,
                recipient_id=b"\xcd" * 8,
                signature=b"\xee" * 64,
                version=version,
            )
            decoded = wire.Packet.decode(pkt.encode())
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.type, pkt.type)
            self.assertEqual(decoded.sender_id, pkt.sender_id)
            self.assertEqual(decoded.recipient_id, pkt.recipient_id)
            self.assertEqual(decoded.signature, pkt.signature)
            self.assertEqual(decoded.payload, pkt.payload)
            self.assertEqual(decoded.ttl, pkt.ttl)

    def test_route_v2_only(self):
        pkt = wire.Packet(
            type=APP_EVENT,
            sender_id=b"\x01" * 8,
            timestamp=1,
            payload=b"r",
            version=2,
            route=[b"\x02" * 8, b"\x03" * 8],
        )
        decoded = wire.Packet.decode(pkt.encode())
        self.assertEqual(decoded.route, [b"\x02" * 8, b"\x03" * 8])
        # v1 strips the route
        pkt.version = 1
        decoded = wire.Packet.decode(pkt.encode())
        self.assertIsNone(decoded.route)


class TestCompression(unittest.TestCase):
    def test_compressible_payload_round_trips(self):
        payload = b"meshkit " * 40  # >100 bytes and compressible
        pkt = wire.Packet(type=APP_EVENT, sender_id=b"\x01" * 8, timestamp=1, payload=payload)
        raw = pkt.encode()
        self.assertEqual(raw[11] & wire.FLAGS_COMPRESSED, wire.FLAGS_COMPRESSED)
        self.assertLess(len(raw), 14 + 8 + len(payload))
        self.assertEqual(wire.Packet.decode(raw).payload, payload)

    def test_impossible_ratio_rejected(self):
        # declare a huge original size: the decode-time ratio guard must drop it
        inner = wire.Packet(
            type=APP_EVENT, sender_id=b"\x01" * 8, timestamp=1, payload=b"A" * 400
        )
        raw = bytearray(inner.encode())
        self.assertNotEqual(raw[11] & wire.FLAGS_COMPRESSED, 0)
        # original size sits right after the header (14) + sender (8)
        raw[22:24] = struct.pack(">H", 0xFFFF)  # lie about the original size
        self.assertIsNone(wire.Packet.decode(bytes(raw)))


class TestPayloadLimits(unittest.TestCase):
    def test_unknown_type_cap_128kib(self):
        self.assertEqual(wire.max_payload_bytes(0x2D), 128 * 1024)
        self.assertEqual(wire.max_payload_bytes(0xFE), 128 * 1024)  # unknown stays relayable

    def test_oversize_unknown_payload_dropped(self):
        oversize = b"z" * (128 * 1024 + 1)
        # v1 sender refuses to encode a payload that cannot fit the frame
        pkt = wire.Packet(type=0x2D, sender_id=b"\x01" * 8, timestamp=1, payload=oversize)
        with self.assertRaises(ValueError):
            pkt.encode()
        # v2 encodes it; the receiver drops it at decode time (cap is 128 KiB)
        pkt.version = 2
        self.assertIsNone(wire.Packet.decode(pkt.encode()))


class TestPadding(unittest.TestCase):
    def test_pad_to_bucket(self):
        padded = wire.pad_frame(b"x" * 100)
        self.assertEqual(len(padded), 256)
        self.assertEqual(wire.unpad_frame(padded), b"x" * 100)

    def test_oversize_frame_goes_unpadded(self):
        big = b"x" * 3000
        self.assertEqual(wire.pad_frame(big), big)


class TestSignatureCoverage(unittest.TestCase):
    def test_ttl_excluded_from_signature(self):
        """A relay decrements TTL; the signature bytes must not move."""
        pkt = wire.Packet(
            type=APP_EVENT, sender_id=b"\x01" * 8, timestamp=1, payload=b"sig test"
        )
        a = pkt.encode_for_signing()
        pkt.ttl = 3
        b = pkt.encode_for_signing()
        self.assertEqual(a, b)


class TestFreshnessAndDedup(unittest.TestCase):
    def test_freshness_gate(self):
        pkt = wire.Packet(
            type=APP_EVENT, sender_id=b"\x01" * 8, timestamp=1_700_000_000_000, payload=b"x"
        )
        now = 1_700_000_100_000  # 100 s later
        self.assertTrue(wire.is_fresh(pkt, now_ms=now))
        now = 1_700_000_200_000  # 200 s later: outside the ±120 s gate
        self.assertFalse(wire.is_fresh(pkt, now_ms=now))

    def test_message_id_includes_payload_digest(self):
        base = dict(type=APP_EVENT, sender_id=b"\x01" * 8, timestamp=5, payload=b"a")
        a = wire.Packet(**base)
        b = wire.Packet(**dict(base, payload=b"b"))
        self.assertNotEqual(wire.packet_message_id(a), wire.packet_message_id(b))


class TestCapabilities(unittest.TestCase):
    def test_bit11_round_trip(self):
        raw = wire.encode_capabilities(wire.CAP_MESHAPPS | wire.CAP_BOARD)
        self.assertEqual(wire.decode_capabilities(raw), (1 << 11) | (1 << 4))

    def test_zero_and_high_bits(self):
        self.assertEqual(wire.encode_capabilities(0), b"\x00")
        self.assertEqual(wire.decode_capabilities(wire.encode_capabilities((1 << 40) | 3)), (1 << 40) | 3)


if __name__ == "__main__":
    unittest.main()
