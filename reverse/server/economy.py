"""Server-owned wallet/progression policy. Native prices and XP thresholds."""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import re
import sqlite3
import struct

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).parent/'config/economy.json'
MAX_BALANCE = 2**31-1
MAX_REWARD = 32767  # conservative bound for native 16-bit result fields


class Policy:
    def __init__(self, path=CONFIG):
        self.path = Path(path)
        self.reload()

    def reload(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        integers = ('starting_game_points','starting_cash','legacy_game_points',
                    'legacy_cash','match_base_xp','match_base_game_points','minimum_match_seconds')
        for key in integers:
            if type(data.get(key)) is not int or not 0 <= data[key] <= MAX_BALANCE:
                raise ValueError('Invalid economy setting: '+key)
        for key in ('enforce_level_requirements','reward_practice'):
            if type(data.get(key)) is not bool:raise ValueError('Invalid economy setting: '+key)
        for key in ('xp_multiplier','game_points_multiplier'):
            v=data.get(key)
            if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=100:
                raise ValueError('Invalid economy setting: '+key)
        for base,multiplier in (('match_base_xp','xp_multiplier'),('match_base_game_points','game_points_multiplier')):
            if self.scaled(data[base],data[multiplier])>MAX_REWARD:
                raise ValueError('Reward exceeds native packet limit: '+base)
        self.data=data
        self.mtime=self.path.stat().st_mtime_ns

    def refresh(self):
        if self.path.stat().st_mtime_ns!=self.mtime:self.reload()

    @staticmethod
    def scaled(value,multiplier):
        return int((Decimal(value)*Decimal(str(multiplier))).quantize(Decimal(1),rounding=ROUND_HALF_UP))

    def rewards(self, mode, seconds):
        self.refresh()
        if seconds < self.data['minimum_match_seconds'] or (mode==2 and not self.data['reward_practice']):
            return 0,0
        if mode not in (2,3):raise ValueError('Unsupported reward mode')
        return (self.scaled(self.data['match_base_xp'],self.data['xp_multiplier']),
                self.scaled(self.data['match_base_game_points'],self.data['game_points_multiplier']))


from resource_catalog import RESOURCE
GOODS={int(k):v for k,v in RESOURCE['goods'].items()}
SELECTIONS={int(k):frozenset(v) for k,v in RESOURCE['selections'].items()}
LEVELS=tuple(tuple(v) for v in RESOURCE['levels'])


def base_fields(character):
    # CharacterBase: wire after nickname; level index2; cumulative XP index7.
    p=character['payload'];offset=6+struct.unpack_from('<H',p,4)[0]
    return offset,struct.unpack_from('<9i',p,offset)


def validate_level(character,goods_id,policy):
    policy.refresh()
    if not policy.data['enforce_level_requirements']:return
    if character is None:raise ValueError('No character selected')
    level=base_fields(character)[1][2];limits=GOODS[goods_id]['limits']
    for key in ('MinLevel','MaxLevel'):
        v=limits.get(key,'NotLimite')
        if v!='NotLimite' and ((key=='MinLevel' and level<int(v)) or (key=='MaxLevel' and level>int(v))):
            raise ValueError('Character does not meet the goods level requirement')


def quote(goods_id,kind,method,option=1):
    # Native payment enum at00A3A6A8: 1 GamePoint,2 Cash,3 All (catalog only).
    goods=GOODS.get(goods_id)
    if not goods or goods['kind']!=kind or method not in (1,2) or option not in (1,2,3):
        raise ValueError('Unsupported purchase or duration option')
    selling=goods['selling'];currency='GamePoint' if method==1 else 'Cash'
    if selling.get('Status') not in ('Normal','Best') or selling.get('Method') not in (currency,'All'):
        raise ValueError('Goods cannot be purchased with this currency')
    discount=int(selling.get('SalePercent',0))
    if not 0<=discount<=100:raise ValueError('Invalid catalog discount')
    cost=int(goods['costs'][currency+str(option)])
    # The declared currency must be allowed above. An explicit zero price in
    # that currency is a native promotional offer, not a negative debit.
    if cost<0:raise ValueError('Invalid negative catalog price')
    return ('game_points' if method==1 else 'cash'),max(0,cost*(100-discount)//100)


def validate_item_abilities(goods_id,abilities):
    if len(abilities)!=10:raise ValueError('Invalid item abilities')
    pairs=[struct.unpack_from('<iB',abilities,o) for o in (0,5)]
    if any((index==-1 and points!=0) or (points and not 0<=index<11) for index,points in pairs):
        raise ValueError('Unknown item attribute')
    active=[(index,points) for index,points in pairs if points]
    rules=GOODS[goods_id]['element']
    if len(active)>int(rules.get('AbilityCount',0)) or sum(points for _,points in active)>int(rules.get('AbilityPoint',0)):
        raise ValueError('Item attributes exceed the catalog budget')
    if len({index for index,_ in active})!=len(active):raise ValueError('Duplicate item attribute')


def migrate(path,policy,legacy_accounts):
    """Add economy state losslessly; backup before upgrading an existing DB."""
    path=Path(path)
    with closing(sqlite3.connect(path)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='wallets'").fetchone():return
        if legacy_accounts:
            folder=ROOT/'backups' if path.resolve().is_relative_to(ROOT) else path.parent
            folder.mkdir(exist_ok=True)
            target=folder/(path.stem+'.pre-economy-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
            with closing(sqlite3.connect(target)) as backup:db.backup(backup)
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE wallets(account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
            game_points INTEGER NOT NULL CHECK(game_points BETWEEN 0 AND 2147483647),
            cash INTEGER NOT NULL CHECK(cash BETWEEN 0 AND 2147483647))''')
        db.execute('''CREATE TABLE economy_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id), character_id INTEGER,
            kind TEXT NOT NULL, reference TEXT NOT NULL, game_points INTEGER NOT NULL,
            cash INTEGER NOT NULL, xp INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id,kind,reference))''')
        db.execute('''CREATE TABLE match_receipts(match_id TEXT PRIMARY KEY, character_id INTEGER NOT NULL,
            mode INTEGER NOT NULL, score_body BLOB NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        db.execute('''CREATE TABLE character_match_history(character_id INTEGER PRIMARY KEY REFERENCES characters(id)
            ON DELETE CASCADE ON UPDATE CASCADE, played INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0, draws INTEGER NOT NULL DEFAULT 0)''')
        for (account_id,) in db.execute('SELECT id FROM accounts').fetchall():
            prefix='legacy' if account_id in legacy_accounts else 'starting'
            db.execute('INSERT INTO wallets VALUES(?,?,?)',(account_id,policy.data[prefix+'_game_points'],policy.data[prefix+'_cash']))
        db.commit()


def wallet(db,account_id,policy):
    if not db.execute('SELECT 1 FROM accounts WHERE id=?',(account_id,)).fetchone():raise ValueError('Unknown account')
    db.execute('INSERT OR IGNORE INTO wallets VALUES(?,?,?)',(account_id,policy.data['starting_game_points'],policy.data['starting_cash']))
    return dict(db.execute('SELECT game_points,cash FROM wallets WHERE account_id=?',(account_id,)).fetchone())


def debit(db,account_id,currency,cost,character_id,reference,policy):
    wallet(db,account_id,policy)
    changed=db.execute(f'UPDATE wallets SET {currency}={currency}-? WHERE account_id=? AND {currency}>=?',(cost,account_id,cost)).rowcount
    if not changed:raise ValueError('Insufficient balance')
    db.execute('INSERT INTO economy_ledger(account_id,character_id,kind,reference,game_points,cash) VALUES(?,?,?,?,?,?)',
               (account_id,character_id,'purchase',reference,-cost if currency=='game_points' else 0,-cost if currency=='cash' else 0))
