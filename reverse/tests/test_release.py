"""Standalone metadata, safe client preparation and read-only purchase history."""
import json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from contextlib import closing
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'));sys.path.insert(0,str(ROOT/'tools'))
from resource_catalog import RESOURCE,load
from prepare_client import configure
from purchase_history import history
from characters import Store
from test_character_slots import creation

class ReleaseTests(unittest.TestCase):
    def test_server_catalog_is_complete_without_client_installation(self):
        self.assertEqual(len(RESOURCE['goods']),481);self.assertEqual(len(RESOURCE['skills']),99)
        self.assertEqual(len(RESOURCE['profiles']),4);self.assertEqual(len(RESOURCE['missions']),27)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.json';p.write_text(json.dumps({'schema_version':2}))
            with self.assertRaises(ValueError):load(p)
    def test_private_config_changes_only_endpoints_and_research_settings(self):
        data=b'<Root><Configure><FrontSessionIP>old</FrontSessionIP><SessionIP>old</SessionIP><FrontSessionMode>0</FrontSessionMode><LogLevel>0</LogLevel><FullScreen>true</FullScreen><EventNoticeUrl>old</EventNoticeUrl><LogicalWidth>1024</LogicalWidth><LogicalHeight>768</LogicalHeight></Configure></Root>'
        changed=configure(data)
        self.assertIn(b'<FrontSessionIP>127.0.0.1</FrontSessionIP>',changed)
        self.assertIn(b'<FrontSessionMode>2</FrontSessionMode>',changed)
        self.assertIn(b'<LogicalWidth>1024</LogicalWidth>',changed)
        self.assertIn(b'<LogicalHeight>768</LogicalHeight>',changed)
        self.assertEqual(changed.count(b'<DisableXTrap '),1)
        self.assertEqual(configure(changed),changed)
        self.assertIn(b'<FullScreen>false</FullScreen>',configure(data,True))
    def test_purchase_history_is_read_only_and_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'demo.sqlite3';missing=Path(tmp)/'missing.sqlite3'
            with self.assertRaises(sqlite3.OperationalError):history(missing)
            self.assertFalse(missing.exists())
            store=Store(path);c=store.create(creation('JogadorRua'))
            store.buy_item(101003,101003,1,bytes(10));before=path.read_bytes()
            rows=history(path,20,c['id'])
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['game_points'],-20)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(history(path,20,c['id']+1),[])

if __name__=='__main__':unittest.main()
