"""Persistent research account and lossless character packet storage."""
from pathlib import Path
from contextlib import contextmanager
import re
import sqlite3
import struct
import economy
from catalog import SKILLS, TUTORIAL_IDS
from missions import MISSION_IDS
from character_profiles import gear_parts,default_gear,validate_creation_gear,compatible,ITEM_INFO
from character_slots import migrate as migrate_slots,SCHEMA as CHARACTER_SCHEMA
from onboarding import migrate as migrate_onboarding,FIRST_MISSION_DONE,MAX_STEP

DEFAULT_DB = Path(__file__).resolve().parent / 'data' / 'kickoff.sqlite3'
# Keep player IDs away from the scene's small, fixed object IDs (ball = 2).
FIRST_CHARACTER_ID = 1001
STARTER_GEAR = Path(__file__).resolve().parent/'data/starter-gear.bin'
BASE_APPEARANCE_SLOTS = (0, 1, 2, 3, 4, 19)
# Hair, face, coat, hand, trousers and footwear of the starter Man_Normal outfit.
STARTER_ITEMS = (101008, 102001, 103004, 104002, 105004, 120007)
NO_ABILITIES = b'\xff\xff\xff\xff\x00\xff\xff\xff\xff\x00'
MAX_CHARACTERS=3

class CharacterError(ValueError):
    def __init__(self,message,code=2):
        super().__init__(message);self.code=code


def read_string(data, offset):
    if len(data) < offset + 2:
        raise ValueError('Missing string length')
    size = struct.unpack_from('<H', data, offset)[0]
    end = offset + 2 + size
    if not size or end > len(data) or data[end-1] != 0:
        raise ValueError('Invalid terminated string')
    return data[offset+2:end-1].decode('cp949'), end


def decode_create(data):
    # 004138F5: id, string, nine int32 fields.
    name, end = read_string(data, 4)
    base_end = end + 36
    if len(data) < base_end:
        raise ValueError('Truncated character base')
    _, gear_start = read_string(data, base_end)
    # 00414E7A: string, int32, two int16, twenty (int32,int16) slots.
    if len(data) != gear_start + 8 + 20*6:
        raise ValueError('Unexpected character equipment length')
    if not 4<=len(name)<=14:raise CharacterError('Nickname must contain 4-14 letters',3)
    if not re.fullmatch(r'[A-Za-z]+',name):raise CharacterError('Nickname must use ASCII letters only',4)
    return name, base_end


