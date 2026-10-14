"""Read recent purchases without creating/modifying a database."""
import argparse,json,sqlite3
from contextlib import closing
from pathlib import Path

DEFAULT_DB=Path(__file__).resolve().parents[1]/'server/data/kickoff.sqlite3'
def history(path,limit=20,character_id=None):
    if not 1<=limit<=1000:raise ValueError('Limit must be between 1 and 1000')
    uri=Path(path).resolve().as_uri()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as db:
        db.row_factory=sqlite3.Row
        sql="SELECT id,account_id,character_id,reference,game_points,cash,created_at FROM economy_ledger WHERE kind='purchase'"
        params=[]
        if character_id is not None:sql+=' AND character_id=?';params.append(character_id)
        sql+=' ORDER BY id DESC LIMIT ?';params.append(limit)
        return [dict(r) for r in db.execute(sql,params)]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,default=DEFAULT_DB)
    p.add_argument('--limit',type=int,default=20)
    p.add_argument('--character',type=int)
    args=p.parse_args()
    try:rows=history(args.database,args.limit,args.character)
    except (sqlite3.Error,ValueError) as error:raise SystemExit('History unavailable: '+str(error))
    print(json.dumps(dict(database=str(args.database.resolve()),timezone='UTC',purchases=rows),indent=2))

if __name__=='__main__':main()
