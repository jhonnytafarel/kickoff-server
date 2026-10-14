"""Validate/show economy settings; update XP/PG multipliers with a backup."""
import argparse,json,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from economy import Policy,CONFIG

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=CONFIG)
    parser.add_argument('--xp',type=float,help='XP multiplier: 1 normal, 2 double')
    parser.add_argument('--gold',type=float,help='Game Point multiplier: 1 normal, 2 double')
    args=parser.parse_args();policy=Policy(args.config)
    if args.xp is not None or args.gold is not None:
        data=dict(policy.data)
        if args.xp is not None:data['xp_multiplier']=args.xp
        if args.gold is not None:data['game_points_multiplier']=args.gold
        candidate=args.config.with_suffix('.candidate.json')
        try:
            candidate.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
            Policy(candidate)  # never replace the active file with invalid policy
            backup=args.config.with_name(args.config.stem+'.backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
            backup.write_bytes(args.config.read_bytes())
            candidate.replace(args.config)
        finally:
            if candidate.exists():candidate.unlink()
        policy=Policy(args.config)
    print(json.dumps(dict(config=str(args.config.resolve()),settings=policy.data,
        common_reward=policy.rewards(3,policy.data['minimum_match_seconds'])),indent=2,ensure_ascii=False))

if __name__=='__main__':main()
