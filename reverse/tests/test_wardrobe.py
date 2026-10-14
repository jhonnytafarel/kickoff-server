"""Native default gear and captured recovery tests; no executable required."""
import sys,struct,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'reverse/tools'))
sys.path.insert(0,str(ROOT/'reverse/server'))
from restore_wardrobe_capture import restore,snapshot
from characters import Store,read_string
from character_profiles import PROFILES,gear_parts
from test_character_slots import creation
import protocol

class CapturedWardrobeTests(unittest.TestCase):
    def test_recovery_keeps_both_characters_and_all_inventory_progress(self):
        frame=bytes.fromhex((Path(__file__).parent/'fixtures/equip-female-before-default.hex').read_text())
        empty=bytes.fromhex((Path(__file__).parent/'fixtures/equip-female-default-empty.hex').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'wardrobe.sqlite3';store=Store(path,starter_items=True)
            first=store.create(creation('Primeiro'));store.complete_tutorial(101)
            payload=creation('Isabelle','Woman_Normal');_,base_end=__import__('characters').decode_create(payload)
            g=bytearray(payload[base_end:]);_,start,_=gear_parts(g);struct.pack_into('<ihh',g,start,4,165,48)
            female=store.create(payload[:base_end]+g)
            _,_,parts=gear_parts(frame[8:8+start+128])
            for info,selection in parts:
                if info!=-1:__import__('economy_helpers').grant_item(store,info,selection)
            # Captured dirty inventory slot numbers belong to the live DB.
            # This isolated DB has different slots; preserve captured Gear/stats
            # while using its valid empty dirty list.
            store.equip(empty[8:8+start+172]+struct.pack('<i',0));before=snapshot(path)
            local_frame=protocol.frame(0x20017,frame[8:8+start+172]+struct.pack('<i',0))
            report=restore(path,female['id'],local_frame);after=snapshot(path)
            self.assertTrue(report['other_characters_inventory_skills_progress_preserved'])
            self.assertEqual(store.get()['payload'][base_end:],frame[8:8+start+128])
            self.assertEqual(store.get(character_id=first['id'])['tutorials'],[101])
            for table in ('accounts','items','skills','tutorial_progress','mission_progress'):
                self.assertEqual(before[table],after[table])
            self.assertEqual(store.get()['stats'],frame[8+start+128:8+start+172])
            with self.assertRaises(ValueError):restore(path,first['id'],local_frame)


class NativeDefaultTests(unittest.TestCase):
    def test_each_body_sends_original_six_parts_after_equipping_and_default_keeps_inventory(self):
        for model in PROFILES:
            with self.subTest(model=model),tempfile.TemporaryDirectory() as tmp:
                store=Store(Path(tmp)/'native.sqlite3',starter_items=True)
                avatar=store.create(creation('Original',model));initial=avatar['initial_gear']
                _,start,parts=gear_parts(initial)
                profile=PROFILES[model]
                self.assertGreater(len(profile['choices'][0]),1)
                replacement=profile['choices'][0][1]
                __import__('economy_helpers').grant_item(store,replacement)
                gear=bytearray(initial);struct.pack_into('<ih',gear,start+8,replacement,1)
                changed=store.equip(gear+bytes(44)+struct.pack('<i',0))
                expected=b''.join(struct.pack('<ih',*parts[i]) for i in (0,1,2,3,4,19))
                packet=protocol.kicker_info_ack(changed,4)
                self.assertEqual(struct.unpack_from('<iii',packet,8),(-1,avatar['id'],4))
                self.assertEqual(packet[20:-4],expected)
                self.assertEqual(len(packet),60)
                # Model native unmount's six fallback parts, clearing extras.
                restored=bytearray(initial)
                inventory_before=store.items()
                saved=store.equip(restored+bytes(44)+struct.pack('<i',0))
                self.assertEqual(saved['payload'][saved['base_end']:],initial)
                self.assertEqual({(i['slot'],i['info_id']) for i in store.items()},
                                 {(i['slot'],i['info_id']) for i in inventory_before})
                combined=protocol.kicker_info_ack(changed,15)
                offset=20+changed['base_end']+len(initial)
                self.assertEqual(combined[offset:offset+36],expected)
                self.assertEqual(combined[offset+36:-4],bytes(44))
                self.assertEqual(protocol.FrameDecoder().feed(combined),[(2,combined)])
    def test_defaults_are_per_character_and_do_not_follow_active_character_or_current_clothes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'native.sqlite3',starter_items=True)
            a=store.create(creation('Primeiro'))
            other=bytearray(creation('Segundo'));_,base_end=__import__('characters').decode_create(other)
            _,start,_=gear_parts(other[base_end:])
            hair=PROFILES['Man_Normal']['choices'][0][1]
            struct.pack_into('<ih',other,base_end+start+8,hair,1)
            b=store.create(other)
            c=store.create(creation('Terceira','Woman_Normal'))
            before=snapshot(store.path)
            for avatar in (a,b,c,a,b):
                info=store.get(character_id=avatar['id']);packet=protocol.kicker_info_ack(info,4)
                _,_,parts=gear_parts(avatar['initial_gear'])
                self.assertEqual(packet[20:-4],b''.join(struct.pack('<ih',*parts[i]) for i in (0,1,2,3,4,19)))
            self.assertNotEqual(protocol.kicker_info_ack(a,4)[20:-4],protocol.kicker_info_ack(b,4)[20:-4])
            self.assertEqual(snapshot(store.path),before)


if __name__=='__main__':unittest.main()
