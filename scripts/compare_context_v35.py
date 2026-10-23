#!/usr/bin/env python3
"""v35 observational, read-only walk-forward A/B test of past-fixture workload.

This is NOT a proven motivation model. League targets, UEFA qualification,
rotation and injuries are excluded until historically verifiable snapshots exist.
Never writes to the database, the live predictions or their history.
"""
import argparse
import collections
import csv
import datetime as dt
import json
import math
import pathlib
import random
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from analysis_engine import calculate, same_team, team_key, when

UTC = dt.timezone.utc
CANDIDATES = (0.0, 0.03, 0.06, 0.09)
COLUMNS = ('home','draw','away','over25','btts')


def read_events(dbpath):
    uri = f'{dbpath.resolve().as_uri()}?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute('SELECT payload FROM matches WHERE done=1 ORDER BY date,id').fetchall()
    events = {}
    invalid = 0
    for (payload,) in rows:
        try:
            item = json.loads(payload)
            kickoff = when(item['date']).astimezone(UTC)
            h, a = item['home'], item['away']
            if not item.get('done') or not isinstance(h.get('score'), int) or isinstance(h['score'], bool) or not isinstance(a.get('score'),int) or isinstance(a['score'],bool):
                invalid += 1; continue
            if not team_key(h.get('name')) or not team_key(a.get('name')):
                invalid += 1; continue
            item['_kickoff'] = kickoff
            events[str(item['id'])] = item
        except (TypeError, ValueError, KeyError, AttributeError):
            invalid += 1
    result = sorted(events.values(), key=lambda m:(m['_kickoff'],str(m['id'])))
    return result, invalid


def team_load(history, side, kickoff):
    # Three-hour finalization buffer: no results from games that may be in progress.
    cutoff = kickoff - dt.timedelta(hours=3)
    rows = [m for m in history if m['_kickoff'] <= cutoff and
            (same_team(m['home'],side) or same_team(m['away'],side))]
    last = max((m['_kickoff'] for m in rows), default=None)
    rest = (kickoff-last).total_seconds()/86400 if last else None
    count14 = sum(m['_kickoff'] >= kickoff-dt.timedelta(days=14) for m in rows)
    # Deterministic, pre-specified bounded fatigue proxy (not motivation).
    # 2-day turnaround scores 1, 3 days 0.5, >=4 days 0.
    short = min(1.0, max(0.0, (4-rest)/2.0)) if rest is not None else 0.0
    dense = min(1.0, max(0.0, (count14-2)/2.0))
    return {'restDays':round(rest,2) if rest is not None else None,
            'past14Days':count14, 'workload':round(0.65*short+0.35*dense,5)}


def goals_distribution(home_lambda, away_lambda):
    # Independent Poisson; same structural score assumption as production model.
    ph=[math.exp(-home_lambda)];pa=[math.exp(-away_lambda)]
    for i in range(1,19):
        ph.append(ph[-1]*home_lambda/i);pa.append(pa[-1]*away_lambda/i)
    p={k:0.0 for k in COLUMNS};mass=0.0
    for i,x in enumerate(ph):
        for j,y in enumerate(pa):
            w=x*y;mass+=w
            p['home' if i>j else 'away' if i<j else 'draw']+=w
            if i+j>=3:p['over25']+=w
            if i>0 and j>0:p['btts']+=w
    return {k:v/mass for k,v in p.items()}


def candidate_probability(base, home_workload, away_workload, k):
    if not k:
        return {key:base['p'][key] for key in COLUMNS}
    sh=home_workload['workload'];sa=away_workload['workload']
    # Fatigue can reduce one's own attack and increase chances conceded.
    # Coefficients are experimental and must be evaluated out of sample.
    lh=min(4.5,max(.15,base['lh']*math.exp(-k*sh+0.5*k*sa)))
    la=min(4.5,max(.15,base['la']*math.exp(-k*sa+0.5*k*sh)))
    return goals_distribution(lh,la)


def truths(match):
    h,a=match['home']['score'],match['away']['score']
    return {'home':int(h>a),'draw':int(h==a),'away':int(h<a),
            'over25':int(h+a>=3),'btts':int(h>0 and a>0)}


