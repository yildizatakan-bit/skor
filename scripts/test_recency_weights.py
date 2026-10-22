"""Read-only chronological recency sensitivity test; never deploys a winner."""
import json, math, sqlite3, sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from analysis_engine import calculate,recent,same_team,team_key
from elo_engine import matchup

WEIGHTS=(0.5,0.65,0.75)

def weighted_rates(rows,team,share):
    # Exactly 10 games, sorted most recent first; each five-game group gets its chosen mass.
    gf=ga=0.0
    for i,m in enumerate(rows[:10]):
        w=(share if i<5 else 1-share)/5
        home=same_team(m['home'],team)
        gf+=w*m['home' if home else 'away']['score']
        ga+=w*m['away' if home else 'home']['score']
    return gf,ga

def poisson(lh,la):
    h=[math.exp(-lh)];a=[math.exp(-la)]
    for i in range(1,16):h.append(h[-1]*lh/i);a.append(a[-1]*la/i)
    p=dict(home=0.,draw=0.,away=0.,over25=0.,btts=0.);mass=0.
    for i in range(16):
        for j in range(16):
            v=h[i]*a[j];mass+=v
            p['home' if i>j else 'draw' if i==j else 'away']+=v
            if i+j>2:p['over25']+=v
            if i and j:p['btts']+=v
    return {k:v/mass for k,v in p.items()}

def evaluate(p,hs,aws):
    truth=dict(home=int(hs>aws),draw=int(hs==aws),away=int(hs<aws),over25=int(hs+aws>2),btts=int(hs>0 and aws>0))
    brier3=sum((p[k]-truth[k])**2 for k in ('home','draw','away'))
    ll3=-math.log(max(1e-12,p['home' if hs>aws else 'draw' if hs==aws else 'away']))
    return dict(result3_brier=brier3,result3_logLoss=ll3,over25_brier=(p['over25']-truth['over25'])**2,btts_brier=(p['btts']-truth['btts'])**2)

def main():
    db=sqlite3.connect(f'file:{ROOT / "match_archive.sqlite3"}?mode=ro',uri=True)
    raw=[]
    for (payload,) in db.execute('SELECT payload FROM matches WHERE done=1'):
        try:
            m=json.loads(payload)
            if m.get('done') and m.get('date') and team_key(m['home']['name']) and team_key(m['away']['name']) and all(isinstance(m[x].get('score'),int) for x in ('home','away')):raw.append(m)
        except (ValueError,TypeError,KeyError):pass
    db.close()
    unique=[];seen=set()
    for m in sorted(raw,key=lambda x:(x['date'],str(x.get('id','')))):
        key=(m['date'][:10],team_key(m['home']['name']),team_key(m['away']['name']),m['home']['score'],m['away']['score'])
        if key not in seen:seen.add(key);unique.append(m)
    counts=defaultdict(int);rows=[]
    for m in unique:
        h=team_key(m['home']['name']);a=team_key(m['away']['name'])
        eligible=counts[h]>=10 and counts[a]>=10
        counts[h]+=1;counts[a]+=1
        if not eligible:continue
        base=calculate(m,unique,elo=matchup(unique,m))
        if not base.get('ready') or base['nh']!=10 or base['na']!=10:continue
        rh=recent(unique,m['home'],__import__('analysis_engine').when(m['date']))
        ra=recent(unique,m['away'],__import__('analysis_engine').when(m['date']))
        scores={}
        for share in WEIGHTS:
            hg,hc=weighted_rates(rh,m['home'],share);ag,ac=weighted_rates(ra,m['away'],share)
            # Preserve Elo's existing correction and shrinkage approximately by scaling
            # its estimated goal rates relative to the unweighted goal inputs.
            h0=sum((base['hf']['gf'],base['af']['ga']))/2*1.12
            a0=sum((base['af']['gf'],base['hf']['ga']))/2*.9
            lh=max(.15,min(4.5,base['lh']*((hg+ac)/2*1.12)/max(.15,h0)))
            la=max(.15,min(4.5,base['la']*((ag+hc)/2*.9)/max(.15,a0)))
            scores[str(share)]=evaluate(poisson(lh,la),m['home']['score'],m['away']['score'])
        scores['elo_reference']=evaluate(base['p'],m['home']['score'],m['away']['score'])
        rows.append(scores)
    split=int(len(rows)*.7)
    def summary(chunk):
        return {name:{metric:round(sum(r[name][metric] for r in chunk)/len(chunk),5) if chunk else None for metric in ('result3_brier','result3_logLoss','over25_brier','btts_brier')} for name in ('elo_reference',*[str(x) for x in WEIGHTS])}
    early=summary(rows[:split]);late=summary(rows[split:]);all_=summary(rows)
    winner=min((str(x) for x in WEIGHTS),key=lambda x:early[x]['result3_brier']) if split else None
    report={'method':'Read-only chronological replay; 10 prior matches per side; recency weights applied to goal averages, approximate Elo scaling; select on early 70%, inspect later 30%; not deployed.', 'uniqueFinished':len(unique),'eligible':len(rows),'split':{'earlier':split,'later':len(rows)-split},'early':early,'later':late,'all':all_,'selected_on_early':winner,'note':'Selection based on 1X2 Brier only; later results are evaluation, not additional tuning.'}
    (ROOT/'recency_weight_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(f'RECENCY TEST: {len(rows)}/{len(unique)} eligible; earlier={split}, later={len(rows)-split}')
    for name in ('elo_reference',*[str(x) for x in WEIGHTS]):print(name,'EARLY',early[name],'LATER',late[name])
    print('Selected on early:',winner,'; compare against elo_reference on LATER. No database or production model changes.')
if __name__=='__main__':main()
