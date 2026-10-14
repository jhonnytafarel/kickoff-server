"""Native introductory mission flags and per-character tutorial checkpoints.

ObjectInfo bit2 means the first mission is finished/skipped (004F4541).
Its second integer is observer data, NOT TutorialStep. Native TutorialWorld
resets TutorialStep to zero at 00572060, so reconnect restarts the introduction.
"""
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import sqlite3,struct

FIRST_MISSION_DONE=2
MAX_STEP=7
SCHEMA='''CREATE TABLE character_onboarding (
 character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE ON UPDATE CASCADE,
 flags INTEGER NOT NULL CHECK(flags BETWEEN 0 AND 4294967295),
 observer INTEGER NOT NULL DEFAULT 0 CHECK(observer BETWEEN 0 AND 4294967295),
 step INTEGER NOT NULL DEFAULT 0 CHECK(step BETWEEN 0 AND 7),
 source TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)'''


def migrate(path):
    path=Path(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='character_onboarding'").fetchone():return
        rows=db.execute('SELECT id,account_id FROM characters').fetchall()
        if rows:
            live=Path(__file__).resolve().parent/'data/kickoff.sqlite3'
            folder=path.parents[2]/'backups' if path.resolve()==live.resolve() else path.parent
            folder.mkdir(parents=True,exist_ok=True)
            backup=folder/(path.stem+'.pre-onboarding-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
            with closing(sqlite3.connect(backup)) as copy:db.backup(copy)
        with db:
            db.execute(SCHEMA)
            for character_id,account_id in rows:
                # Existing profiles were previously routed past the intro.
                # Preserve that behavior when their original completion is unknown.
                flags,observer,source=2,0,'legacy_skipped'
                saved=db.execute("SELECT payload FROM environment_updates WHERE account_id=? AND scope='kicker'",(account_id,)).fetchone()
                if saved and len(saved[0])==12:
                    owner,native_flags,native_observer=struct.unpack('<iII',saved[0])
                    if owner==character_id:flags,observer,source=native_flags,native_observer,'native'
                db.execute('INSERT INTO character_onboarding(character_id,flags,observer,source) VALUES (?,?,?,?)',
                           (character_id,flags,observer,source))
            db.execute('PRAGMA user_version=8')
            if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Onboarding migration broke a foreign key')
