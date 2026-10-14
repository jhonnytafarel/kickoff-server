import sys
from pathlib import Path
import struct
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import protocol


class ProtocolTests(unittest.TestCase):
    def test_real_client_front_packet(self):
        packet = bytes.fromhex('1400020002000200000000001700000019e15322')
        self.assertEqual(protocol.checksum(packet[:-4]), 0x2253e119)
        self.assertEqual(protocol.FrameDecoder().feed(packet), [(2, packet)])

    def test_fragmented_and_combined_stream(self):
        packet = protocol.front_ack()
        decoder = protocol.FrameDecoder()
        self.assertEqual(decoder.feed(packet[:3]), [])
        self.assertEqual(decoder.feed(packet[3:] + bytes.fromhex('04000700')), [(2, packet), (7, bytes.fromhex('04000700'))])
        self.assertEqual(struct.unpack_from('<H', packet)[0], len(packet))

    def test_corrupt_checksum_is_rejected(self):
        packet = bytearray(protocol.version_ack())
        packet[8] ^= 1
        with self.assertRaises(ValueError):
            protocol.FrameDecoder().feed(packet)
