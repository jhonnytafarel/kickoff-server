"""Transactional upgrade from one character per account to three native slots."""
from datetime import datetime,timezone
from contextlib import closing
from pathlib import Path
import sqlite3
from character_profiles import default_gear

SCHEMA='''CREATE TABLE characters (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 account_id INTEGER NOT NULL REFERENCES accounts(id),
 nickname TEXT COLLATE NOCASE UNIQUE NOT NULL,
 payload BLOB NOT NULL, base_end INTEGER NOT NULL, stats BLOB,
 char_slot INTEGER NOT NULL CHECK(char_slot BETWEEN 0 AND 2),
 initial_gear BLOB NOT NULL,
 UNIQUE(account_id,char_slot))'''

def migrate(path):
    path=Path(path)
    with closing(sqlite3.connect(path)) as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='characters'").fetchone()
        if not exists:return
        columns={r[1] for r in db.execute('PRAGMA table_info(characters)')}
        if 'char_slot' in columns:return
        folder=path.parents[2]/'backups' if path.resolve()==(Path(__file__).resolve().parent/'data/kickoff.sqlite3').resolve() else path.parent
        folder.mkdir(parents=True,exist_ok=True)
        target=folder/(path.stem+'.pre-three-characters-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
        with closing(sqlite3.connect(target)) as backup:db.backup(backup)
        db.execute('PRAGMA foreign_keys=OFF');db.execute('BEGIN IMMEDIATE')
        try:
            rows=db.execute('SELECT id,account_id,nickname,payload,base_end'+(',stats' if 'stats' in columns else ',NULL')+' FROM characters').fetchall()
            db.execute(SCHEMA.replace('CREATE TABLE characters','CREATE TABLE characters_new',1))
            for id,account,name,payload,end,stats in rows:
                gear=default_gear(payload[end:])
                db.execute('INSERT INTO characters_new VALUES (?,?,?,?,?,?,?,?)',(id,account,name,payload,end,stats,0,gear))
            db.execute('DROP TABLE characters');db.execute('ALTER TABLE characters_new RENAME TO characters')
            if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Character migration broke a foreign key')
            db.commit()
        except Exception:
            db.rollback();raise
