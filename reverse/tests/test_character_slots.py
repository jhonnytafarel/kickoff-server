"""Three-slot migration, independent progression and native appearance validation."""
import sys,struct,tempfile,unittest,sqlite3
from contextlib import closing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from characters import Store,CharacterError,read_string,BASE_APPEARANCE_SLOTS
from character_profiles import PROFILES,gear_parts
import protocol
FIXTURE=bytes.fromhex((Path(__file__).parent/'fixtures/create-localplayer.hex').read_text())[8:-4]

def creation(name,model='Man_Normal'):
    _,end=read_string(FIXTURE,4);prefix=name.encode('ascii')+b'\0'
    base=FIXTURE[:4]+struct.pack('<H',len(prefix))+prefix+FIXTURE[end:end+36]
    p=PROFILES[model];tag=model.encode()+b'\0'
    gear=struct.pack('<H',len(tag))+tag+struct.pack('<ihh',p['body'],p['heights'][0],int(p['weights'][0]))
    gear+=b''.join(struct.pack('<ih',p['choices'][i][0],1) if i in p['choices'] else struct.pack('<ih',-1,-1) for i in range(20))
    return base+gear

class SlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'slots.sqlite3'
    def tearDown(self):self.tmp.cleanup()
    def test_three_slots_selection_and_progress_are_independent_after_reopen(self):
        s=Store(self.path,starter_items=True)
        a=s.create(creation('Primeiro'));s.complete_tutorial(101)
        a=__import__('economy_helpers').set_level(s,2);s.buy_skill(1006012,1)
        b=s.create(creation('Segunda','Woman_Normal'));s.complete_tutorial(102)
        c=s.create(creation('Terceiro','Man_Big'))
        self.assertEqual([p['char_slot'] for p in s.list_characters()],[0,1,2])
        self.assertEqual(Store(self.path).get()['id'],c['id'])
        s.select(a['id']);self.assertEqual(s.get()['tutorials'],[101]);self.assertEqual(len(s.get()['skills']),1)
        s.select(b['id']);self.assertEqual(s.get()['tutorials'],[102]);self.assertEqual(s.get()['skills'],[])
        self.assertEqual(s.get(character_id=a['id'])['id'],a['id']);self.assertEqual(s.get()['id'],b['id'])
        with self.assertRaises(CharacterError) as e:s.create(creation('Quarto'))
        self.assertEqual(e.exception.code,0)
        packet=protocol.locker_init_ack(s.get(),s.list_characters())
        self.assertEqual(struct.unpack_from('<iii',packet,8),(-1,b['id'],3))
        self.assertEqual(packet[20:-4],a['payload']+b['payload']+c['payload'])
        login=protocol.login_ack(s.get(),len(s.list_characters()));n=struct.unpack_from('<H',login,20)[0]
        self.assertEqual(struct.unpack_from('<ii',login,30+n),(3,3))
    def test_names_retries_duplicates_invalid_names_and_foreign_selection(self):
        s=Store(self.path);p=creation('Primeiro');first=s.create(p)
        self.assertEqual(s.create(p)['id'],first['id']);self.assertEqual(len(s.list_characters()),1)
        for name,code in [('primeiro',1),('Ab',3),('MuitoMuitoComprido',3),('Player01',4)]:
            with self.assertRaises(CharacterError) as e:s.create(creation(name))
            self.assertEqual(e.exception.code,code)
        with s.connect() as db:db.execute("INSERT INTO accounts(id,name) VALUES(2,'other')")
        other=s.create(creation('Estranho'),2)
        with self.assertRaises(ValueError):s.select(other['id'])
        self.assertIsNone(s.get(character_id=other['id']))
        self.assertEqual(s.get()['id'],first['id'])
    def test_female_starter_and_unequip_restore_female_parts(self):
        s=Store(self.path,starter_items=True);female=s.create(creation('Maria','Woman_Normal'))
        initial=female['payload'][female['base_end']:];_,start,parts=gear_parts(initial)
        self.assertEqual({i['info_id'] for i in s.items()},{p[0] for p in parts if p[0]!=-1})
        gear=bytearray(initial)
        for slot in BASE_APPEARANCE_SLOTS:struct.pack_into('<ih',gear,start+8+6*slot,-1,-1)
        saved=s.equip(bytes(gear)+bytes(44)+struct.pack('<i',0))
        self.assertEqual(saved['payload'][saved['base_end']:],initial)
        s.buy_item(101003,101003,1,bytes(10))
        gear=bytearray(initial);struct.pack_into('<ih',gear,start+8,101003,1)
        with self.assertRaises(ValueError):s.equip(bytes(gear)+bytes(44)+struct.pack('<i',0))
        self.assertEqual(s.get()['payload'],saved['payload'])
    def test_old_database_migration_preserves_items_skills_progress_and_ids(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("CREATE TABLE accounts(id INTEGER PRIMARY KEY,name TEXT); INSERT INTO accounts VALUES(1,'localtest'); CREATE TABLE characters(id INTEGER PRIMARY KEY,account_id INTEGER UNIQUE,nickname TEXT,payload BLOB,base_end INTEGER,stats BLOB); CREATE TABLE tutorial_progress(character_id INTEGER REFERENCES characters(id),tutorial_id INTEGER,completed_at TEXT,PRIMARY KEY(character_id,tutorial_id));")
            payload=struct.pack('<i',1001)+FIXTURE[4:]
            db.execute('INSERT INTO characters VALUES(?,?,?,?,?,?)',(1001,1,'LocalPlayer',payload,54,bytes(44)))
            db.execute("INSERT INTO tutorial_progress VALUES(1001,101,'old')");db.commit()
        s=Store(self.path);self.assertEqual(s.get()['id'],1001);self.assertEqual(s.get()['payload'],payload)
        self.assertEqual(s.get()['tutorials'],[101]);self.assertEqual(s.get()['char_slot'],0)
        s.create(creation('Segunda','Woman_Normal'))
        with s.connect() as db:self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(len(list(self.path.parent.glob('*.pre-three-characters-*.sqlite3'))),1)

    def test_delete_keeps_other_progress_inventory_and_never_reuses_id(self):
        s=Store(self.path);a=s.create(creation('Primeiro'));s.complete_tutorial(101)
        b=s.create(creation('Segunda','Woman_Normal'));s.buy_item(101003,101003,1,bytes(10))
        s.delete(b['id']);self.assertEqual(s.get()['id'],a['id'])
        self.assertEqual(s.get()['tutorials'],[101]);self.assertEqual(len(s.items()),1)
        c=s.create(creation('Terceiro'));self.assertGreater(c['id'],b['id'])
        s.delete(a['id']);s.delete(c['id']);self.assertIsNone(s.get())
        self.assertEqual(len(s.items()),1)

if __name__=='__main__':unittest.main()
