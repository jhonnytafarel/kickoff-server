import sys
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'server'))
from characters import Store, read_string
from rooms import RoomRegistry, decode_create, decode_score
import protocol


class RoomTests(unittest.TestCase):
    def test_real_match_score_retains_goals_and_exact_ack_layout(self):
        raw = bytes.fromhex((ROOT/'tests/fixtures/free-score-quit.hex').read_text())
        division, records = decode_score(raw[8:-4], 1001)
        self.assertEqual([struct.unpack_from('<i', r)[0] for r in records], [1001, 3, 4])
        self.assertEqual(struct.unpack_from('<hh', records[0], 30), (7, 11))
        ack = protocol.free_score_ack(division, records)
        self.assertEqual(protocol.FrameDecoder().feed(ack), [(2, ack)])
        self.assertEqual(struct.unpack_from('<iiBii', ack, 8), (-1, 1, 0, 0, 3))
        self.assertEqual(ack[25:-4], raw[16:-4])
        for length in range(len(raw[8:-4])):
            with self.assertRaises(ValueError):
                decode_score(raw[8:-4][:length], 1001)
        for offset, replacement in [(4, struct.pack('<i', 4)),
                                    (8+138, struct.pack('<i', 1001)),
                                    (8, struct.pack('<i', 9999)),
                                    (8+26, b'\x01'),
                                    (8+8, struct.pack('<f', float('nan')))]:
            body = bytearray(raw[8:-4])
            body[offset:offset+len(replacement)] = replacement
            with self.assertRaises(ValueError):
                decode_score(body, 1001)

    def setUp(self):
        self.request = bytes.fromhex((ROOT/'tests/fixtures/create-free-room.hex').read_text())
        self.body = self.request[8:-4]

    def test_captured_request_and_truncation(self):
        room = decode_create(self.body)
        self.assertEqual((room['name'],room['mode'],room['map_id'],room['ball_id'],room['match_count']),
                         ('Unstoppable Kickoff!',3,12,6,3))
        for length in range(len(self.body)):
            with self.assertRaises(ValueError):
                decode_create(self.body[:length])
        with self.assertRaises(ValueError):
            decode_create(self.body+b'\0')

    def test_room_wire_reads_exactly_six_slots_and_extra(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp)/'room.sqlite3')
            creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
            character = store.create(creation[8:-4])
            registry = RoomRegistry()
            room = registry.create(self.body, character, 'owner')
            raw = protocol.free_create_ack(room)
            self.assertEqual(protocol.FrameDecoder().feed(raw), [(2,raw)])
            self.assertEqual(struct.unpack_from('<Iii',raw,4),(0x30003,-1,1))
            name,offset = read_string(raw,16)
            self.assertEqual(name,'Unstoppable Kickoff!')
            self.assertEqual(struct.unpack_from('<ihhiiiiBBB',raw,offset),(1,1,6,3,1,3,0,0,0,0))
            offset += 27
            for slot in range(6):
                nickname, offset = read_string(raw,offset)
                team,position,level = struct.unpack_from('<iii',raw,offset)
                offset += 12
                self.assertEqual(nickname,character['nickname'] if slot == 0 else '')
                self.assertEqual(team,1 if slot == 0 else 7)
                if slot: self.assertEqual((position,level),(1,0))
            self.assertEqual(struct.unpack_from('<iii',raw,offset),(1,99,character['id']))
            password,offset = read_string(raw,offset+12)
            self.assertEqual(password,'')
            self.assertEqual(struct.unpack_from('<ii',raw,offset),(12,6))
            # NAT endpoint: the UDP relay on the client's own adapter address.
            import netstate
            self.assertEqual(raw[offset+8:-4],netstate.relay_endpoint())
            self.assertNotEqual(raw[offset+8:-4][4:],(20000).to_bytes(2,'big'))
            self.assertEqual(registry.list(1,99,0),[room])
            self.assertEqual(registry.list(1,5,0),[])
            registry.remove_owner('owner')
            self.assertEqual(registry.list(1,99,0),[])
            self.assertEqual(store.get()['payload'],character['payload'])

    def test_host_join_has_complete_current_appearance(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp)/'avatar.sqlite3')
            creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
            character = store.create(creation[8:-4])
            room = RoomRegistry().create(self.body,character,'host')
            raw = protocol.free_player_join_cast(room,character)
            self.assertEqual(protocol.FrameDecoder().feed(raw),[(2,raw)])
            self.assertEqual(struct.unpack_from('<Iiiii',raw,4),(0x30008,1,1,0,0))
            # Five 6-byte endpoints, server/transport shorts, two network ints.
            self.assertEqual(len(raw[24:66]),42)
            self.assertEqual(struct.unpack_from('<ii',raw,66),(0x1044,0))
            self.assertEqual(raw[74:74+len(character['payload'])],character['payload'])
            self.assertEqual(len(raw[74+len(character['payload']):-4]),46)

    def test_loading_cast_clears_all_requested_player_sections(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp)/'loading.sqlite3')
            creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
            character = store.create(creation[8:-4])
            # Captured loading request: division=1, sections=0x78, one ID.
            request = bytes.fromhex('1c00020022000300010000007800000001000000e9030000b55b5a80')
            division, sections, count, kicker = struct.unpack_from('<iiii', request, 8)
            self.assertEqual((count,kicker),(1,character['id']))
            raw = protocol.free_kicker_info_cast(division,character,sections)
            self.assertEqual(protocol.FrameDecoder().feed(raw),[(2,raw)])
            self.assertEqual(struct.unpack_from('<Iiii',raw,4),(0x30024,1,kicker,0x78))
            self.assertEqual(raw[20:64],character.get('stats') or bytes(44))
            locker = protocol.skill_locker_record(character)
            self.assertEqual(raw[64:64+len(locker)],locker)
            self.assertEqual(raw[64+len(locker):-4],bytes(232))

    def test_match_start_references_existing_goalkeeper_catalog(self):
        import xml.etree.ElementTree as ET
        from resource_catalog import RESOURCE
        valid = set(RESOURCE['goalkeepers'])
        room = dict(mode=2,map_id=4,ball_id=3,character_id=1001,team=1)
        raw = protocol.free_loadstart_cast(room)
        fields = struct.unpack_from('<12i',raw,8)
        # 00574454 and 005744E7 look these up before creating keeper objects.
        self.assertIn(fields[9],valid)
        self.assertIn(fields[10],valid)
        self.assertEqual(fields[5],1001)
        self.assertEqual(fields[7:9],(300,0))
        room['mode']=3
        fields=struct.unpack_from('<12i',protocol.free_loadstart_cast(room),8)
        self.assertEqual(fields[7:9],(300,60))

    def test_closed_slots_preserve_host_and_team_change_uses_available_slot(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp)/'slots.sqlite3')
            creation = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
            character = store.create(creation[8:-4])
            registry = RoomRegistry()
            room = registry.create(self.body,character,'owner')
            self.assertEqual(registry.set_slot(room,2,0,1),5)
            self.assertEqual(registry.set_slot(room,2,0,1),5)
            registry.change_team(room)
            self.assertEqual((room['team'],room['slot']),(2,1))
            with self.assertRaises(ValueError):
                registry.set_slot(room,2,1,1)
            for slot in range(3): registry.set_slot(room,1,slot,1)
            with self.assertRaises(ValueError): registry.change_team(room)
            self.assertEqual((room['team'],room['slot']),(2,1))
            data = protocol.free_room_record(room)
            _,offset = read_string(data,0)
            self.assertEqual(struct.unpack_from('<ihh',data,offset),(1,1,2))
            offset += 27
            for index in range(6):
                name,offset = read_string(data,offset)
                team,_,_ = struct.unpack_from('<iii',data,offset)
                offset += 12
                self.assertEqual(team,2 if index == 4 else 6 if index < 4 else 7)
                self.assertEqual(name,character['nickname'] if index == 4 else '')
