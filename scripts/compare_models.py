"""Read-only chronological comparison. No model is deployed or fitted."""
import json, math, sqlite3, sys
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from analysis_engine import calculate, recent, same_team, team_key, when
from elo_engine import matchup

MARKETS=('home','draw','away','over25','btts')
def weighted_model(m,events):
    kick=when(m['date']); sides=[]
    for side in ('home','away'):
        rows=recent(events,m[side],kick)
        if len(rows)<10:return None
        weights=[0.88**i for i in range(len(rows))]; sw=sum(weights)
        gf=ga=0.
        for row,w in zip(rows,weights):
            home=same_team(row['home'],m[side]);gf+=w*row['home' if home else 'away']['score'];ga+=w*row['away' if home else 'home']['score']
        sides.append((gf/sw,ga/sw))
    lh=max(.15,min(4.5,(sides[0][0]+sides[1][1])/2*1.12))
    la=max(.15,min(4.5,(sides[1][0]+sides[0][1])/2*.9))
    hp=[math.exp(-lh)];ap=[math.exp(-la)]
    for i in range(1,16):hp.append(hp[-1]*lh/i);ap.append(ap[-1]*la/i)
    p={k:0. for k in MARKETS};mass=0.
    for i in range(16):
        for j in range(16):
            v=hp[i]*ap[j];mass+=v
            p['home' if i>j else 'draw' if i==j else 'away']+=v
            if i+j>2:p['over25']+=v
            if i>0 and j>0:p['btts']+=v
    return {k:v/mass for k,v in p.items()}

def score(p,hs,aws):
    truth={'home':int(hs>aws),'draw':int(hs==aws),'away':int(hs<aws),'over25':int(hs+aws>2),'btts':int(hs>0 and aws>0)}
    out={}
    for k in MARKETS:
        q=max(1e-12,min(1-1e-12,p[k]));y=truth[k]
        out[k]={'brier':(q-y)**2,'logLoss':-(y*math.log(q)+(1-y)*math.log(1-q))}
    out['result3']={'brier':sum((p[k]-truth[k])**2 for k in ('home','draw','away')),'logLoss':-math.log(max(1e-12,p['home' if hs>aws else 'draw' if hs==aws else 'away']))}
    return out

def main():
    db=sqlite3.connect(f'file:{ROOT / "match_archive.sqlite3"}?mode=ro',uri=True)
    raw=[]
    for (payload,) in db.execute('SELECT payload FROM matches WHERE done=1'):
        try:
            m=json.loads(payload)
            if m.get('done') and m.get('date') and team_key(m['home']['name']) and team_key(m['away']['name']) and all(isinstance(m[x].get('score'),int) for x in ('home','away')):raw.append(m)
        except (ValueError,KeyError,TypeError):pass
    db.close()
    unique=[];seen=set()
    for m in sorted(raw,key=lambda x:(x['date'],str(x.get('id','')))):
        key=(m['date'][:10],team_key(m['home']['name']),team_key(m['away']['name']),m['home']['score'],m['away']['score'])
        if key not in seen:seen.add(key);unique.append(m)
    counts=defaultdict(int); rows=[]
    for m in unique:
        h=team_key(m['home']['name']);a=team_key(m['away']['name'])
        eligible=counts[h]>=10 and counts[a]>=10
        counts[h]+=1;counts[a]+=1
        if not eligible:continue
        base=calculate(m,unique)
        if not base.get('ready') or base['nh']!=10 or base['na']!=10:continue
        elo=calculate(m,unique,elo=matchup(unique,m))
        weighted=weighted_model(m,unique)
        if not weighted:continue
        rows.append({'date':m['date'],'scores':{name:score(p,m['home']['score'],m['away']['score']) for name,p in [('baseline',base['p']),('elo',elo['p']),('recency',weighted)]}})
    # Last 30% of eligible chronological matches is a held-out evaluation PERIOD.
    # No parameters are trained, but this split reveals time instability.
    split=int(len(rows)*.7)
    report={'method':'Read-only chronological replay, strict pre-kickoff 10-game histories, no fitted parameters; last 30% is temporal holdout (not independent model training).', 'uniqueFinished':len(unique),'eligible':len(rows),'split':{'earlier':split,'later':len(rows)-split},'models':{}}
    for model in ('baseline','elo','recency'):
        report['models'][model]={}
        for period,subset in [('all',rows),('earlier',rows[:split]),('later',rows[split:])]:
            report['models'][model][period]={key:{metric:round(sum(r['scores'][model][key][metric] for r in subset)/len(subset),5) if subset else None for metric in ('brier','logLoss')} for key in (*MARKETS,'result3')}
    path=ROOT/'model_comparison_report.json';path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(f'MODEL COMPARISON: {len(rows)}/{len(unique)} eligible; earlier={split}, later={len(rows)-split}')
    for name,periods in report['models'].items():
        print(name, 'ALL result3=',periods['all']['result3'],'LATER result3=',periods['later']['result3'],'LATER over25=',periods['later']['over25'],'LATER btts=',periods['later']['btts'])
    print('Report: model_comparison_report.json (database unchanged; no production model changes)')
if __name__=='__main__':main()
