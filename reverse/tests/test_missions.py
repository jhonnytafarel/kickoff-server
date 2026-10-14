import sys
import struct
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
from characters import Store
import protocol
from missions import MISSION_IDS


class MissionTests(unittest.TestCase):
    def test_selection_replacement_abandonment_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'missions.sqlite3'
            store=Store(path)
            raw=bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
            before=store.create(raw[8:-4])
            for mission_id in MISSION_IDS:
                store.select_mission(mission_id)
                store.select_mission(mission_id)
                saved=Store(path).get()
                self.assertEqual(saved['missions'],{mission_id:2})
                self.assertEqual(saved['payload'],before['payload'])
            with self.assertRaises(ValueError): store.select_mission(99999)
            self.assertEqual(store.get()['missions'],{417:2})
            packet=protocol.kicker_info_ack(store.get(),0xc0000)
            self.assertEqual(struct.unpack_from('<iii',packet,8),(-1,before['id'],0xc0000))
            # detail: count + ID; progress: count, then CategoryID, MissionID,
            # State, GameCount and ObjectivesCount (verified against the client log).
            self.assertEqual(struct.unpack_from('<9i',packet,20)[:8],(1,417,1,3,417,2,0,0))
            self.assertEqual(protocol.FrameDecoder().feed(packet),[(2,packet)])
            store.select_mission(417,abandon=True)
            store.select_mission(417,abandon=True)
            self.assertEqual(Store(path).get()['missions'],{})
            self.assertEqual(store.get()['payload'],before['payload'])

    def test_success_reward_is_collected_once_and_stays_completed(self):
        from missions import mission_info,compensation_ack
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'reward.sqlite3'
            store=Store(path)
            raw=bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
            store.create(raw[8:-4])
            with self.assertRaises(ValueError): store.claim_mission_reward(7)   # not succeeded yet
            store.select_mission(7)
            store.mark_mission_success(7)
            self.assertEqual(Store(path).get()['missions'],{7:3})
            category,rewards=mission_info(7)
            self.assertEqual((category,rewards),(1,[(6,72)]))
            # 0044EA12 layout, confirmed by the client log: GamePoint 72.
            self.assertEqual(compensation_ack(7,category,rewards),struct.pack('<8i',-1,1,7,1,6,72,0,0))
            store.claim_mission_reward(7)
            with self.assertRaises(ValueError): store.claim_mission_reward(7)   # no double payout
            with self.assertRaises(ValueError): store.select_mission(7)         # cannot restart
            store.select_mission(8)
            self.assertEqual(Store(path).get()['missions'],{7:4,8:2})
            # Collected missions are no longer "assigned" in the detail section.
            packet=protocol.kicker_info_ack(Store(path).get(),0x40000)
            self.assertEqual(struct.unpack_from('<2i',packet,20),(1,8))

    def test_category_section_opens_jeffrey_and_trainer_only(self):
        # 0044DA42 reads int32 highest index and highest+1 state bytes; 008571A3
        # only lists categories whose state is 3..5. Category 2 stays closed.
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'categories.sqlite3')
            raw=bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
            character=store.create(raw[8:-4])
            packet=protocol.kicker_info_ack(character,0x110000)
            self.assertEqual(struct.unpack_from('<iii',packet,8),(-1,character['id'],0x110000))
            # mission flags (2 ints) precede the category section
            self.assertEqual(packet[28:28+4+4],struct.pack('<i',3)+bytes([0,3,0,3]))
            self.assertEqual(protocol.FrameDecoder().feed(packet),[(2,packet)])
