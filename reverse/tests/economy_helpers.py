"""Explicit test fixture progression; never used against a real account."""
import struct
import economy

def set_level(store,level,character_id=None,xp=0):
    c=store.get(character_id=character_id)
    p=bytearray(c['payload']);o,_=economy.base_fields(c)
    struct.pack_into('<i',p,o+8,level);struct.pack_into('<i',p,o+28,xp)
    with store.connect() as db:db.execute('UPDATE characters SET payload=? WHERE id=?',(bytes(p),c['id']))
    return store.get(character_id=c['id'])

def fund(store,game_points=999999999,cash=999999999):
    with store.connect() as db:db.execute('UPDATE wallets SET game_points=?,cash=? WHERE account_id=1',(game_points,cash))

def grant_item(store,info_id,selection=1,abilities=bytes(10)):
    # Wardrobe fixtures include native starter parts not sold in the shop.
    with store.connect() as db:
        if db.execute('SELECT 1 FROM items WHERE account_id=1 AND goods_id=? AND selection=?',(info_id,selection)).fetchone():return
        slot=db.execute('SELECT COALESCE(MAX(slot),-1)+1 FROM items WHERE account_id=1').fetchone()[0]
        db.execute('INSERT INTO items VALUES(1,?,?,?,?,?)',(slot,info_id,info_id,selection,abilities))