def evaluate(rows, k):
    if not rows:
        return {'n':0, 'result3Brier':None,'result3LogLoss':None,'over25Brier':None,'bttsBrier':None}
    sums=collections.defaultdict(float)
    for r in rows:
        p=r['alternatives'][k];y=r['actual']
        sums['result3Brier']+=sum((p[c]-y[c])**2 for c in ('home','draw','away'))
        actual=next(c for c in ('home','draw','away') if y[c])
        sums['result3LogLoss']-=math.log(max(1e-10,p[actual]))
        for c in ('over25','btts'):
            sums[c+'Brier']+=(p[c]-y[c])**2
    return {'n':len(rows), **{key:round(sums[key]/len(rows),6) for key in ('result3Brier','result3LogLoss','over25Brier','bttsBrier')}}


def confidence_interval(rows,k,seed=35,iterations=1000):
    if not rows or k==0.0:return None
    diffs=[]
    for r in rows:
        y=r['actual']
        a=sum((r['alternatives'][k][x]-y[x])**2 for x in ('home','draw','away'))
        b=sum((r['alternatives'][0.0][x]-y[x])**2 for x in ('home','draw','away'))
        diffs.append(a-b)
    rng=random.Random(seed)
    means=[]
    n=len(diffs)
    for _ in range(iterations):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n))/n)
    means.sort()
    return {'meanDeltaResult3Brier':round(sum(diffs)/n,6),
            'pairedBootstrap95CI':[round(means[25],6),round(means[975],6)],
            'interpretation':'Negative values favor the experimental model; interval containing zero is inconclusive.'}


def compare(events):
    # Build the knowledge base incrementally so later matches never leak into
    # earlier predictions. Every score becomes available after a 3-hour buffer.
    eligible=[]
    known_by_club=collections.defaultdict(list)
    pointer=0
    for match in events:
        cutoff=match['_kickoff']-dt.timedelta(hours=3)
        while pointer<len(events) and events[pointer]['_kickoff'] <= cutoff:
            past=events[pointer]
            for key in {team_key(past['home']['name']),team_key(past['away']['name'])}:
                if key:
                    known_by_club[key].append(past)
            pointer+=1
        home_key=team_key(match['home']['name'])
        away_key=team_key(match['away']['name'])
        home_history=known_by_club.get(home_key,[])
        away_history=known_by_club.get(away_key,[])
        if min(len(home_history),len(away_history))<10:
            continue
        # calculate() needs ten earlier games per side; at most 20 distinct
        # matches suffice, instead of rescanning the entire multi-year archive.
        subset={str(m['id']):m for m in home_history[-10:]+away_history[-10:]}
        base=calculate(match,list(subset.values()))
        if not base.get('ready') or min(base['nh'],base['na'])<10:
            continue
        h=team_load(home_history,match['home'],match['_kickoff'])
        a=team_load(away_history,match['away'],match['_kickoff'])
        eligible.append({'id':str(match['id']),'date':match['date'],
            'league':match.get('league'), 'home':match['home']['name'],
            'away':match['away']['name'],'actual':truths(match),
            'homeLoad':h,'awayLoad':a,
            'alternatives':{k:candidate_probability(base,h,a,k) for k in CANDIDATES}})
    eligible.sort(key=lambda m:(m['date'],m['id']))
    return eligible

