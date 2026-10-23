import datetime as dt
import unittest
from compare_stakes_v36 import season_for, stakes_snapshot, adjusted_probabilities, update_standings, bootstrap
from collections import defaultdict

UTC=dt.timezone.utc

class StakesTests(unittest.TestCase):
    def test_season(self):
        self.assertEqual(season_for(dt.datetime(2026,1,1,tzinfo=UTC)),2025)
        self.assertEqual(season_for(dt.datetime(2026,10,1,tzinfo=UTC)),2026)

    def test_thin_league_rejected(self):
        self.assertFalse(stakes_snapshot({'a':{'p':3,'played':1,'gd':0,'gf':1}},'a','b')['usable'])

    def test_dense_table_observed_only(self):
        table={f'team{i}':{'p':30-i,'played':12,'gd':0,'gf':20} for i in range(18)}
        s=stakes_snapshot(table,'team0','team17')
        self.assertTrue(s['usable'])
        self.assertEqual(s['coverage']['status'],'partial_unverified')
        self.assertGreaterEqual(s['teams']['home']['stakesProxy'],0)

    def test_adjustment_zero_is_identity(self):
        base={'p':{'home':.5,'draw':.2,'away':.3,'over25':.6,'btts':.52},'lh':1.8,'la':1.2}
        self.assertEqual(adjusted_probabilities(base,{'usable':False},0)['home'],.5)
        self.assertEqual(adjusted_probabilities(base,{'usable':False},.12)['home'],.5)

    def test_bootstrap_zero_or_empty(self):
        self.assertIsNone(bootstrap([],0.12))
        self.assertIsNone(bootstrap([{'alternatives':{0.0:{'home':0.5}},'actual':{'home':1}}],0.0))

    def test_update_points(self):
        match={'league':'eng.1','_kickoff':dt.datetime(2026,8,1,tzinfo=UTC),
               'home':{'name':'Arsenal','score':2},'away':{'name':'Liverpool','score':1}}
        tables=defaultdict(dict);update_standings(tables,match)
        self.assertEqual(tables[('eng.1',2026)]['arsenal']['p'],3)
        self.assertEqual(tables[('eng.1',2026)]['liverpool']['p'],0)

if __name__=='__main__':unittest.main()
