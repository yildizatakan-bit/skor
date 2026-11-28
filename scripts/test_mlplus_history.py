import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import mlplus_history as history

class HistoryTests(unittest.TestCase):
    def test_lossless_migration_and_sharding(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            rows=[{"matchId":str(i),"name":"Çağlar","data":"abc"*50} for i in range(30)]
            legacy=root/"mlplus_player_history.json"
            legacy.write_text(json.dumps(rows))
            before=legacy.read_bytes()
            self.assertEqual(history.load_history(root),rows)
            limit=history.SHARD_BYTES
            try:
                history.SHARD_BYTES=600
                history.save_history(rows,root)
                self.assertEqual(history.load_history(root),rows)
                manifest=json.loads((root/"mlplus_player_history/manifest.json").read_text())
                self.assertGreater(len(manifest["files"]),1)
                history.save_history(rows+[rows[0]],root)
                self.assertEqual(history.load_history(root),rows+[rows[0]])
                self.assertEqual(legacy.read_bytes(),before)
            finally:
                history.SHARD_BYTES=limit

if __name__=="__main__":
    unittest.main()
