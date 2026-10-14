"""Recover an avatar's outfit from an explicitly selected captured wear request."""
import argparse, json, sqlite3, struct, sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'reverse/server'))
from characters import Store,DEFAULT_DB
from character_profiles import gear_parts


def snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t:db.execute('SELECT * FROM "'+t+'" ORDER BY rowid').fetchall() for t in tables}


def restore(path,character_id,frame):
    before=snapshot(path)
    store=Store(path)
    current=store.get()
    if not current or current['id']!=character_id:
        raise ValueError('Recovery must target the currently selected character')
    if len(frame)<12 or struct.unpack_from('<H',frame)[0]!=len(frame) or struct.unpack_from('<I',frame,4)[0]!=0x20017:
        raise ValueError('Not a native equipment request')
    saved=store.equip(frame[8:-4])
    after=snapshot(path)
    for table,rows in before.items():
        if table=='characters':
            assert [r for r in rows if r[0]!=character_id]==[r for r in after[table] if r[0]!=character_id]
            assert len(rows)==len(after[table])
        else:assert rows==after[table],table+' changed during recovery'
    assert saved['nickname']==current['nickname']
    assert saved['initial_gear']==current['initial_gear']
    assert saved['payload'][:saved['base_end']]==current['payload'][:current['base_end']]
    with store.connect() as db:assert not db.execute('PRAGMA foreign_key_check').fetchall()
    return {'character_id':character_id,'nickname':saved['nickname'],
            'restored_parts':gear_parts(saved['payload'][saved['base_end']:])[2],
            'preserved_counts':{k:len(v) for k,v in before.items()},
            'other_characters_inventory_skills_progress_preserved':True}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--character',required=True,type=int)
    p.add_argument('--capture-time',required=True)
    args=p.parse_args()
    frame=None
    with (ROOT/'reverse/captures/events.jsonl').open(encoding='utf8') as f:
        for line in f:
            e=json.loads(line)
            if e.get('kind')=='tcp_frame' and e.get('message_id')==0x20017 and e.get('time')==args.capture_time:
                if frame is not None:raise ValueError('Ambiguous capture timestamp')
                frame=bytes.fromhex(e['hex'])
    if frame is None:raise ValueError('Requested equipment capture was not found')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=ROOT/'reverse/backups'/('kickoff.pre-wardrobe-'+stamp+'.sqlite3')
    with closing(sqlite3.connect(DEFAULT_DB)) as db, closing(sqlite3.connect(backup)) as copy:
        db.backup(copy)
    report=restore(DEFAULT_DB,args.character,frame)
    report.update(backup=str(backup),capture_time=args.capture_time)
    (ROOT/'reverse/reports/wardrobe-recovery.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


if __name__=='__main__':main()
