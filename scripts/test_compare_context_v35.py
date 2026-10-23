import datetime as dt
import unittest
from compare_context_v35 import candidate_probability, goals_distribution, team_load, truths, UTC

class CompareContextTests(unittest.TestCase):
    def event(self,days_ago):
        t=dt.datetime(2026,10,23,tzinfo=UTC)-dt.timedelta(days=days_ago)
        return {'_kickoff':t,'home':{'name':'Test FC','score':1},'away':{'name':'Visitors','score':0},'date':t.isoformat(), 'done':True}

    def test_workload_timing(self):
        target=dt.datetime(2026,10,23,tzinfo=UTC)
        h=team_load([self.event(2),self.event(4),self.event(12)],{'name':'Test FC'},target)
        self.assertEqual(h['past14Days'],3)
        self.assertEqual(h['restDays'],2)
        self.assertGreater(h['workload'],0)

    def test_finalization_buffer(self):
        target=dt.datetime(2026,10,23,tzinfo=UTC)
        h=team_load([self.event(.04),self.event(5)],{'name':'Test FC'},target)
        self.assertEqual(h['past14Days'],1)
        self.assertEqual(h['restDays'],5)

    def test_probabilities(self):
        p=goals_distribution(1.6,1.1)
        self.assertAlmostEqual(sum(p[k] for k in ('home','draw','away')),1,places=8)
        self.assertTrue(all(0<v<1 for v in p.values()))

    def test_no_effect_at_zero(self):
        b={'p':{'home':.4,'draw':.3,'away':.3,'over25':.5,'btts':.55},'lh':1.5,'la':1.1}
        w={'workload':1.}
        self.assertEqual(candidate_probability(b,w,w,0),b['p'])

    def test_fatigue_penalizes_attacking_team(self):
        b={'p':{'home':.4,'draw':.3,'away':.3,'over25':.5,'btts':.55},'lh':1.5,'la':1.1}
        alt=candidate_probability(b,{'workload':1.},{'workload':0.},.09)
        normal=goals_distribution(1.5,1.1)
        self.assertLess(alt['home'],normal['home'])

if __name__=='__main__':unittest.main()
