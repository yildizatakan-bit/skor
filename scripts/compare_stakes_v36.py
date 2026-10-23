#!/usr/bin/env python3
"""v36 observational historical league-stakes study (NEVER changes production).

The archived ESPN matches are incomplete, so these are *provisional* title,
upper-table and bottom-table proximity proxies, NOT official qualification,
relegation probabilities, mathematical guarantees or true motivation.
Only matches that were completed >=3h before kickoff enter a historical table.
"""
import argparse
import collections
import csv
import datetime as dt
import json
import math
import pathlib
import random
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from analysis_engine import calculate, team_key
from compare_context_v35 import read_events, goals_distribution, truths, evaluate

DOMESTIC = {'tur.1', 'eng.1', 'eng.2', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'ned.1'}
COEFFS = (0.0, -0.12, -0.06, 0.06, 0.12)
UTC = dt.timezone.utc


def season_for(at):
    return at.year if at.month >= 7 else at.year-1


def update_standings(tables, match):
    league=match.get('league')
    if league not in DOMESTIC:
        return
    t=tables[(league,season_for(match['_kickoff']))]
    home,away=team_key(match['home'].get('name')),team_key(match['away'].get('name'))
    if not home or not away or home==away:
        return
    hs,as_=match['home']['score'],match['away']['score']
    for name,gf,ga in ((home,hs,as_),(away,as_,hs)):
        row=t.setdefault(name,{'p':0,'played':0,'gd':0,'gf':0})
        row['p']+=3 if gf>ga else 1 if gf==ga else 0
        row['gd']+=gf-ga
        row['gf']+=gf
        row['played']+=1


def points_near_cutoff(points,cutoff,scale=5.0):
    return math.exp(-abs(points-cutoff)/scale)


def stakes_snapshot(table, home, away):
    """Only proximity signals from pre-match *observed* standings.
    No inference of official table positions or league qualification rules.
    Conservative coverage gate prevents thin/misaligned archives from qualifying.
    """
    ordered=sorted(table, key=lambda k:(-table[k]['p'],-table[k]['gd'],-table[k]['gf'],k))
    games=[table[k]['played'] for k in ordered]
    n=len(ordered)
    coverage={'teamsObserved':n,'minGames':min(games,default=0),
              'medianGames':statistics.median(games) if games else 0,
              'gameCountSpread':max(games,default=0)-min(games,default=0),
              'status':'partial_unverified'}
    reasons=[]
    if n<14:reasons.append('insufficient_clubs')
    if coverage['medianGames']<7:reasons.append('early_season_or_incomplete_archive')
    if coverage['minGames']<4 or coverage['gameCountSpread']>5:reasons.append('uneven_archive_coverage')
    if home not in table or away not in table:reasons.append('team_missing_from_table')
    if any(table.get(k,{}).get('played',0)<6 for k in (home,away)):reasons.append('insufficient_team_matches')
    if reasons:
        return {'usable':False,'reasons':reasons,'coverage':coverage,'teams':{}}
    leader=table[ordered[0]]['p']
    # Upper-table and bottom-three are *position landmarks*, NOT official
    # UEFA qualification / relegation boundaries. Rules vary across seasons.
    upper=table[ordered[min(3,n-1)]]['p']
    bottom=table[ordered[max(0,n-4)]]['p']
    # Observed pressure grows late in an observed season. Approximation only.
    phase=min(1.0,coverage['medianGames']/34.0)
    def classify(key):
        pts=table[key]['p'];rank=ordered.index(key)+1
        title=math.exp(-(leader-pts)/5.0)
        upper_proximity=points_near_cutoff(pts,upper)
        bottom_proximity=points_near_cutoff(pts,bottom)
        # Avoid treating a dominant leader or last-place club as equally urgent
        # simply because of absolute rank. This is *not* an urgency measurement.
        title_contested=title*(1 if leader-table[ordered[1]]['p'] <= 7 else 0.25)
        signal=phase*(0.45*title_contested+0.25*upper_proximity+0.30*bottom_proximity)
        return {'observedRank':rank,'observedPoints':pts,'games':table[key]['played'],
                'leaderGap':leader-pts,'upperTableProximity':round(upper_proximity,4),
                'bottomTableProximity':round(bottom_proximity,4),
                'titleProximity':round(title_contested,4),
                'seasonProgressProxy':round(phase,4),
                'stakesProxy':round(signal,5)}
    return {'usable':True,'reasons':[],'coverage':coverage,
            'teams':{'home':classify(home),'away':classify(away)}}


