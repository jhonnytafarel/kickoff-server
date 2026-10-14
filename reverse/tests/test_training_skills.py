from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
from characters import Store
from catalog import SKILLS, TUTORIAL_IDS
import protocol
from economy_helpers import set_level,fund


class TrainingSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'account.sqlite3'
        self.store = Store(self.path)
        packet = bytes.fromhex((ROOT/'tests/fixtures/create-localplayer.hex').read_text())
        self.character = self.store.create(packet[8:-4])
        self.character = set_level(self.store,36)
        fund(self.store)
        self.character = self.store.get()

    def tearDown(self):
        self.temp.cleanup()

    def test_tutorial_completion_survives_restart_and_retry_without_losing_items(self):
        self.store.buy_item(101003,101003,1,struct.pack('<ib',-1,0)*2)
        self.assertTrue(self.store.complete_tutorial(101))
        reopened = Store(self.path)
        self.assertFalse(reopened.complete_tutorial(101))
        saved = reopened.get()
        self.assertEqual(saved['tutorials'],[101])
        self.assertEqual(saved['payload'],self.character['payload'])
        self.assertEqual(len(reopened.items()),1)
        packet = protocol.kicker_info_ack(saved,0x200000)
        self.assertEqual(struct.unpack_from('<iI',packet,20),(101,128))
        self.assertEqual(packet[28+101//8],1 << (101%8))
        self.assertEqual(protocol.FrameDecoder().feed(packet),[(2,packet)])
        with reopened.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tutorial_progress').fetchone()[0],1)

    def test_unknown_tutorial_does_not_mark_progress(self):
        self.assertIn(101,TUTORIAL_IDS)
        for value in (-1,1024,999):
            with self.assertRaises(ValueError):
                self.store.complete_tutorial(value)
        self.assertEqual(self.store.get()['tutorials'],[])

    def test_skill_purchase_and_group_survive_restart_and_invalid_save_is_atomic(self):
        self.assertEqual(SKILLS[1006012],1006012)
        self.store.buy_skill(1006012,1)
        self.store.buy_skill(1006012,1)
        saved = Store(self.path).get()
        self.assertEqual(len(saved['skills']),1)
        wire = protocol.skill_locker_record(saved)
        self.assertEqual(struct.unpack('<hhhihh',wire),(0,5,1,1006012,-1,-1))
        selected = struct.pack('<hhhihh',1,5,1,1006012,1,1)
        self.store.save_skills(selected)
        self.assertEqual(protocol.skill_locker_record(Store(self.path).get()),selected)
        invalid = struct.pack('<hhhihh',1,5,1,1234567,1,1)
        with self.assertRaises(ValueError):
            self.store.save_skills(invalid)
        self.assertEqual(protocol.skill_locker_record(Store(self.path).get()),selected)

    def test_invalid_purchase_and_duplicate_skill_ids_leave_database_unchanged(self):
        for goods,option in ((999,1),(1006012,2)):
            with self.assertRaises(ValueError):
                self.store.buy_skill(goods,option)
        self.store.buy_skill(1006012,1)
        self.store.buy_skill(1006013,1)
        before = self.store.get()
        duplicated = struct.pack('<hhhihhihh',2,5,2,1006012,1,1,1006012,1,1)
        with self.assertRaises(ValueError):
            self.store.save_skills(duplicated)
        self.assertEqual(self.store.get(),before)

    def test_real_five_skills_allow_shared_classification_and_survive_relogin(self):
        packet=bytes.fromhex((ROOT/'tests/fixtures/equip-five-skills-shared-group.hex').read_text())
        records=[struct.unpack_from('<ihh',packet,14+8*i) for i in range(7)]
        for skill_id,_,_ in records:self.store.buy_skill(skill_id,1)
        before=self.store.get();saved=self.store.save_skills(packet[8:-4])
        self.assertEqual({s['skill_id']:(s['group_id'],s['sort']) for s in saved['skills']},
                         {skill_id:(group,sort) for skill_id,group,sort in records})
        self.assertEqual(struct.unpack_from('<hhh',protocol.skill_locker_record(saved)),(5,5,7))
        self.assertEqual(Store(self.path).get(),saved)
        self.assertEqual(saved['balances'],before['balances'])
        self.assertEqual(saved['payload'],before['payload'])
        # Unequip/re-equip one pass skill without disturbing the other passes.
        remove=bytearray(packet[8:-4]);struct.pack_into('<h',remove,0,4);struct.pack_into('<hh',remove,10,-1,-1)
        self.store.save_skills(remove);self.store.save_skills(packet[8:-4])
        self.assertEqual(self.store.get(),saved)
        too_many=bytearray(packet[8:-4]);struct.pack_into('<h',too_many,0,6);struct.pack_into('<hh',too_many,6+8*5+4,5,5)
        with self.assertRaises(ValueError):self.store.save_skills(too_many)
        self.assertEqual(self.store.get(),saved)

    def test_real_three_skill_save_preserves_active_groups_and_unequip(self):
        for goods in (1109001,1006012,2001007):
            self.store.buy_skill(goods,1)
        packet = bytes.fromhex((ROOT/'tests/fixtures/equip-three-skills.hex').read_text())
        self.assertEqual(protocol.FrameDecoder().feed(packet),[(2,packet)])
        saved = self.store.save_skills(packet[8:-4])
        self.assertEqual(struct.unpack_from('<hhh',protocol.skill_locker_record(saved)),(3,5,3))
        self.assertEqual({r['skill_id']:r['group_id'] for r in saved['skills']},
                         {1109001:1,1006012:2,2001007:3})
        before = Store(self.path).get()
        self.assertEqual(before,saved)
        remove = bytearray(protocol.skill_locker_record(saved))
        struct.pack_into('<h',remove,0,2)
        struct.pack_into('<hh',remove,10,-1,-1)
        removed = self.store.save_skills(bytes(remove))
        self.assertEqual(struct.unpack_from('<hhh',protocol.skill_locker_record(removed)),(2,5,3))
        self.assertEqual(len(removed['skills']),3)

    def test_schema_upgrade_keeps_existing_account_and_inventory(self):
        self.store.buy_item(101003,101003,1,struct.pack('<ib',-1,0)*2)
        with self.store.connect() as db:
            db.execute('DROP TABLE tutorial_progress')
            db.execute('DROP TABLE skills')
            db.execute('PRAGMA user_version=2')
        migrated = Store(self.path)
        self.assertEqual(migrated.get()['payload'],self.character['payload'])
        self.assertEqual(len(migrated.items()),1)
        with migrated.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)


if __name__ == '__main__':
    unittest.main()
