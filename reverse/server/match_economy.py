"""Idempotent MVP participation rewards, bound to a server-started match.

The legacy host reports outcomes. They are archived, not used to increase payout.
Production authoritative goals/elapsed-play verification is still separate work.
"""
import struct
import time
import uuid
import economy


def start(room,store):
    if room.get('playing'):raise ValueError('Match is already playing')
    if room['mode']==3:
        players=[dict(team=room['team'],ready=room['ready'])]+list(room.get('guests',{}).values())
        if room.get('bot'):players.append(dict(team=room['bot']['team'],ready=True))
        if any(not 2<=sum(p['team']==team for p in players)<=3 for team in (1,2)) or any(not p['ready'] for p in players):
            raise ValueError('Common match requires two or three ready players per team')
    character=store.get(character_id=room['character_id'])
    if not character:raise ValueError('Room character is missing')
    for skill in character['skills']:
        if skill['group_id']!=-1:economy.validate_level(character,skill['goods_id'],store.economy)
    ids={room['character_id'],3,4}
    if room.get('bot'):ids.add(room['bot']['character']['id'])
    for guest in room.get('guests',{}).values():
        if 'character' not in guest:raise ValueError('Guest character is missing')
        if guest['character']['id'] in ids:raise ValueError('Duplicate match participant')
        ids.add(guest['character']['id'])
    room['match_session']=dict(id=uuid.uuid4().hex,character_id=room['character_id'],
        mode=room['mode'],started=time.monotonic(),participants=ids)


def finish(store,room,records):
    session=room.get('match_session')
    if not session:raise ValueError('No server-started match to settle')
    submitted=b''.join(records)
    ids={struct.unpack_from('<i',r)[0] for r in records}
    if session['character_id'] not in ids or not ids<=session['participants']:
        raise ValueError('Results do not match the starting roster')
    if session['mode']==3 and ids!=session['participants']:
        raise ValueError('Common results must include the complete starting roster')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        receipt=db.execute('SELECT * FROM match_receipts WHERE match_id=?',(session['id'],)).fetchone()
        if receipt:
            # Replay is acknowledged without repeating XP on the native client.
            out=[]
            for offset in range(0,len(receipt['score_body']),138):
                record=bytearray(receipt['score_body'][offset:offset+138]);record[14:30]=bytes(16);out.append(bytes(record))
            return out,dict(replayed=True,xp=0,game_points=0)
        if not room.get('playing'):raise ValueError('Match is not playing')
        character=db.execute('SELECT * FROM characters WHERE id=?',(session['character_id'],)).fetchone()
        if character is None:raise ValueError('Match character no longer exists')
        account_id=character['account_id']
        elapsed=max(0,time.monotonic()-session['started'])
        xp,gp=store.economy.rewards(session['mode'],elapsed)
        balance=economy.wallet(db,account_id,store.economy)
        gp=min(gp,economy.MAX_BALANCE-balance['game_points'])
        offset,fields=economy.base_fields(character)
        xp=min(xp,economy.MAX_BALANCE-max(0,fields[7]))
        payload=bytearray(character['payload']);total=max(0,fields[7])+xp
        # Keep existing research avatars (level37 sentinel) untouched. New ones
        # follow the native LevelExp table; no custom client-side XP curve.
        level=max(fields[2],max(l for l,threshold in economy.LEVELS if total>=threshold)) if xp else fields[2]
        struct.pack_into('<i',payload,offset+7*4,total)
        struct.pack_into('<i',payload,offset+2*4,level)
        if xp:db.execute('UPDATE characters SET payload=? WHERE id=?',(bytes(payload),character['id']))
        if gp:db.execute('UPDATE wallets SET game_points=game_points+? WHERE account_id=?',(gp,account_id))
        out=[]
        for original in records:
            record=bytearray(original)
            if struct.unpack_from('<i',record)[0]==character['id']:
                struct.pack_into('<HH',record,26,xp,gp)
                if session['mode']==3:
                    result=struct.unpack_from('<i',record,4)[0]
                    db.execute('''INSERT INTO character_match_history(character_id,played,wins,draws) VALUES(?,1,?,?)
                        ON CONFLICT(character_id) DO UPDATE SET played=played+1,wins=wins+excluded.wins,draws=draws+excluded.draws''',
                        (character['id'],int(result==2),int(result==4)))
            out.append(bytes(record))
        db.execute('INSERT INTO match_receipts(match_id,character_id,mode,score_body) VALUES(?,?,?,?)',
                   (session['id'],character['id'],session['mode'],b''.join(out)))
        db.execute('INSERT INTO economy_ledger(account_id,character_id,kind,reference,game_points,cash,xp) VALUES(?,?,?,?,?,0,?)',
                   (account_id,character['id'],'match',session['id'],gp,xp))
    return out,dict(replayed=False,xp=xp,game_points=gp,elapsed_seconds=round(elapsed,2),level=level)