def adjusted_probabilities(base, snapshot, coeff):
    if coeff==0 or not snapshot['usable']:
        return {k:base['p'][k] for k in ('home','draw','away','over25','btts')}
    diff=snapshot['teams']['home']['stakesProxy']-snapshot['teams']['away']['stakesProxy']
    # Bounded symmetrical exploratory adjustment, no guessed fixed win bonus.
    lh=min(4.5,max(0.15,base['lh']*math.exp(coeff*diff)))
    la=min(4.5,max(0.15,base['la']*math.exp(-coeff*diff)))
    return goals_distribution(lh,la)


def bootstrap(rows, coeff, iterations=1000):
    if not rows or coeff==0:return None
    diffs=[]
    for r in rows:
        y=r['actual']
        def brier(k):return sum((r['alternatives'][k][col]-y[col])**2 for col in ('home','draw','away'))
        diffs.append(brier(coeff)-brier(0.0))
    rng=random.Random(36)
    means=sorted(sum(diffs[rng.randrange(len(diffs))] for _ in diffs)/len(diffs)
                 for _ in range(iterations))
    return {'meanDeltaBrier':round(statistics.mean(diffs),6),
            'pairedBootstrap95CI':[round(means[24],6),round(means[974],6)],
            'negativeDifferenceFavorsExperimental':True}


def compute(events):
    tables=collections.defaultdict(dict)
    known=collections.defaultdict(list)
    pointer=0
    result=[]
    counters=collections.Counter()
    for m in events:
        cutoff=m['_kickoff']-dt.timedelta(hours=3)
        while pointer<len(events) and events[pointer]['_kickoff']<=cutoff:
            past=events[pointer]
            update_standings(tables,past)
            for key in {team_key(past['home'].get('name')),team_key(past['away'].get('name'))}:
                if key:known[key].append(past)
            pointer+=1
        if m.get('league') not in DOMESTIC:
            counters['notDomestic']+=1
            continue
        hk=team_key(m['home'].get('name'));ak=team_key(m['away'].get('name'))
        if not hk or not ak or hk==ak:
            counters['badTeamKey']+=1;continue
        hhist,ahist=known[hk],known[ak]
        if min(len(hhist),len(ahist))<10:
            counters['under10PriorMatches']+=1;continue
        sample={str(p['id']):p for p in hhist[-10:]+ahist[-10:]}
        base=calculate(m,list(sample.values()))
        if not base.get('ready') or min(base.get('nh',0),base.get('na',0))<10:
            counters['baselineNotReady']+=1;continue
        snapshot=stakes_snapshot(tables[(m['league'],season_for(m['_kickoff']))],hk,ak)
        if not snapshot['usable']:
            for reason in snapshot['reasons']:counters[reason]+=1
            continue
        result.append({'matchId':str(m['id']),'date':m['date'],'league':m.get('league'),
                       'home':m['home']['name'],'away':m['away']['name'],
                       'homeGoals':m['home']['score'],'awayGoals':m['away']['score'],
                       'actual':truths(m),'snapshot':snapshot,
                       'alternatives':{k:adjusted_probabilities(base,snapshot,k) for k in COEFFS}})
    return result,counters


