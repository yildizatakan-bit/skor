"""Small, synthetic regression tests. No network access or production DB changes."""
import datetime as dt
import unittest
from match_context_v34 import build, parse_time, standings, slug


def event(event_id,league,date,home,away,hs=None,as_=None,done=False):
    return {'id':event_id,'league':league,'date':date,'_at':parse_time(date),
            'home':{'name':home,'score':hs},'away':{'name':away,'score':as_},
            '_hk':slug(home),'_ak':slug(away),'done':done}


class ContextTests(unittest.TestCase):
    def test_no_future_result_leakage(self):
        # Even if DB contains a future final score, it must not affect past standings.
        rows=[event('1','eng.1','2026-08-01T12:00:00Z','A','B',1,0,True),
              event('2','eng.1','2026-10-28T12:00:00Z','A','B',9,0,True)]
        rank,_=standings(rows,'eng.1',parse_time('2026-10-23T12:00:00Z'))
        self.assertEqual(rank['a']['points'],3)
        self.assertEqual(rank['a']['games'],1)

    def test_rest_and_upcoming_uefa(self):
        rows=[event('1','eng.1','2026-10-20T12:00:00Z','A','B',1,0,True),
              event('2','eng.1','2026-10-25T12:00:00Z','A','C'),
              event('3','uefa.champions','2026-10-28T12:00:00Z','A','D')]
        result=build(rows,parse_time('2026-10-23T12:00:00Z'))
        game=next(m for m in result['matches'] if m['matchId']=='2')
        a=game['home']
        self.assertEqual(a['restDays'],5.0)
        self.assertEqual(len(a['factorEvidence']['upcoming_key_match']['evidence']['knownUEFAOrCupGamesNext7Days']),1)
        self.assertEqual(a['factorEvidence']['rotation']['status'],'not_verifiable')

    def test_just_kicked_off_is_not_treated_as_finished(self):
        rows=[event('1','eng.1','2026-10-23T11:00:00Z','A','B',5,0,True)]
        table,_=standings(rows,'eng.1',parse_time('2026-10-23T12:00:00Z'))
        self.assertEqual(table,{})

    def test_no_score_model_changes(self):
        x=build([event('1','tur.1','2026-10-24T12:00:00Z','A','B')],
                parse_time('2026-10-23T12:00:00Z'))
        self.assertFalse(x['modelPredictionsModified'])
        self.assertEqual(x['scope']['matchesAnalyzed'],1)
        self.assertEqual(x['matches'][0]['home']['factorEvidence']['uefa_guaranteed']['status'],'not_verifiable')

    def test_year_boundary_and_turkish_team_name(self):
        self.assertEqual(slug('Fenerbahçe'),'fenerbahce')
        rows=[event('1','tur.1','2026-06-02T12:00:00Z','Fenerbahçe','B',2,0,True),
              event('2','tur.1','2026-09-02T12:00:00Z','Fenerbahçe','B',0,2,True)]
        table,_=standings(rows,'tur.1',parse_time('2026-10-23T12:00:00Z'))
        self.assertEqual(table['fenerbahce']['games'],1)


if __name__=='__main__':
    unittest.main()
