"""Money conservation, requirements, replays, policy reload and native XP."""
from pathlib import Path
import concurrent.futures
import json,sqlite3,struct,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
import economy,match_economy,protocol
from characters import Store,read_string
from rooms import RoomRegistry,decode_score
from test_character_slots import creation
from economy_helpers import set_level,fund

class EconomyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'account.sqlite3'
        self.config=Path(self.tmp.name)/'economy.json';self.config.write_bytes(economy.CONFIG.read_bytes())
        self.store=Store(self.path,economy_config=self.config);self.character=self.store.create(creation('Novato'))
    def tearDown(self):self.tmp.cleanup()
    def room(self,mode=3):
        body=bytearray(bytes.fromhex((ROOT/'tests/fixtures/create-free-room.hex').read_text())[8:-4])
        _,o=read_string(body,8);struct.pack_into('<i',body,o,mode)
        room=RoomRegistry().create(bytes(body),self.store.get(),'host');room['ready']=True
        if mode==3:room['guests']={i:dict(character=dict(id=28674+i),team=t,ready=True) for i,t in enumerate((2,1,2))}
        match_economy.start(room,self.store);room['playing']=True;room['match_session']['started']-=121
        return room
    def records(self,common=True):
        records=decode_score(bytes.fromhex((ROOT/'tests/fixtures/free-score-quit.hex').read_text())[8:-4],self.character['id'])[1]
        if common:
            for i in range(3):
                r=bytearray(records[0]);struct.pack_into('<i',r,0,28674+i);records.append(bytes(r))
        return records
    def test_items_debit_real_price_once_and_relogin_uses_saved_wallet(self):
        initial=self.store.balances();cost=economy.quote(101003,'Item',1)[1]
        item=self.store.buy_item(101003,101003,1,bytes(10))
        after=self.store.balances();self.assertEqual(after['game_points'],initial['game_points']-cost)
        self.assertEqual(self.store.buy_item(101003,101003,1,bytes(10)),item)
        self.assertEqual(Store(self.path).balances(),after)
        packet=protocol.login_ack(self.store.get());n=struct.unpack_from('<H',packet,20)[0]
        self.assertEqual(struct.unpack_from('<ii',packet,22+n),(after['cash'],after['game_points']))
        ack=protocol.item_buy_ack(item,after);self.assertEqual(struct.unpack_from('<ii',ack,16),(after['game_points'],after['cash']))
    def test_failed_purchase_and_forged_options_have_no_effect(self):
        fund(self.store,game_points=0,cash=0)
        before=self.store.get()
        for goods,info,selection,method,option in [(101003,101003,1,1,1),(101003,999,1,1,1),(999,999,1,1,1),(101003,101003,99,1,1),(101003,101003,1,0,1),(101003,101003,1,1,0)]:
            with self.assertRaises(ValueError):self.store.buy_item(goods,info,selection,bytes(10),method=method,option=option)
        self.assertEqual(self.store.items(),[]);self.assertEqual(self.store.get(),before)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM economy_ledger').fetchone()[0],0)
    def test_two_simultaneous_purchase_retries_charge_once(self):
        def buy(_):return self.store.buy_item(101003,101003,1,bytes(10))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(buy,range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(len(self.store.items()),1)
        self.assertEqual(self.store.balances()['game_points'],10000-20)
    def test_forged_item_attributes_cannot_buy_extra_power(self):
        before=self.store.balances()
        for abilities in (struct.pack('<iBiB',2,255,3,255),struct.pack('<iBiB',999,1,-1,0),struct.pack('<iBiB',2,1,2,1)):
            with self.assertRaises(ValueError):self.store.buy_item(103008,103008,1,abilities)
        self.assertEqual(self.store.balances(),before);self.assertEqual(self.store.items(),[])
        self.store.buy_item(103008,103008,1,struct.pack('<iBiB',2,1,3,1))
    def test_concurrent_distinct_buys_cannot_overdraw(self):
        fund(self.store,game_points=20,cash=0)
        goods=[g for g in economy.GOODS if economy.GOODS[g]['kind']=='Item' and economy.GOODS[g]['costs']['GamePoint1']=='20' and economy.GOODS[g]['limits'].get('MinLevel')=='NotLimite'][:2]
        def buy(g):
            try:self.store.buy_item(g,int(economy.GOODS[g]['element']['InfoID']),1,bytes(10));return True
            except ValueError:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(buy,goods))
        self.assertEqual(results.count(True),1);self.assertEqual(self.store.balances()['game_points'],0)
    def test_cash_is_separate_and_requires_a_cash_catalog_offer(self):
        fund(self.store,game_points=100,cash=10)
        with self.assertRaises(ValueError):self.store.buy_item(101003,101003,1,bytes(10),method=2)
        # Current catalog is GP-only. Synthetic All offer tests native Cash enum.
        with patch.dict(economy.GOODS[101003]['selling'],Method='All'):
            self.store.buy_item(101003,101003,1,bytes(10),method=2)
        self.assertEqual(self.store.balances(),dict(game_points=100,cash=9))
    def test_explicit_free_promotional_offer_does_not_charge(self):
        set_level(self.store,36)
        goods=next(g for g,r in economy.GOODS.items() if r['kind']=='Item' and r['costs']['GamePoint1']=='0')
        before=self.store.balances();info=int(economy.GOODS[goods]['element']['InfoID'])
        with self.assertRaises(ValueError):self.store.buy_item(goods,info,1,bytes(10))
        with patch.dict(economy.GOODS[goods]['selling'],Status='Normal'):
            self.store.buy_item(goods,info,1,bytes(10))
        self.assertEqual(self.store.balances(),before)
    def test_skill_purchase_mount_requirements_and_retries(self):
        before=self.store.balances()
        with self.assertRaises(ValueError):self.store.buy_skill(1006012,1)
        self.assertEqual(self.store.balances(),before)
        set_level(self.store,2);c=self.store.buy_skill(1006012,1)
        self.assertEqual(c['balances']['game_points'],10000-150)
        self.store.buy_skill(1006012,1);self.assertEqual(self.store.balances(),c['balances'])
        mounted=struct.pack('<hhhihh',1,5,1,1006012,1,1)
        self.store.save_skills(mounted);set_level(self.store,1)
        with self.assertRaises(ValueError):self.store.save_skills(mounted)
        with self.assertRaises(ValueError):self.room()
        self.store.save_skills(struct.pack('<hhhihh',0,5,1,1006012,-1,-1))
    def test_duplicate_skill_ids_and_half_active_slots_rollback(self):
        set_level(self.store,36);fund(self.store)
        for g in (1006012,1006013):self.store.buy_skill(g,1)
        before=self.store.get()
        for wire in (struct.pack('<hhhihhihh',2,5,2,1006012,1,1,1006012,2,1),struct.pack('<hhhihhihh',1,5,2,1006012,1,-1,1006013,-1,-1)):
            with self.assertRaises(ValueError):self.store.save_skills(wire)
        self.assertEqual(self.store.get(),before)
    def test_common_payout_exactly_once_and_history_survives_restart(self):
        room=self.room();before=self.store.balances();out,payout=match_economy.finish(self.store,room,self.records())
        self.assertEqual((payout['xp'],payout['game_points']),(300,200))
        self.assertEqual(struct.unpack_from('<HH',out[0],26),(300,200))
        self.assertEqual(out[1:],self.records()[1:])
        self.assertEqual(self.store.balances()['game_points'],before['game_points']+200)
        room['playing']=False
        replay,payout=match_economy.finish(Store(self.path),room,self.records());self.assertTrue(payout['replayed'])
        self.assertEqual(struct.unpack_from('<HH',replay[0],26),(0,0))
        self.assertEqual(self.store.balances()['game_points'],before['game_points']+200)
        saved=Store(self.path).get();self.assertEqual(economy.base_fields(saved)[1][7],300)
        self.assertEqual(saved['match_history']['played'],1)
        self.assertEqual(struct.unpack_from('<i',protocol.kicker_info_ack(saved,0x800),20)[0],1)
    def test_practice_and_short_common_have_zero_payout(self):
        for mode in (2,3):
            room=self.room(mode)
            if mode==3:room['match_session']['started']+=121
            before=self.store.balances();out,payout=match_economy.finish(self.store,room,self.records(mode==3))
            self.assertEqual((payout['xp'],payout['game_points']),(0,0));self.assertEqual(self.store.balances(),before)
            self.assertEqual(struct.unpack_from('<HH',out[0],26),(0,0))
    def test_finish_before_start_and_unknown_roster_are_rejected(self):
        with self.assertRaises(ValueError):match_economy.finish(self.store,{},self.records())
        room=self.room();records=self.records();bad=bytearray(records[1]);struct.pack_into('<i',bad,0,88888)
        with self.assertRaises(ValueError):match_economy.finish(self.store,room,[records[0],bytes(bad)])
        self.assertEqual(self.store.balances()['game_points'],10000)
    def test_common_cannot_start_solo_or_twice(self):
        room=self.room()
        with self.assertRaises(ValueError):match_economy.start(room,self.store)
        room['playing']=False;room['guests'].clear()
        with self.assertRaises(ValueError):match_economy.start(room,self.store)
    def test_native_threshold_level_up_and_new_character_cannot_mint_xp(self):
        self.assertEqual(economy.base_fields(self.character)[1][2],1)
        fake=bytearray(creation('Outro'));o,_=economy.base_fields({'payload':fake})
        struct.pack_into('<i',fake,o+8,999);struct.pack_into('<i',fake,o+28,999999)
        other=self.store.create(bytes(fake));self.assertEqual(economy.base_fields(other)[1][2],1);self.assertEqual(economy.base_fields(other)[1][7],0)
        self.store.select(self.character['id']);set_level(self.store,1,xp=600)
        out,payout=match_economy.finish(self.store,self.room(),self.records())
        saved=self.store.get();self.assertEqual(economy.base_fields(saved)[1][2],2);self.assertEqual(economy.base_fields(saved)[1][7],900)
        self.assertEqual(self.store.create(creation('Novato'))['payload'],saved['payload'])
    def test_multiplier_reload_and_invalid_configuration_are_safe(self):
        data=json.loads(self.config.read_text());data.update(xp_multiplier=1,game_points_multiplier=3)
        self.config.write_text(json.dumps(data))
        self.assertEqual(self.store.economy.rewards(3,120),(150,300))
        for key,value in [('xp_multiplier',float('nan')),('match_base_xp',65536),('starting_cash',True),('reward_practice',1)]:
            bad=dict(data);bad[key]=value;self.config.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):economy.Policy(self.config)
    def test_wallet_cap_matches_wire_and_receipt_prevents_reminting(self):
        fund(self.store,game_points=economy.MAX_BALANCE-5,cash=0)
        out,payout=match_economy.finish(self.store,self.room(),self.records())
        self.assertEqual(payout['game_points'],5);self.assertEqual(self.store.balances()['game_points'],economy.MAX_BALANCE)
        self.assertEqual(struct.unpack_from('<HH',out[0],26),(300,5))
    def test_mission_claim_deposits_once_and_gm_cannot_reopen_it(self):
        before=self.store.balances();self.store.select_mission(7);self.store.mark_mission_success(7)
        self.assertEqual(self.store.claim_mission_reward(7),[(6,72)])
        self.assertEqual(self.store.balances()['game_points'],before['game_points']+72)
        with self.assertRaises(ValueError):self.store.claim_mission_reward(7)
        with self.assertRaises(ValueError):self.store.mark_mission_success(7)
    def test_economy_upgrade_preserves_legacy_avatar_and_sandbox_wallet(self):
        set_level(self.store,37);before=self.store.get()['payload']
        with self.store.connect() as db:
            for table in ('wallets','economy_ledger','match_receipts','character_match_history'):db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=8')
        upgraded=Store(self.path);self.assertEqual(upgraded.get()['payload'],before)
        self.assertEqual(upgraded.balances(),dict(game_points=999999999,cash=999999999))
        self.assertEqual(Store(self.path).balances(),upgraded.balances())
        with upgraded.connect() as db:self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

if __name__=='__main__':unittest.main()
