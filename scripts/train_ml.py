#!/usr/bin/env python3
"""Chronological multinomial logistic regression; reads published snapshot only.
Never opens or writes the durable SQLite archive or frozen prediction history.
"""
import json, math, pathlib, datetime
ROOT=pathlib.Path(__file__).resolve().parents[1]
def date(m): return m.get('date','')
def team(m,side): return (m.get(side) or {}).get('name','').strip().casefold()
def features(m,history):
    h=team(m,'home'); a=team(m,'away')
    def recent(name):
        rows=[]
        for x in reversed(history):
            if name not in (team(x,'home'),team(x,'away')): continue
            home=team(x,'home')==name
            hs=x['home']['score']; aw=x['away']['score']
            if not isinstance(hs,int) or not isinstance(aw,int):continue
            gf,ga=(hs,aw) if home else (aw,hs)
            rows.append((gf,ga,1 if gf>ga else 0,1 if gf==ga else 0))
            if len(rows)==10:break
        return rows
    hr=recent(h);ar=recent(a)
    if len(hr)<5 or len(ar)<5:return None
    def stats(rows):
        n=len(rows);return [sum(x[i] for x in rows)/n for i in range(4)]
    x,y=stats(hr),stats(ar)
    return [1.,(x[0]-y[0])/3,(x[1]-y[1])/3,(x[2]-y[2]),(x[3]-y[3]),(x[0]+y[1])/5,(y[0]+x[1])/5,(len(hr)-len(ar))/10]
def probs(w,x):
    z=[sum(a*b for a,b in zip(row,x)) for row in w]
    mx=max(z);v=[math.exp(t-mx) for t in z];s=sum(v)
    return [t/s for t in v]
def fit(rows,epochs=180,lr=.12,reg=.012):
    w=[[0.]*8 for _ in range(3)]
    for epoch in range(epochs):
        grad=[[0.]*8 for _ in range(3)]
        for x,y in rows:
            p=probs(w,x)
            for k in range(3):
                d=p[k]-(k==y)
                for j in range(8):grad[k][j]+=d*x[j]
        n=len(rows)
        for k in range(3):
            for j in range(8):w[k][j]-=lr*(grad[k][j]/n+reg*w[k][j])
    return w
def main():
    source=ROOT/'data.json'
    if not source.exists():raise SystemExit('No published snapshot')
    data=json.loads(source.read_text(encoding='utf-8'))
    events=sorted((m for m in data.get('events',[]) if m.get('date') and m.get('home') and m.get('away')),key=date)
    seen=[];rows=[]
    for m in events:
        x=features(m,seen)
        hs=m['home'].get('score');aw=m['away'].get('score')
        if m.get('done') and isinstance(hs,int) and isinstance(aw,int):
            if x is not None:rows.append((x,0 if hs>aw else 1 if hs==aw else 2))
            seen.append(m)
    out={'model':'multinomial-logistic-v1','method':'chronological 80/20 holdout; no future matches used as features','trainingMatches':0,'validationMatches':0,'metrics':None,'predictions':{},'generatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if len(rows)>=100:
        cut=int(len(rows)*.8)
        w=fit(rows[:cut])
        test=rows[cut:]
        acc=sum(max(range(3),key=lambda k:probs(w,x)[k])==y for x,y in test)/len(test)
        loss=-sum(math.log(max(1e-12,probs(w,x)[y])) for x,y in test)/len(test)
        brier=sum(sum((p-(k==y))**2 for k,p in enumerate(probs(w,x))) for x,y in test)/len(test)
        out.update(trainingMatches=cut,validationMatches=len(test),metrics={'accuracy':round(acc,4),'logLoss':round(loss,4),'brier':round(brier,4)})
        w=fit(rows)
        for m in events:
            if m.get('done'):continue
            x=features(m,seen)
            if x is None:continue
            p=probs(w,x)
            out['predictions'][m['id']]={'home':round(p[0],6),'draw':round(p[1],6),'away':round(p[2],6)}
    (ROOT/'ml_predictions.json').write_text(json.dumps(out,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    print('ML:',len(rows),'historical samples,',out['validationMatches'],'validation,',len(out['predictions']),'predictions')
if __name__=='__main__':main()