def compare(db_path):
    events,invalid=read_events(db_path)
    rows,counters=compute(events)
    train_end=int(.6*len(rows))
    train,test=rows[:train_end],rows[train_end:]
    train_scores={k:evaluate(train,k) for k in COEFFS}
    if train:
        chosen=min(COEFFS,key=lambda k:train_scores[k]['result3Brier'])
        # Require a meaningful train improvement; zero remains the default.
        if chosen and (train_scores[0.0]['result3Brier']-train_scores[chosen]['result3Brier']<0.002):
            chosen=0.0
    else:chosen=0.0
    base=evaluate(test,0.0)
    selected=evaluate(test,chosen)
    report={'version':'v36-domestic-stakes-proxy-read-only',
            'productionPredictionsModified':False,'readOnly':True,
            'importantScope':'ONLY domestic title / upper-table / bottom-table proximity proxies; UEFA qualification and relegation are NOT established. No actual motivation, lineup or rotation data.',
            'limitations':[
                'Historic standings reconstructed from an incomplete current-day archive, NOT dated official standings snapshots.',
                'Only archived prior completed results available >=3h before kickoff are used; 3h is an approximation of score finalization.',
                'Club names from available archive may be incomplete or aliased.',
                'Upper-table rank four and bottom-three markers are descriptive proxies, NOT actual qualification/relegation cutoffs.',
                'The opponent schedule, guaranteed qualification, UEFA group/knockout status, lineup, injuries and rotation are not modelled.',
                'Fixed proximity formulas are unvalidated; tuning on training and holdout evaluation are exploratory.',
                'Production may use different Elo/opponent correction inputs than the base engine invoked here.',
                'Historical forecasts are reconstructed rather than frozen before kickoff.',
                'No implied probability of betting profits.'
            ],'completedArchive':len(events),'invalidRows':invalid,
            'eligibleQualifiedHistorical':len(rows),'excludedCounts':dict(counters),
            'train':{'n':len(train),'start':train[0]['date'] if train else None,
                     'end':train[-1]['date'] if train else None,
                     'scores':{str(k):v for k,v in train_scores.items()}},
            'holdout':{'n':len(test),'start':test[0]['date'] if test else None,
                       'end':test[-1]['date'] if test else None,'baseline':base,
                       'selectedCandidate':selected,
                       'allCandidatesForDiagnosisOnly':{str(k):evaluate(test,k) for k in COEFFS},
                       'deltaBrier':round(selected['result3Brier']-base['result3Brier'],6) if test else None,
                       'pairedBootstrap':bootstrap(test,chosen)},
            'coefficientChosenOnTrain':chosen,
            'verdict':('INSUFFICIENT_SAMPLE_KEEP_MODEL' if len(test)<100 else
                       'NO_IMPROVEMENT_KEEP_MODEL' if chosen==0 or selected['result3Brier']>=base['result3Brier'] else
                       'PROMISING_NEEDS_INDEPENDENT_PROSPECTIVE_VALIDATION'),
            'modelDeployed':False}
    return report,rows


def main():
    cli=argparse.ArgumentParser(description='Historical stakes proximity proxy, read-only')
    cli.add_argument('--db',type=pathlib.Path,default=ROOT/'match_archive.sqlite3')
    cli.add_argument('--json-out',type=pathlib.Path,default=ROOT/'stakes_comparison_v36.json')
    cli.add_argument('--csv-out',type=pathlib.Path,default=ROOT/'stakes_comparison_v36.csv')
    args=cli.parse_args()
    if not args.db.is_file():cli.error('Database missing: '+str(args.db))
    report,rows=compare(args.db)
    args.json_out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['matchId','date','league','home','away','homeGoals','awayGoals',
            'homeRankObserved','awayRankObserved','homeStakesProxy','awayStakesProxy',
            'homeLeaderGap','awayLeaderGap','tableTeamsObserved','tableMedianGames',
            'chosenCoeff','baselineHome','baselineDraw','baselineAway',
            'experimentalHome','experimentalDraw','experimentalAway']
    with args.csv_out.open('w',newline='',encoding='utf-8-sig') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        c=report['coefficientChosenOnTrain']
        for r in rows:
            s=r['snapshot'];h=s['teams']['home'];a=s['teams']['away']
            b=r['alternatives'][0.0];x=r['alternatives'][c]
            writer.writerow({'matchId':r['matchId'],'date':r['date'],'league':r['league'],
                'home':r['home'],'away':r['away'],'homeGoals':r['homeGoals'],'awayGoals':r['awayGoals'],
                'homeRankObserved':h['observedRank'],'awayRankObserved':a['observedRank'],
                'homeStakesProxy':h['stakesProxy'],'awayStakesProxy':a['stakesProxy'],
                'homeLeaderGap':h['leaderGap'],'awayLeaderGap':a['leaderGap'],
                'tableTeamsObserved':s['coverage']['teamsObserved'],
                'tableMedianGames':s['coverage']['medianGames'],
                'chosenCoeff':c,'baselineHome':round(b['home'],5),'baselineDraw':round(b['draw'],5),
                'baselineAway':round(b['away'],5),'experimentalHome':round(x['home'],5),
                'experimentalDraw':round(x['draw'],5),'experimentalAway':round(x['away'],5)})
    print(f"V36 STAKES: {report['eligibleQualifiedHistorical']}/{report['completedArchive']} eligible, malformed={report['invalidRows']}")
    print('Excluded:', json.dumps(report['excludedCounts'],ensure_ascii=False))
    print(f"Train={report['train']['n']} Holdout={report['holdout']['n']} coefficient={report['coefficientChosenOnTrain']}")
    print('1X2 holdout Brier:', 'baseline=',report['holdout']['baseline']['result3Brier'],
          'experimental=',report['holdout']['selectedCandidate']['result3Brier'])
    print('Verdict:',report['verdict'])
    print('Database/predictions unchanged. Files:',args.json_out.name,args.csv_out.name)

if __name__=='__main__':main()
