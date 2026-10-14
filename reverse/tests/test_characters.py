import sys
from pathlib import Path
import tempfile
import unittest
import struct
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import characters
import protocol


class CharacterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.sqlite3'
        self.packet=bytes.fromhex((Path(__file__).parent/'fixtures/create-localplayer.hex').read_text())
        self.payload=self.packet[8:-4]

    def tearDown(self):
        self.temp.cleanup()

    def test_real_creation_persists_after_reopen_and_retry(self):
        self.assertEqual(protocol.FrameDecoder().feed(self.packet),[(2,self.packet)])
        store=characters.Store(self.path)
        saved=store.create(self.payload)
        self.assertEqual(saved['nickname'],'LocalPlayer')
        self.assertGreaterEqual(saved['id'],1001)
        self.assertEqual(saved['base_end'],54)
        expected=bytearray(self.payload);struct.pack_into('<i',expected,18+8,1)
        self.assertEqual(saved['payload'][4:],expected[4:])
        reopened=characters.Store(self.path)
        self.assertEqual(reopened.get(),saved)
        self.assertEqual(reopened.create(self.payload)['id'],saved['id'])
        self.assertEqual(struct.unpack_from('<i',saved['payload'])[0],saved['id'])

    def test_truncated_packet_does_not_create_character(self):
        store=characters.Store(self.path)
        with self.assertRaises(ValueError):
            store.create(self.payload[:-1])
        self.assertIsNone(store.get())

    def test_shop_purchase_persists_and_retries_do_not_duplicate(self):
        store=characters.Store(self.path)
        abilities=struct.pack('<ib',-1,0)*2
        item=store.buy_item(101003,101003,1,abilities)
        self.assertEqual(characters.Store(self.path).items(),[item])
        self.assertEqual(store.buy_item(101003,101003,1,abilities),item)
        wire=protocol.item_record(item)
        self.assertEqual(len(wire),42)
        self.assertEqual(struct.unpack_from('<ihih',wire),(101003,1,-1,0))
        ack=protocol.item_buy_ack(item)
        self.assertEqual(protocol.FrameDecoder().feed(ack),[(2,ack)])
        self.assertEqual(ack[24:-4],wire)
        self.assertEqual(protocol.inventory_ack(store.items())[20:-4],wire)

    def test_multiple_inventory_items_keep_ids_and_abilities_aligned(self):
        store=characters.Store(self.path)
        ids=[101003,103008,105008,120017]
        for n,goods in enumerate(ids):
            __import__('economy_helpers').grant_item(store,goods,1,struct.pack('<ibib',2*n+1,1,2*n+2,1))
        packet=protocol.inventory_ack(store.items())
        self.assertEqual(struct.unpack_from('<i',packet,16)[0],4)
        self.assertEqual(len(packet),192)
        for n,goods in enumerate(ids):
            offset=20+42*n
            self.assertEqual(struct.unpack_from('<i',packet,offset)[0],goods)
            self.assertEqual(struct.unpack_from('<i',packet,offset+32)[0],2*n+1)
            self.assertEqual(packet[offset+36],1)
            self.assertEqual(struct.unpack_from('<i',packet,offset+37)[0],2*n+2)
            self.assertEqual(packet[offset+41],1)
        self.assertEqual(protocol.FrameDecoder().feed(packet),[(2,packet)])

    def test_expiration_uses_client_comparison_field_not_64_bit_field(self):
        store=characters.Store(self.path)
        item=store.buy_item(101003,101003,1,struct.pack('<ib',-1,0)*2)
        from datetime import datetime, timezone
        for packet,offset in ((protocol.inventory_ack([item]),20),
                              (protocol.item_buy_ack(item),24)):
            end_time=struct.unpack_from('<i',packet,offset+26)[0]
            remaining=struct.unpack_from('<h',packet,offset+30)[0]
            self.assertGreater(end_time,int(datetime.now(timezone.utc).timestamp()))
            self.assertLess(end_time,2**31)
            self.assertEqual(datetime.fromtimestamp(end_time,timezone.utc).year,2037)
            self.assertGreater(remaining,0)
            self.assertNotEqual(struct.unpack_from('<Q',packet,offset+16)[0],end_time)

    def test_local_login_has_both_balances_without_changing_character_count(self):
        saved=characters.Store(self.path).create(self.payload)
        packet=protocol.login_ack(saved)
        name_length=struct.unpack_from('<H',packet,20)[0]
        self.assertEqual(struct.unpack_from('<iiii',packet,22+name_length),
                         (0,10000,1,3))

    def test_real_equipment_request_preserves_appearance_after_relogin(self):
        store=characters.Store(self.path)
        store.create(self.payload)
        store.buy_item(101003,101003,1,struct.pack('<ib',-1,0)*2)
        packet=bytes.fromhex((Path(__file__).parent/'fixtures/equip-male-santa.hex').read_text())
        self.assertEqual(protocol.FrameDecoder().feed(packet),[(2,packet)])
        saved=store.equip(packet[8:-4])
        gear=saved['payload'][saved['base_end']:]
        self.assertEqual(struct.unpack_from('<ih',gear,21),(101003,1))
        self.assertEqual(len(saved['stats']),44)
        self.assertEqual(characters.Store(self.path).get(),saved)
        inventory=store.items()
        self.assertEqual(inventory[0]['kicker_id'],saved['id'])
        self.assertEqual(struct.unpack_from('<i',protocol.item_record(inventory[0]),6)[0],saved['id'])
        info=protocol.kicker_info_ack(saved,2)
        self.assertEqual(info[20:-4],gear)
        before=store.get()
        with self.assertRaises(ValueError): store.equip(packet[8:-4][:-1])
        self.assertEqual(store.get(),before)

    def test_legacy_scene_id_is_migrated_without_losing_character(self):
        store=characters.Store(self.path)
        saved=store.create(self.payload)
        with store.connect() as db:
            db.execute('UPDATE characters SET id=1,payload=? WHERE id=?',
                       (struct.pack('<i',1)+saved['payload'][4:],saved['id']))
        restored=characters.Store(self.path).get()
        self.assertEqual(restored['id'],1001)
        self.assertEqual(restored['payload'],saved['payload'])
        self.assertEqual(restored['nickname'],saved['nickname'])
        self.assertEqual(characters.Store(self.path).get(),restored)

    def test_unequipping_clothing_restores_visible_default_pieces(self):
        store=characters.Store(self.path)
        store.create(self.payload)
        gear=bytearray(self.payload[54:])
        _, start=characters.read_string(gear,0)
        for slot in characters.BASE_APPEARANCE_SLOTS:
            struct.pack_into('<ih',gear,start+8+6*slot,-1,-1)
        saved=store.equip(bytes(gear)+bytes(44)+struct.pack('<i',0))
        starter=characters.STARTER_GEAR.read_bytes()
        for slot in characters.BASE_APPEARANCE_SLOTS:
            offset=start+8+6*slot
            self.assertEqual(saved['payload'][54+offset:54+offset+6],starter[offset:offset+6])

    def test_creation_ack_preserves_equipment_and_assigned_id(self):
        saved=characters.Store(self.path).create(self.payload)
        packet=protocol.create_ack(saved)
        self.assertEqual(protocol.FrameDecoder().feed(packet),[(2,packet)])
        self.assertEqual(packet[12:-12],saved['payload'])

    def test_shop_sections_match_client_read_sizes_and_order(self):
        saved=characters.Store(self.path).create(self.payload)
        packet=protocol.kicker_info_ack(saved,0x5818)
        result, kicker, flags=struct.unpack_from('<iii',packet,8)
        self.assertEqual((result,kicker,flags),(-1,saved['id'],0x5818))
        # Fixed stats + three int16 locker fields + performance + record + rank.
        self.assertEqual(len(packet[20:-4]),44+6+88+20+26)
        self.assertEqual(protocol.FrameDecoder().feed(packet),[(2,packet)])

    def test_practice_progress_preserves_fixed_bitfield_buffer(self):
        saved=characters.Store(self.path).create(self.payload)
        packet=protocol.kicker_info_ack(saved,0x160000)
        self.assertEqual(struct.unpack_from('<iii',packet,8),(-1,saved['id'],0x160000))
        self.assertEqual(struct.unpack_from('<ii',packet,20),(0,0))
        self.assertEqual(struct.unpack_from('<iI',packet,28),(-1,128))
        self.assertEqual(packet[36:164],bytes(128))
        self.assertEqual(struct.unpack_from('<i',packet,164),(0,))
        self.assertEqual(protocol.FrameDecoder().feed(packet),[(2,packet)])
