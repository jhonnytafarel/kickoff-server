import math
import struct
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'server'))
import bots
import protocol
import relay
from characters import Store, read_string
from rooms import RoomRegistry, decode_score


class BotTests(unittest.TestCase):
    def test_ephemeral_roster_never_changes_account_and_uses_free_slot(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp)/'bot.sqlite3')
            char = store.create(bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())[8:-4])
            body = bytearray(bytes.fromhex((ROOT/'tests/fixtures/create-free-room.hex').read_text())[8:-4])
            _, end = read_string(body, 8)
            struct.pack_into('<i', body, end, 2)
            rooms = RoomRegistry(practice_bots=True)
            room = rooms.create(body, char, 'host')
            bot = room['bot']['character']
            self.assertEqual(struct.unpack_from('<i', bot['payload'])[0], bots.BOT_ID)
            self.assertEqual(read_string(bot['payload'], 4)[0], 'BotTreino')
            self.assertEqual(bot['payload'][bot['base_end']:], char['payload'][char['base_end']:])
            self.assertEqual(protocol.FrameDecoder().feed(protocol.free_player_join_cast(room, bot, True))[0][0], 2)
            rooms.change_team(room)
            self.assertEqual((room['team'], room['slot']), (2, 1))
            with self.assertRaises(ValueError): rooms.set_slot(room, 2, 0, 1)
            rooms.remove_owner('host')
            self.assertEqual(store.get(), char)
            self.assertEqual(Store(store.path).get(), char)

    def packet(self, message_id, body):
        header = bytearray(48)
        struct.pack_into('<ii', header, 0, 1001, bots.BOT_ID)
        header[11] = 1
        message = struct.pack('<I', message_id) + body
        struct.pack_into('<I', header, 32, protocol.checksum(message))
        struct.pack_into('<H', header, 46, len(message))
        return relay.packet(3, header + message)

    def test_virtual_peer_routing_crc_sync_and_no_confirmation_loop(self):
        peer = bots.Opponent(1001)
        transport = relay.Relay()
        transport.bots[bots.BOT_ID] = peer
        address = ('192.168.56.1', 20000)
        request = bytes.fromhex((ROOT/'tests/fixtures/bot-confirmation.hex').read_text())
        responses, _ = transport.handle(request, address)
        self.assertEqual(len(responses), 1)  # real confirmation is non-reliable
        response, target = responses[0]
        self.assertEqual(target, address)
        self.assertEqual(relay.decode(response)[0], 3)
        self.assertEqual(struct.unpack_from('<ii', response, 4), (bots.BOT_ID, 1001))
        self.assertEqual(struct.unpack_from('<Iiii', response, 52), (0x10001, 2, 1001, bots.BOT_ID))
        import netstate
        self.assertEqual(response[68:-4], netstate.relay_endpoint())
        self.assertEqual(struct.unpack_from('<H',response,50)[0],len(response)-56)
        self.assertEqual(struct.unpack_from('<I',response,36)[0],protocol.checksum(response[52:-4]))
        self.assertEqual(peer.phase,'connected')
        self.assertEqual(peer.handle(self.packet(0x10001, struct.pack('<iii', 2,bots.BOT_ID,1001))), [])
        for message_id, payload, expected in [
            (0x10004, struct.pack('<d', 13.5), struct.pack('<dd', 13.5,13.5)),
            (0x10006, struct.pack('<id', 8,13.5), struct.pack('<iid',8,bots.BOT_ID,13.5)),
            (0x10008, struct.pack('<i',1001), struct.pack('<i',bots.BOT_ID)),
            (0x10009, struct.pack('<i',1001), struct.pack('<i',bots.BOT_ID)),
            (0x1000a, struct.pack('<idd',1001,13.5,14), struct.pack('<idd',bots.BOT_ID,13.5,14))]:
            response = peer.handle(self.packet(message_id,payload))[0]
            self.assertEqual(response[56:-4],expected)
            self.assertEqual(relay.decode(response)[0], 3)
        self.assertEqual(peer.phase,'synchronized')

    def test_encoded_capture_is_not_dispatched_as_plain_message(self):
        transport = relay.Relay()
        transport.bots[bots.BOT_ID] = bots.Opponent(1001)
        data = bytes.fromhex((ROOT/'tests/fixtures/bot-encoded-stream.hex').read_text())
        replies, event = transport.handle(data, ('192.168.56.1',20000))
        self.assertEqual(len(replies),1)  # transport ACK only
        self.assertEqual(relay.decode(replies[0][0])[0],relay.KIND_ACK)
        self.assertEqual(transport.bots[bots.BOT_ID].phase,'waiting')
        self.assertEqual(bots.decode_stream(data[16:]),(0x10037,bytes.fromhex('00000f00ffffffff')))

    def test_real_encrypted_loading_and_time_completion(self):
        transport = relay.Relay()
        peer = transport.bots[bots.BOT_ID] = bots.Opponent(1001)
        for mid in (0x10008,0x10009):
            data = bytes.fromhex((ROOT/f'tests/fixtures/bot-sync-{mid:x}.hex').read_text())
            responses, _ = transport.handle(data,('192.168.56.1',20000))
            self.assertEqual(len(responses),2)
            reply = responses[1][0]
            self.assertEqual(bots.decode_stream(reply),(mid,struct.pack('<i',bots.BOT_ID)))
            damaged = bytearray(data)
            damaged[-5] ^= 1
            with self.assertRaises(ValueError): peer.handle(damaged)
        self.assertEqual(peer.phase,'loading')

    def test_real_final_sync_releases_bot_and_retries_do_not_regress_phase(self):
        peer = bots.Opponent(1001)
        transport = relay.Relay()
        transport.bots[bots.BOT_ID] = peer
        address = ('192.168.56.1',20000)
        for mid in (0x10008,0x10009,0x1000a,0x1000c):
            raw = bytes.fromhex((ROOT/f'tests/fixtures/bot-sync-{mid:x}.hex').read_text())
            responses, event = transport.handle(raw,address)
            self.assertEqual(len(responses),2)
            response_mid, payload = bots.decode_stream(responses[-1][0])
            self.assertEqual(response_mid,mid)
            self.assertEqual(struct.unpack_from('<i',payload)[0],bots.BOT_ID)
            self.assertIn(f'message=0x{mid:x}',event)
        self.assertEqual(peer.phase,'playing')
        for mid in (0x10008,0x1000a,0x1000c):
            raw = bytes.fromhex((ROOT/f'tests/fixtures/bot-sync-{mid:x}.hex').read_text())
            transport.handle(raw,address)
            self.assertEqual(peer.phase,'playing')
        self.assertEqual(peer.handle(self.packet(0x1000c,struct.pack('<i',1234))),[])

    def test_quit_confirms_bot_completion_and_repeated_quit_is_safe(self):
        peer = bots.Opponent(1001)
        peer.phase = 'playing'
        transport = relay.Relay()
        transport.bots[bots.BOT_ID] = peer
        for _ in range(2):
            responses, _ = transport.handle(self.packet(0x1000d,struct.pack('<I',1001)),
                                           ('192.168.56.1',20000))
            self.assertEqual(len(responses),2)
            self.assertEqual(bots.decode_stream(responses[1][0]),(0x1000d,struct.pack('<I',bots.BOT_ID)))
            self.assertEqual(peer.phase,'finished')
            self.assertIsNone(peer.decide(1,(0,0,0),(10,0,10)))
        self.assertEqual(peer.handle(self.packet(0x1000d,struct.pack('<I',1234))),[])
        self.assertEqual(peer.handle(self.packet(0x1000d,b'')),[])

    def test_decisions_reject_invalid_time_and_stop_outside_play(self):
        peer = bots.Opponent(1001)
        self.assertIsNone(peer.decide(1,(0,0,0),(10,0,10)))
        peer.phase = 'playing'
        action, position, direction = peer.decide(1,(0,0,0),(10,0,10))
        self.assertEqual(action,'move')
        self.assertAlmostEqual(math.hypot(direction[0],direction[2]),1)
        self.assertIsNone(peer.decide(0,(0,0,0),(10,0,10)))
        self.assertEqual(peer.decide(2,(0,0,0),(1,0,1))[0],'stop')
        with self.assertRaises(ValueError): peer.decide(float('nan'),(0,0,0),(1,0,1))

    def test_result_accepts_only_bot_registered_in_this_room(self):
        raw = bytes.fromhex((ROOT/'tests/fixtures/free-score-quit.hex').read_text())[8:-4]
        extra = bytearray(raw[8:146])
        struct.pack_into('<i',extra,0,bots.BOT_ID)
        body = struct.pack('<ii',1,4)+raw[8:]+extra
        with self.assertRaises(ValueError): decode_score(body,1001)
        _, records = decode_score(body,1001,[bots.BOT_ID])
        self.assertEqual(len(records),4)


if __name__ == '__main__': unittest.main()