def run(db):
    events,invalid=read_events(db)
    rows=compare(events)
    cutoff=int(0.6*len(rows))
    train,test=rows[:cutoff],rows[cutoff:]
    train_scores={k:evaluate(train,k) for k in CANDIDATES}
    base=train_scores[0.0]['result3Brier'] if train else None
    # Require actual train improvement to choose a nonzero parameter; zero is a valid selection.
    best=(min(CANDIDATES,key=lambda k:train_scores[k]['result3Brier']) if train else 0.0)
    if base is not None and best!=0 and base-train_scores[best]['result3Brier']<0.002:
        best=0.0
    score=evaluate(test,best)
    benchmark=evaluate(test,0.0)
    sub=[r for r in test if max(r['homeLoad']['workload'],r['awayLoad']['workload'])>=0.5]
    report={
        'version':'v35-experimental-observation-only',
        'experiment':'Past 14d match workload and >=3h finalized rest; NOT a validated motivation model',
        'readOnly':True, 'productionPredictionsModified':False,
        'historicalLimitations':[
            'Reconstructed historical forecasts; not previously frozen pre-match predictions.',
            'No official historical table snapshots, UEFA qualification, injuries, lineups or actual rotations used.',
            'Incomplete historical ESPN archive and team-name aliases may omit matches.',
            'Match finalization is approximated as kickoff plus three hours; no real final-status timestamps.',
            'Fixture workload is a proxy; not evidence a team will be less motivated.',
            'Candidate choice uses chronological 60% training sample only; later 40% is holdout.',
            'Current production might use other options; baseline is analysis_engine.calculate(match, history) without Elo parameters.',
            'Results do not imply betting profitability.'
        ],
        'totalCompleted':len(events),'invalidRows':invalid,'eligible':len(rows),
        'train':{'n':len(train),'first':train[0]['date'] if train else None,'last':train[-1]['date'] if train else None,
                 'candidateMetrics':{str(k):v for k,v in train_scores.items()}},
        'test':{'n':len(test),'first':test[0]['date'] if test else None,'last':test[-1]['date'] if test else None,
                'baseline':benchmark,'experimental':score,
                'exploratoryCandidateMetrics':{str(k):evaluate(test,k) for k in CANDIDATES},
                'referenceCandidate006DeltaCI':confidence_interval(test,0.06),
                'highWorkloadBaseline':evaluate(sub,0.0),
                'highWorkloadExperimental':evaluate(sub,best),
                'result3BrierDifference':round(score['result3Brier']-benchmark['result3Brier'],6) if test else None,
                'pairedBootstrap':confidence_interval(test,best)},
        'selectedFatigueCoefficient':best,
        'recommendation':'Keep production unchanged; independent prospective evaluation required before any activation.'
    }
    return report,rows


def main():
    p=argparse.ArgumentParser(description='v35 historical stress proxy comparison (read-only)')
    p.add_argument('--db',type=pathlib.Path,default=ROOT/'match_archive.sqlite3')
    p.add_argument('--json-out',type=pathlib.Path,default=ROOT/'context_comparison_v35.json')
    p.add_argument('--csv-out',type=pathlib.Path,default=ROOT/'context_comparison_v35.csv')
    a=p.parse_args()
    if not a.db.is_file():p.error(f'database missing: {a.db}')
    report,rows=run(a.db)
    a.json_out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with a.csv_out.open('w',newline='',encoding='utf-8-sig') as fd:
        cols=['matchId','date','league','home','away','scoreOutcome','homeRestDays','awayRestDays','homePast14','awayPast14','homeWorkload','awayWorkload','selectedCoefficient','baseline1','baselineX','baseline2','experimental1','experimentalX','experimental2']
        writer=csv.DictWriter(fd,fieldnames=cols);writer.writeheader()
        chosen=report['selectedFatigueCoefficient']
        for r in rows:
            base=r['alternatives'][0.0]; alt=r['alternatives'][chosen]
            writer.writerow({'matchId':r['id'],'date':r['date'],'league':r['league'],
                'home':r['home'],'away':r['away'],
                'scoreOutcome':next(c for c in ('home','draw','away') if r['actual'][c]),
                'homeRestDays':r['homeLoad']['restDays'],'awayRestDays':r['awayLoad']['restDays'],
                'homePast14':r['homeLoad']['past14Days'],'awayPast14':r['awayLoad']['past14Days'],
                'homeWorkload':r['homeLoad']['workload'],'awayWorkload':r['awayLoad']['workload'],
                'selectedCoefficient':chosen,'baseline1':round(base['home'],5),
                'baselineX':round(base['draw'],5),'baseline2':round(base['away'],5),
                'experimental1':round(alt['home'],5),'experimentalX':round(alt['draw'],5),'experimental2':round(alt['away'],5)})
    print(f"V35 CONTEXT BACKTEST: {report['eligible']}/{report['totalCompleted']} eligible; invalid={report['invalidRows']}")
    print(f"Train={report['train']['n']} | Test={report['test']['n']} | Selected workload coefficient={report['selectedFatigueCoefficient']}")
    print(f"Holdout result3 Brier: baseline={report['test']['baseline']['result3Brier']} experimental={report['test']['experimental']['result3Brier']}")
    print(f"Holdout reference k=0.06 Brier={report['test']['exploratoryCandidateMetrics']['0.06']['result3Brier']} (exploratory, not selected)")
    print(f"Holdout high workload n={report['test']['highWorkloadBaseline']['n']}")
    print('NO DEPLOY: model, database, data.json and prediction history unchanged.')
    print('Reports:',a.json_out.name,a.csv_out.name)

if __name__=='__main__':
    main()
