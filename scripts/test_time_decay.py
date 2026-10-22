"""Read-only expanding-window calendar-day recency validation."""
import json, math, sqlite3, sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from analysis_engine import calculate,recent,same_team,team_key
from elo_engine import matchup

HALF_LIVES=(14,30,60,120)

def weighted_rates(rows,team,kickoff,half_life):
    # Calendar-day exponential decay, normalized separately for each team's 10 matches.
    from analysis_engine import when
    vals=[]
    for m in rows[:10]:
        days=max(0.,(kickoff-when(m['date'])).total_seconds()/86400)
        weight=2**(-days/half_life)
        home=same_team(m['home'],team)
        vals.append((weight,m['home' if home else 'away']['score'],m['away' if home else 'home']['score']))
    total=sum(x[0] for x in vals)
    return (sum(w*gf for w,gf,ga in vals)/total,sum(w*ga for w,gf,ga in vals)/total)

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
        for half_life in HALF_LIVES:
            kickoff=__import__('analysis_engine').when(m['date'])
            hg,hc=weighted_rates(rh,m['home'],kickoff,half_life);ag,ac=weighted_rates(ra,m['away'],kickoff,half_life)
            # Preserve Elo's existing correction and shrinkage approximately by scaling
            # its estimated goal rates relative to the unweighted goal inputs.
            h0=sum((base['hf']['gf'],base['af']['ga']))/2*1.12
            a0=sum((base['af']['gf'],base['hf']['ga']))/2*.9
            lh=max(.15,min(4.5,base['lh']*((hg+ac)/2*1.12)/max(.15,h0)))
            la=max(.15,min(4.5,base['la']*((ag+hc)/2*.9)/max(.15,a0)))
            scores[str(half_life)]=evaluate(poisson(lh,la),m['home']['score'],m['away']['score'])
        scores['elo_reference']=evaluate(base['p'],m['home']['score'],m['away']['score'])
        rows.append(scores)
    names=('elo_reference',*[str(x) for x in HALF_LIVES])
    metrics=('result3_brier','result3_logLoss','over25_brier','btts_brier')
    def summary(chunk):
        return {name:{metric:round(sum(r[name][metric] for r in chunk)/len(chunk),6) if chunk else None for metric in metrics} for name in names}
    # Expanding-window chronological validation. Tune only on prior games.
    # 40% initial training; next 3 contiguous blocks of 20% each.
    n=len(rows);bounds=[0,int(n*.4),int(n*.6),int(n*.8),n]
    folds=[];wins=[]
    for i in range(1,4):
        train=rows[:bounds[i]];test=rows[bounds[i]:bounds[i+1]]
        train_stats=summary(train);test_stats=summary(test)
        selected=min((str(x) for x in HALF_LIVES),key=lambda name:train_stats[name]['result3_brier'])
        folds.append({'train_n':len(train),'test_n':len(test),'selected':selected,'train':train_stats,'test':test_stats})
        wins.extend([(selected,r[selected],r['elo_reference']) for r in test])
    pooled={metric:round(sum(pred[metric] for _,pred,_ in wins)/len(wins),6) for metric in metrics}
    reference={metric:round(sum(ref[metric] for _,_,ref in wins)/len(wins),6) for metric in metrics}
    report={'method':'Read-only walk-forward; select half-life on earlier games only; three chronological test folds; approximate Elo rate rescaling; no deployment', 'uniqueFinished':len(unique),'eligible':n,'half_lives_days':HALF_LIVES,'folds':folds,'walk_forward_selected':pooled,'walk_forward_reference':reference,'warning':'Historical sample and model implementation are approximate; evaluate against production logic before adoption.'}
    (ROOT/'time_decay_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(f'TIME DECAY WALK-FORWARD: {n}/{len(unique)} eligible')
    for i,f in enumerate(folds,1):
        print(f'FOLD {i}: train={f["train_n"]}, test={f["test_n"]}, selected_half_life={f["selected"]} days')
        print('  TEST selected:',f['test'][f['selected']]);print('  TEST elo_reference:',f['test']['elo_reference'])
    print('POOLED selected:',pooled,'reference:',reference)
    print('Report: time_decay_report.json (read-only; production unchanged)')
if __name__=='__main__':main()
