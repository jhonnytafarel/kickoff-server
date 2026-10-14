"""First-avatar introduction, completion, legacy migration and safe replay."""
import sys,struct,tempfile,unittest,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
sys.path.insert(0,str(ROOT/'tools'))
from characters import Store
from test_character_slots import creation
from restore_wardrobe_capture import snapshot
import protocol


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'intro.sqlite3'
        self.store=Store(self.path,starter_items=True)
    def tearDown(self):self.tmp.cleanup()
    def test_first_character_starts_pending_and_practice_does_not_finish_the_intro(self):
        self.assertIsNone(self.store.get())
        login=protocol.login_ack(None,0)
        self.assertEqual(struct.unpack_from('<I',login,16)[0],1)
        c=self.store.create(creation('Novato'))
        self.assertEqual(c['onboarding']['flags'],0);self.assertEqual(c['onboarding']['step'],0)
        self.assertEqual(c['onboarding']['source'],'new')
        self.assertEqual(struct.unpack_from('<II',protocol.kicker_info_ack(c,0x100000),20),(0,0))
        self.assertEqual(struct.unpack_from('<I',protocol.login_ack(c,1),16)[0]&1,0)
        self.store.complete_tutorial(101)
        self.assertEqual(self.store.get()['onboarding']['flags'],0)
        self.assertEqual(len(self.store.items()),6)
    def test_steps_are_monotonic_persisted_and_do_not_mark_final_exercise_complete(self):
        c=self.store.create(creation('Novato'))
        for step in (2,3,7):self.assertEqual(self.store.save_intro_step(step,c['id']),(c['id'],True))
        for step in (2,7):self.assertEqual(self.store.save_intro_step(step,c['id']),(c['id'],False))
        saved=Store(self.path).get();self.assertEqual(saved['onboarding']['step'],7)
        self.assertEqual(saved['onboarding']['flags']&2,0)
        for step in (-1,8,100):
            with self.assertRaises(ValueError):self.store.save_intro_step(step,c['id'])
        self.assertEqual(Store(self.path).get(),saved)
    def test_native_completion_preserves_other_flags_observer_and_survives_late_updates(self):
        c=self.store.create(creation('Novato'))
        self.store.save_environment('user',struct.pack('<I',0x43))
        self.store.save_environment('kicker',struct.pack('<iII',c['id'],0x9045,99))
        saved=self.store.get()
        self.assertEqual(struct.unpack_from('<I',protocol.login_ack(saved),16)[0],0x42)
        self.assertEqual(struct.unpack_from('<II',protocol.kicker_info_ack(saved,0x100000),20),(0x9045,99))
        self.store.save_environment('kicker',struct.pack('<iII',c['id'],2,0))
        self.store.save_environment('kicker',struct.pack('<iII',c['id'],0x8001,0))
        finished=Store(self.path).get()
        self.assertEqual(finished['onboarding']['flags'],0x8003)
        self.assertEqual(struct.unpack_from('<I',protocol.login_ack(finished),16)[0],0x43)
        self.assertFalse(self.store.save_intro_step(7,c['id'])[1])
        self.assertEqual(finished['tutorials'],[])
    def test_characters_do_not_share_intro_steps_and_replay_keeps_profile_items_and_progress(self):
        a=self.store.create(creation('Primeiro'));self.store.complete_tutorial(101)
        self.store.save_environment('kicker',struct.pack('<iii',a['id'],2,0))
        b=self.store.create(creation('Segunda','Woman_Normal'))
        self.assertEqual(b['onboarding']['flags'],0)
        with self.assertRaises(ValueError):self.store.save_intro_step(3,a['id'])
        with self.assertRaises(ValueError):self.store.save_environment('kicker',struct.pack('<iii',a['id'],2,0))
        before=snapshot(self.path);replay=self.store.replay_intro(a['id']);after=snapshot(self.path)
        self.assertEqual(replay['onboarding']['flags']&3,0)
        for table in before:
            if table!='character_onboarding':self.assertEqual(before[table],after[table])
        self.assertEqual(self.store.get()['id'],b['id'])
        self.assertEqual(self.store.get(character_id=a['id'])['tutorials'],[101])
        self.assertEqual(self.store.create(creation('Segunda','Woman_Normal'))['id'],b['id'])
        self.assertEqual(self.store.get(character_id=a['id'])['onboarding']['source'],'replay')
        self.store.delete(a['id'])
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM character_onboarding WHERE character_id=?',(a['id'],)).fetchone())
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
    def test_legacy_migration_does_not_force_old_players_and_uses_owned_native_environment(self):
        a=self.store.create(creation('Primeiro'));self.store.complete_tutorial(101)
        b=self.store.create(creation('Segunda','Woman_Normal'))
        self.store.save_environment('kicker',struct.pack('<iii',b['id'],2,0))
        with self.store.connect() as db:db.execute('DROP TABLE character_onboarding')
        before=snapshot(self.path);upgraded=Store(self.path);after=snapshot(self.path)
        for table in before:self.assertEqual(before[table],after[table])
        self.assertEqual(upgraded.get(character_id=a['id'])['onboarding']['source'],'legacy_skipped')
        self.assertEqual(upgraded.get(character_id=b['id'])['onboarding']['flags'],2)
        self.assertEqual(upgraded.get(character_id=b['id'])['onboarding']['source'],'native')
        self.assertEqual(len(list(self.path.parent.glob('*.pre-onboarding-*.sqlite3'))),1)
        Store(self.path);self.assertEqual(len(list(self.path.parent.glob('*.pre-onboarding-*.sqlite3'))),1)


if __name__=='__main__':unittest.main()
