"""Read-only wallet, native progression and skill-slot audit."""
import argparse,json,sqlite3,sys
from pathlib import Path
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import economy
from character_profiles import gear_parts,compatible

def audit(path):
    with closing(sqlite3.connect('file:'+Path(path).resolve().as_posix()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;errors=[];notes=[];characters=[]
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        foreign_keys=[tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
        if integrity!='ok':errors.append('SQLite integrity check failed')
        if foreign_keys:errors.append('Broken foreign keys')
        for row in db.execute('SELECT * FROM characters'):
            offset,fields=economy.base_fields(row)
            if fields[2]==37:notes.append(f'Avatar {row["id"]}: legacy test level37 preserved')
            elif not 1<=fields[2]<=36:errors.append(f'Avatar {row["id"]}: invalid level')
            if not 0<=fields[7]<=economy.MAX_BALANCE:errors.append(f'Avatar {row["id"]}: invalid XP')
            skills=[dict(r) for r in db.execute('SELECT * FROM skills WHERE character_id=?',(row['id'],))]
            groups=[s['group_id'] for s in skills if s['group_id']!=-1]
            if len(groups)>5:errors.append(f'Avatar {row["id"]}: more than five active skills')
            for s in skills:
                if s['group_id'] not in (-1,1,2,3,4,5) or s['sort'] not in (-1,1,2,3,4,5):errors.append(f'Avatar {row["id"]}: invalid skill classification')
                if (s['group_id']==-1)!=(s['sort']==-1):errors.append(f'Avatar {row["id"]}: half-active skill')
                if s['group_id']!=-1:
                    try:economy.validate_level(row,s['goods_id'],economy.Policy())
                    except (ValueError,KeyError):errors.append(f'Avatar {row["id"]}: equipped skill {s["skill_id"]} outside requirements')
            model,_,parts=gear_parts(row['payload'][row['base_end']:]);_,_,initial=gear_parts(row['initial_gear'])
            owned={(r[0],r[1]) for r in db.execute('SELECT info_id,selection FROM items WHERE account_id=?',(row['account_id'],))}
            for i,part in enumerate(parts):
                if part==(-1,-1):continue
                if part not in owned and part!=initial[i]:errors.append(f'Avatar {row["id"]}: unowned equipped part {part[0]}')
                if not compatible(part[0],model):errors.append(f'Avatar {row["id"]}: incompatible part {part[0]}')
            characters.append(dict(id=row['id'],name=row['nickname'],level=fields[2],xp=fields[7],skills=len(skills)))
        wallets=[dict(r) for r in db.execute('SELECT * FROM wallets')]
        for w in wallets:
            if any(not 0<=w[c]<=economy.MAX_BALANCE for c in ('game_points','cash')):errors.append('Invalid wallet')
        return dict(database=str(Path(path).resolve()),schema=db.execute('PRAGMA user_version').fetchone()[0],
            integrity=integrity,foreign_keys=foreign_keys,characters=characters,wallets=wallets,errors=errors,notes=notes,
            native_purchase_and_result_visual_validation='pending')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',type=Path,required=True);p.add_argument('--output',type=Path)
    a=p.parse_args();r=audit(a.database);text=json.dumps(r,indent=2,ensure_ascii=False)
    if a.output:a.output.write_text(text+'\n',encoding='utf-8')
    print(text);sys.exit(1 if r['errors'] else 0)

if __name__=='__main__':main()
