import sys
from pathlib import Path
import struct
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import messenger
import protocol
from characters import read_string


class MessengerTests(unittest.TestCase):
    CAPTURE = bytes.fromhex('2600020000006500e90300000c004c6f63616c506c617965720025000000030000007a93f095')

    def test_real_registration_and_initial_list(self):
        self.assertEqual(protocol.FrameDecoder().feed(self.CAPTURE), [(2, self.CAPTURE)])
        character = dict(id=1001, nickname='LocalPlayer')
        wire, registration = messenger.bootstrap(self.CAPTURE[8:-4], character)
        self.assertEqual(registration, dict(id=1001, nickname='LocalPlayer', level=37, position=3))
        decoder = protocol.FrameDecoder()
        self.assertEqual(decoder.feed(wire[:5]), [])
        packets = decoder.feed(wire[5:])
        self.assertEqual(len(packets), 1)
        self.assertNotIn(messenger.LOGIN_FAILURE, [struct.unpack_from('<I',p[1],4)[0] for p in packets])
        body = packets[0][1][8:-4]
        self.assertEqual(struct.unpack_from('<I', packets[0][1], 4)[0], 0x650002)
        self.assertEqual(struct.unpack_from('<ii', body), (1, 1))
        name, end = read_string(body, 8)
        self.assertEqual(name, 'Amigos')
        self.assertEqual(struct.unpack_from('<i', body, end)[0], 0)
        self.assertEqual(end + 4, len(body))

    def test_rejects_wrong_character_and_malformed_registration(self):
        body = self.CAPTURE[8:-4]
        for character in (None, dict(id=999, nickname='LocalPlayer'), dict(id=1001, nickname='Different')):
            with self.assertRaises(ValueError):
                messenger.bootstrap(body, character)
        for malformed in (b'', body[:4], body[:-1], body+b'\0', body[:6]+b'\0'*12+body[18:]):
            with self.assertRaises(ValueError):
                messenger.decode_registration(malformed)


if __name__ == '__main__':
    unittest.main()