class Store:
    def __init__(self, path=DEFAULT_DB, starter_items=False, economy_config=economy.CONFIG):
        self.path = Path(path)
        self.economy = economy.Policy(economy_config)
        self.starter_items = starter_items
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migrate_slots(self.path)
        with self.connect() as db:
            legacy_accounts={r[0] for r in db.execute('SELECT id FROM accounts')} if db.execute("SELECT 1 FROM sqlite_master WHERE name='accounts'").fetchone() else set()
            db.execute('CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL)')
            db.execute("INSERT OR IGNORE INTO accounts(id,name) VALUES (1, 'localtest')")
            if 'active_character_id' not in {r['name'] for r in db.execute('PRAGMA table_info(accounts)')}:
                db.execute('ALTER TABLE accounts ADD COLUMN active_character_id INTEGER')
            db.execute(CHARACTER_SCHEMA.replace('CREATE TABLE characters','CREATE TABLE IF NOT EXISTS characters',1))
            for row in db.execute('SELECT id,payload FROM characters WHERE id < ?', (FIRST_CHARACTER_ID,)).fetchall():
                assigned_id = FIRST_CHARACTER_ID + row['id'] - 1
                payload = struct.pack('<i', assigned_id) + bytes(row['payload'])[4:]
                db.execute('UPDATE characters SET id=?,payload=? WHERE id=?', (assigned_id, payload, row['id']))
            db.execute('CREATE TABLE IF NOT EXISTS items (account_id INTEGER NOT NULL REFERENCES accounts(id), slot INTEGER NOT NULL, goods_id INTEGER NOT NULL, info_id INTEGER NOT NULL, selection INTEGER NOT NULL, abilities BLOB NOT NULL, PRIMARY KEY(account_id,slot), UNIQUE(account_id,goods_id,selection))')
            columns={row['name'] for row in db.execute('PRAGMA table_info(characters)')}
            if 'stats' not in columns: db.execute('ALTER TABLE characters ADD COLUMN stats BLOB')
            db.execute('CREATE TABLE IF NOT EXISTS tutorial_progress (character_id INTEGER NOT NULL REFERENCES characters(id), tutorial_id INTEGER NOT NULL, completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(character_id,tutorial_id))')
            db.execute('CREATE TABLE IF NOT EXISTS skills (character_id INTEGER NOT NULL REFERENCES characters(id), skill_id INTEGER NOT NULL, goods_id INTEGER NOT NULL, group_id INTEGER NOT NULL DEFAULT -1, sort INTEGER NOT NULL DEFAULT -1, PRIMARY KEY(character_id,skill_id))')
            db.execute('CREATE TABLE IF NOT EXISTS environment_updates (account_id INTEGER NOT NULL REFERENCES accounts(id), scope TEXT NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(account_id,scope))')
            db.execute('CREATE TABLE IF NOT EXISTS mission_progress (character_id INTEGER NOT NULL REFERENCES characters(id), mission_id INTEGER NOT NULL, state INTEGER NOT NULL DEFAULT 2, PRIMARY KEY(character_id,mission_id))')
            db.execute('UPDATE accounts SET active_character_id=(SELECT id FROM characters WHERE account_id=accounts.id ORDER BY char_slot LIMIT 1) WHERE active_character_id IS NULL OR NOT EXISTS(SELECT 1 FROM characters WHERE id=accounts.active_character_id AND account_id=accounts.id)')
            for row in db.execute('SELECT * FROM characters').fetchall():
                self._grant_starter_items(db,row)
        migrate_onboarding(self.path)
        economy.migrate(self.path,self.economy,legacy_accounts)
        with self.connect() as db:db.execute('PRAGMA user_version=9')

    def balances(self, account_id=1):
        with self.connect() as db:return economy.wallet(db,account_id,self.economy)

    def _grant_starter_items(self, db, character):
        # The client cannot re-mount a default part after the player removes
        # one, so the base appearance must exist as real, re-equippable items.
        account_id=character['account_id']
        _,_,parts=gear_parts(bytes(character['initial_gear']))
        for info_id,selection in parts if self.starter_items else ():
            if info_id==-1:continue
            if db.execute('SELECT 1 FROM items WHERE account_id=? AND goods_id=? AND selection=1',(account_id,info_id)).fetchone():
                continue
            slot=db.execute('SELECT COALESCE(MAX(slot),-1)+1 FROM items WHERE account_id=?',(account_id,)).fetchone()[0]
            db.execute('INSERT INTO items VALUES (?,?,?,?,1,?)',(account_id,slot,info_id,info_id,NO_ABILITIES))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, account_id=1,character_id=None):
        with self.connect() as db:
            row = self._active_row(db,account_id) if character_id is None else db.execute('SELECT * FROM characters WHERE id=? AND account_id=?',(character_id,account_id)).fetchone()
            if not row:
                return None
            result = dict(row)
            result['tutorials'] = [r[0] for r in db.execute('SELECT tutorial_id FROM tutorial_progress WHERE character_id=? ORDER BY tutorial_id', (row['id'],))]
            result['skills'] = [dict(r) for r in db.execute('SELECT * FROM skills WHERE character_id=? ORDER BY skill_id', (row['id'],))]
            result['missions'] = {r[0]:r[1] for r in db.execute('SELECT mission_id,state FROM mission_progress WHERE character_id=?', (row['id'],))}
            result['onboarding']=dict(db.execute('SELECT * FROM character_onboarding WHERE character_id=?',(row['id'],)).fetchone())
            env=db.execute("SELECT payload FROM environment_updates WHERE account_id=? AND scope='user'",(account_id,)).fetchone()
            result['user_environment_flags']=struct.unpack('<I',env[0])[0] if env else 1
            result['balances']=economy.wallet(db,account_id,self.economy)
            history=db.execute('SELECT played,wins,draws FROM character_match_history WHERE character_id=?',(row['id'],)).fetchone()
            if history:result['match_history']=dict(history)
            return result

    @staticmethod
    def _active_row(db,account_id):
        return db.execute('SELECT c.* FROM characters c JOIN accounts a ON a.id=c.account_id WHERE c.account_id=? ORDER BY (c.id=a.active_character_id) DESC,c.char_slot LIMIT 1',(account_id,)).fetchone()

    def list_characters(self,account_id=1):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM characters WHERE account_id=? ORDER BY char_slot',(account_id,))]

    def select(self,character_id,account_id=1):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM characters WHERE id=? AND account_id=?',(character_id,account_id)).fetchone():
                raise ValueError('Character does not belong to this account')
            db.execute('UPDATE accounts SET active_character_id=? WHERE id=?',(character_id,account_id))
        return self.get(account_id)

    def delete(self,character_id,account_id=1):
        with self.connect() as db:
            row=db.execute('SELECT * FROM characters WHERE id=? AND account_id=?',(character_id,account_id)).fetchone()
            if row is None:raise ValueError('Character does not belong to this account')
            for table in ('tutorial_progress','skills','mission_progress'):
                db.execute(f'DELETE FROM {table} WHERE character_id=?',(character_id,))
            db.execute('DELETE FROM characters WHERE id=?',(character_id,))
            active=self._active_row(db,account_id)
            db.execute('UPDATE accounts SET active_character_id=? WHERE id=?',(active['id'] if active else None,account_id))
            db.execute("DELETE FROM environment_updates WHERE account_id=? AND scope='kicker'",(account_id,))
        return self.get(account_id)

    def select_mission(self, mission_id, abandon=False, account_id=1):
        if mission_id not in MISSION_IDS:
            raise ValueError('Mission not offered by local server')
        with self.connect() as db:
            row=self._active_row(db,account_id)
            if row is None: raise ValueError('No local character')
            if abandon:
                db.execute('DELETE FROM mission_progress WHERE character_id=? AND mission_id=? AND state<>4',(row['id'],mission_id))
            else:
                # 008573C3: the client holds one active Play mission ID.
                # Collected missions (state 4) stay recorded as finished.
                if db.execute('SELECT 1 FROM mission_progress WHERE character_id=? AND mission_id=? AND state=4',(row['id'],mission_id)).fetchone():
                    raise ValueError('Mission already completed')
                db.execute('DELETE FROM mission_progress WHERE character_id=? AND mission_id<>? AND state<>4',(row['id'],mission_id))
                db.execute('INSERT OR IGNORE INTO mission_progress(character_id,mission_id) VALUES (?,?)',(row['id'],mission_id))

    def mark_mission_success(self, mission_id, account_id=1):
        """Local test hook: client state 3 is 'succeeded, reward pending' (004449D0)."""
        if mission_id not in MISSION_IDS:
            raise ValueError('Mission not offered by local server')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=self._active_row(db,account_id)
            if row is None: raise ValueError('No local character')
            if db.execute('SELECT 1 FROM mission_progress WHERE character_id=? AND mission_id=? AND state=4',(row['id'],mission_id)).fetchone():
                raise ValueError('Mission reward already collected')
            db.execute('DELETE FROM mission_progress WHERE character_id=? AND mission_id<>? AND state<>4',(row['id'],mission_id))
            db.execute('INSERT OR REPLACE INTO mission_progress(character_id,mission_id,state) VALUES (?,?,3)',(row['id'],mission_id))
            return row['id']

    def claim_mission_reward(self, mission_id, account_id=1):
        """Collect the reward of a succeeded mission exactly once."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=self._active_row(db,account_id)
            if row is None: raise ValueError('No local character')
            state=db.execute('SELECT state FROM mission_progress WHERE character_id=? AND mission_id=?',(row['id'],mission_id)).fetchone()
            if state is None or state[0]!=3:
                raise ValueError('Mission has no pending reward')
            from missions import mission_info,REWARD_GAME_POINT
            _,rewards=mission_info(mission_id)
            if any(kind!=REWARD_GAME_POINT for kind,_ in rewards):
                raise ValueError('Mission item reward is not reconstructed')
            amount=sum(value for kind,value in rewards)
            balance=economy.wallet(db,account_id,self.economy)
            amount=min(max(0,amount),economy.MAX_BALANCE-balance['game_points'])
            db.execute('UPDATE wallets SET game_points=game_points+? WHERE account_id=?',(amount,account_id))
            db.execute('INSERT INTO economy_ledger(account_id,character_id,kind,reference,game_points,cash) VALUES(?,?,?,?,?,0)',
                       (account_id,row['id'],'mission',f'{row["id"]}:{mission_id}',amount))
            db.execute('UPDATE mission_progress SET state=4 WHERE character_id=? AND mission_id=?',(row['id'],mission_id))
            return [(REWARD_GAME_POINT,amount)] if rewards else []

    def complete_tutorial(self, tutorial_id, account_id=1):
        if tutorial_id not in TUTORIAL_IDS or not 0 <= tutorial_id < 1024:
            raise ValueError('Unknown tutorial')
        with self.connect() as db:
            character = self._active_row(db,account_id)
            if not character:
                raise ValueError('No local character')
            inserted = db.execute('INSERT OR IGNORE INTO tutorial_progress(character_id,tutorial_id) VALUES (?,?)', (character['id'],tutorial_id))
            return inserted.rowcount == 1

    def save_environment(self, scope, data, account_id=1):
        # Preserve the complete native packet. Only confirmed bit2 is used
        # for introduction routing; the observer integer is not a checkpoint.
        if scope not in ('user','kicker') or len(data) != (4 if scope == 'user' else 12):
            raise ValueError('Unexpected environment update')
        with self.connect() as db:
            if scope == 'kicker':
                row = self._active_row(db,account_id)
                if not row or struct.unpack_from('<i',data)[0] != row['id']:
                    raise ValueError('Environment references another character')
                _,flags,observer=struct.unpack('<iII',data)
                previous=db.execute('SELECT flags FROM character_onboarding WHERE character_id=?',(row['id'],)).fetchone()
                # Late packets must not reopen an already finished introduction.
                flags |= previous['flags'] & FIRST_MISSION_DONE
                db.execute("UPDATE character_onboarding SET flags=?,observer=?,source='native',updated_at=CURRENT_TIMESTAMP WHERE character_id=?",(flags,observer,row['id']))
            db.execute('INSERT INTO environment_updates VALUES (?,?,?) ON CONFLICT(account_id,scope) DO UPDATE SET payload=excluded.payload',(account_id,scope,data))

    def save_intro_step(self, step, character_id=None, account_id=1):
        if not isinstance(step,int) or not 0<=step<=MAX_STEP:raise ValueError('Invalid introductory tutorial step')
        with self.connect() as db:
            character=self._active_row(db,account_id)
            if not character or (character_id is not None and character_id!=character['id']):
                raise ValueError('Tutorial step belongs to an inactive character')
            state=db.execute('SELECT * FROM character_onboarding WHERE character_id=?',(character['id'],)).fetchone()
            changed=not (state['flags'] & FIRST_MISSION_DONE) and step>state['step']
            if changed:
                db.execute("UPDATE character_onboarding SET step=?,source='native',updated_at=CURRENT_TIMESTAMP WHERE character_id=?",(step,character['id']))
            return character['id'],bool(changed)

    def replay_intro(self, character_id, account_id=1):
        """Explicit administrative replay; never alter practice/mission/gear data."""
        with self.connect() as db:
            state=db.execute('SELECT o.* FROM character_onboarding o JOIN characters c ON c.id=o.character_id WHERE c.id=? AND c.account_id=?',(character_id,account_id)).fetchone()
            if not state:raise ValueError('Character does not belong to this account')
            db.execute("UPDATE character_onboarding SET flags=?,step=0,source='replay',updated_at=CURRENT_TIMESTAMP WHERE character_id=?",(state['flags'] & ~3,character_id))
        return self.get(account_id,character_id)

    def buy_skill(self, goods_id, method, account_id=1):
        if goods_id not in SKILLS:
            raise ValueError('Unsupported skill purchase')
        currency,cost=economy.quote(goods_id,'Skill',method)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            character = self._active_row(db,account_id)
            if not character:
                raise ValueError('No local character')
            economy.validate_level(character,goods_id,self.economy)
            if not db.execute('SELECT 1 FROM skills WHERE character_id=? AND skill_id=?',(character['id'],SKILLS[goods_id])).fetchone():
                economy.debit(db,account_id,currency,cost,character['id'],f'skill:{character["id"]}:{goods_id}',self.economy)
                db.execute('INSERT INTO skills(character_id,skill_id,goods_id) VALUES (?,?,?)', (character['id'],SKILLS[goods_id],goods_id))
        return self.get(account_id)

    def save_skills(self, data, account_id=1):
        # 0041D35E / 0041E3B3: used slots, max slots, count; (ID, group, sort).
        if len(data) < 6:
            raise ValueError('Truncated skill locker')
        used, maximum, count = struct.unpack_from('<hhh', data)
        if not 0 <= used <= 5 or maximum != 5 or count < 0 or len(data) != 6+8*count:
            raise ValueError('Unsupported skill locker header')
        records = [struct.unpack_from('<ihh',data,6+8*i) for i in range(count)]
        ids = [record[0] for record in records]
        groups = [record[1] for record in records if record[1] != -1]
        if used != len(groups):
            raise ValueError('Skill slot count does not match groups')
        # Group/Sort are native skill classifications, not exclusive slot IDs.
        # A real locker can have three pass skills with (1,1), each using a slot.
        # Count active skill records; only the skill identity must be unique.
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate skill ID')
        if any(group not in (-1,1,2,3,4,5) or sort not in (-1,1,2,3,4,5) for _,group,sort in records):
            raise ValueError('Invalid skill group or sort')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            character = self._active_row(db,account_id)
            if not character:
                raise ValueError('No local character')
            owned = {row[0] for row in db.execute('SELECT skill_id FROM skills WHERE character_id=?', (character['id'],))}
            if set(ids) != owned:
                raise ValueError('Skill locker does not match owned skills')
            for skill_id,group,sort in records:
                if (group==-1)!=(sort==-1):raise ValueError('Skill group and sort must both be inactive or active')
                if group!=-1:
                    goods=db.execute('SELECT goods_id FROM skills WHERE character_id=? AND skill_id=?',(character['id'],skill_id)).fetchone()[0]
                    economy.validate_level(character,goods,self.economy)
                db.execute('UPDATE skills SET group_id=?,sort=? WHERE character_id=? AND skill_id=?', (group,sort,character['id'],skill_id))
        return self.get(account_id)

    def items(self, account_id=1):
        with self.connect() as db:
            items=[dict(row) for row in db.execute('SELECT * FROM items WHERE account_id=? ORDER BY slot',(account_id,))]
            character=self._active_row(db,account_id)
            if character:
                roster=db.execute('SELECT * FROM characters WHERE account_id=? ORDER BY (id=?) DESC,char_slot',(account_id,character['id'])).fetchall()
                for owner in roster:
                    _,_,parts=gear_parts(bytes(owner['payload'])[owner['base_end']:])
                    for item in items:
                        if 'kicker_id' not in item and (item['info_id'],item['selection']) in parts:
                            item['kicker_id']=owner['id']
            return items

    def buy_item(self, goods_id, info_id, selection, abilities, account_id=1, method=1, option=1):
        from catalog import ITEMS
        if ITEMS.get(goods_id)!=info_id:raise ValueError('Unknown or mismatched goods item')
        currency,cost=economy.quote(goods_id,'Item',method,option)
        economy.validate_item_abilities(goods_id,abilities)
        if len(abilities)!=10 or selection not in economy.SELECTIONS.get(info_id,()):
            raise ValueError('Invalid item selection or abilities')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            character=self._active_row(db,account_id)
            if character:economy.validate_level(character,goods_id,self.economy)
            row=db.execute('SELECT * FROM items WHERE account_id=? AND goods_id=? AND selection=?',(account_id,goods_id,selection)).fetchone()
            if row: return dict(row)
            slot=db.execute('SELECT COALESCE(MAX(slot),-1)+1 FROM items WHERE account_id=?',(account_id,)).fetchone()[0]
            if slot>=200: raise ValueError('Local inventory is full')
            economy.debit(db,account_id,currency,cost,character['id'] if character else None,f'item:{goods_id}:{selection}',self.economy)
            db.execute('INSERT INTO items VALUES (?,?,?,?,?,?)',(account_id,slot,goods_id,info_id,selection,abilities))
            return dict(db.execute('SELECT * FROM items WHERE account_id=? AND slot=?',(account_id,slot)).fetchone())

    def equip(self, data, account_id=1):
        _, start=read_string(data,0)
        gear_end=start+8+20*6
        if len(data)<gear_end+48: raise ValueError('Truncated equipment request')
        count=struct.unpack_from('<i',data,gear_end+44)[0]
        if count<0 or len(data)!=gear_end+48+2*count: raise ValueError('Invalid equipment slot list')
        slots=struct.unpack_from('<'+'H'*count,data,gear_end+48)
        with self.connect() as db:
            row=self._active_row(db,account_id)
            if not row: raise ValueError('No local character')
            owned={r['slot'] for r in db.execute('SELECT slot FROM items WHERE account_id=?',(account_id,))}
            if not set(slots)<=owned: raise ValueError('Equipment references an unowned item')
            # Restore base model parts; accessories remain optional.
            gear=bytearray(data[:gear_end])
            starter=bytes(row['initial_gear'])
            starter_model,starter_start=read_string(starter,0)
            model,_=read_string(gear,0)
            if model!=starter_model or gear[start:start+8]!=starter[starter_start:starter_start+8]:
                raise ValueError('Equipment cannot change the character body')
            for slot in BASE_APPEARANCE_SLOTS if model==starter_model else ():
                offset=start+8+6*slot
                if struct.unpack_from('<i',gear,offset)[0]==-1:
                    gear[offset:offset+6]=starter[starter_start+8+6*slot:starter_start+14+6*slot]
            _,_,original_parts=gear_parts(starter)
            _,_,equipped_parts=gear_parts(gear)
            owned_parts={(r['info_id'],r['selection']) for r in db.execute('SELECT info_id,selection FROM items WHERE account_id=?',(account_id,))}
            for slot,part in enumerate(equipped_parts):
                if part==(-1,-1):continue
                if part not in owned_parts and part!=original_parts[slot]:raise ValueError('Unowned equipment part')
                if not compatible(part[0],model):raise ValueError('Clothing is incompatible with this character')
                if part!=original_parts[slot]:
                    for other in db.execute('SELECT * FROM characters WHERE account_id=? AND id<>?',(account_id,row['id'])):
                        _,_,other_parts=gear_parts(bytes(other['payload'])[other['base_end']:])
                        if part in other_parts:raise ValueError('Item is already equipped by another character')
            assigned=bytes(row['payload'])[:row['base_end']]+gear
            decode_create(assigned)
            db.execute('UPDATE characters SET payload=?,stats=? WHERE id=?',
                       (assigned,data[gear_end:gear_end+44],row['id']))
        return self.get(account_id)

    def create(self, payload, account_id=1):
        name, base_end = decode_create(payload)
        # Creation packets contain a native "NotLimite" level sentinel (37).
        # Progression belongs to the server, never to the submitted base fields.
        clean=bytearray(payload)
        offset,fields=economy.base_fields({'payload':payload})
        struct.pack_into('<i',clean,offset+2*4,1)
        struct.pack_into('<i',clean,offset+7*4,0)
        payload=bytes(clean)
        validate_creation_gear(payload[base_end:])
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM characters WHERE nickname=? COLLATE NOCASE', (name,)).fetchone()
            if existing:
                original = bytearray(existing['payload'])
                original_offset,_=economy.base_fields(existing)
                struct.pack_into('<i',original,original_offset+2*4,1)
                struct.pack_into('<i',original,original_offset+7*4,0)
                if existing['account_id']!=account_id or original[4:] != payload[4:]:
                    raise CharacterError('Character name already in use',1)
                character_id=existing['id']
                db.execute('UPDATE accounts SET active_character_id=? WHERE id=?',(character_id,account_id))
            else:
                occupied={r[0] for r in db.execute('SELECT char_slot FROM characters WHERE account_id=?',(account_id,))}
                if len(occupied)>=MAX_CHARACTERS:raise CharacterError('All three character slots are occupied',0)
                slot=next(i for i in range(MAX_CHARACTERS) if i not in occupied)
                sequence=db.execute("SELECT seq FROM sqlite_sequence WHERE name='characters'").fetchone()
                character_id = max(FIRST_CHARACTER_ID, db.execute('SELECT COALESCE(MAX(id),0)+1 FROM characters').fetchone()[0],sequence[0]+1 if sequence else 0)
                assigned = struct.pack('<i', character_id) + payload[4:]
                db.execute('INSERT INTO characters(id,account_id,nickname,payload,base_end,char_slot,initial_gear) VALUES (?,?,?,?,?,?,?)', (character_id, account_id, name, assigned, base_end,slot,payload[base_end:]))
                db.execute("INSERT INTO character_onboarding(character_id,flags,source) VALUES (?,0,'new')",(character_id,))
                db.execute('UPDATE accounts SET active_character_id=? WHERE id=?',(character_id,account_id))
                self._grant_starter_items(db,dict(account_id=account_id,initial_gear=payload[base_end:]))
        return self.get(account_id)
