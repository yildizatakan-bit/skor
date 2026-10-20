"""Deterministic pre-match probability model and immutable prediction tracking."""
import math
from datetime import datetime, timezone

MARKETS = ('home','draw','away','homeOrDraw','awayOrDraw','noDraw','over15','under15','over25','under25','over35','under35','btts','noBtts','homeOver05','awayOver05','homeUnder15','awayUnder15','even','odd')

def when(s):
    return datetime.fromisoformat(s.replace('Z','+00:00'))

def recent(events, team_id, kickoff, league=None):
    # No information from this fixture or later matches can enter a prediction.
    rows=[m for m in events if m.get('done') and when(m['date']) < kickoff and (m['home']['id']==team_id or m['away']['id']==team_id) and isinstance(m['home'].get('score'),int) and isinstance(m['away'].get('score'),int)]
    return sorted(rows,key=lambda x:x['date'],reverse=True)[:10]

def form(rows,team_id):
    gf=ga=w=d=l=0
    for m in rows:
        ishome=m['home']['id']==team_id
        x=m['home']['score'] if ishome else m['away']['score']
        y=m['away']['score'] if ishome else m['home']['score']
        gf+=x;ga+=y
        if x>y:w+=1
        elif x==y:d+=1
        else:l+=1
    n=len(rows)
    return {'w':w,'d':d,'l':l,'gf':round(gf/n,3),'ga':round(ga/n,3),'n':n}

def calculate(match,events):
    kick=when(match['date']); h=recent(events,match['home']['id'],kick); a=recent(events,match['away']['id'],kick)
    if len(h)<5 or len(a)<5:return {'ready':False,'nh':len(h),'na':len(a)}
    hf=form(h,match['home']['id']);af=form(a,match['away']['id'])
    lh=min(4.5,max(.15,(hf['gf']+af['ga'])/2*1.12));la=min(4.5,max(.15,(af['gf']+hf['ga'])/2*.9))
    p={k:0.0 for k in MARKETS};mass=0
    hp=[math.exp(-lh)];ap=[math.exp(-la)]
    for i in range(1,16):hp.append(hp[-1]*lh/i);ap.append(ap[-1]*la/i)
    for i in range(16):
        for j in range(16):
            v=hp[i]*ap[j];mass+=v;g=i+j
            p['home' if i>j else 'draw' if i==j else 'away']+=v
            p['over15' if g>1 else 'under15']+=v
            p['over25' if g>2 else 'under25']+=v
            p['over35' if g>3 else 'under35']+=v
            p['btts' if i>0 and j>0 else 'noBtts']+=v
            p['odd' if g%2 else 'even']+=v
            if i>0:p['homeOver05']+=v
            if j>0:p['awayOver05']+=v
            if i<2:p['homeUnder15']+=v
            if j<2:p['awayUnder15']+=v
    for k in p:p[k]=p[k]/mass
    p['homeOrDraw']=p['home']+p['draw'];p['awayOrDraw']=p['away']+p['draw'];p['noDraw']=p['home']+p['away']
    return {'ready':True,'p':{k:round(v,6) for k,v in p.items()},'lh':round(lh,3),'la':round(la,3),'hf':hf,'af':af,'nh':len(h),'na':len(a),'model':'poisson-v2-all-competitions'}

def outcome(k,h,a):
    total=h+a
    return {'home':h>a,'draw':h==a,'away':h<a,'homeOrDraw':h>=a,'awayOrDraw':h<=a,'noDraw':h!=a,'over15':total>1,'under15':total<=1,'over25':total>2,'under25':total<=2,'over35':total>3,'under35':total<=3,'btts':h>0 and a>0,'noBtts':h==0 or a==0,'homeOver05':h>0,'awayOver05':a>0,'homeUnder15':h<2,'awayUnder15':a<2,'even':total%2==0,'odd':total%2==1}[k]

def settle_archive(archive,events):
    lookup={e['id']:e for e in events}
    for row in archive:
        if row.get('settled'):continue
        m=lookup.get(row['matchId'])
        if not m or not m.get('done'):continue
        h=m['home'].get('score');a=m['away'].get('score')
        if not isinstance(h,int) or not isinstance(a,int):continue
        row['settled']=True;row['score']=[h,a]
        row['correct']={k:outcome(k,h,a) for k in row['p']}

def metrics(archive):
    done=[x for x in archive if x.get('settled')]
    # Each match contributes at most one precommitted primary prediction.
    selected=[x for x in done if x.get('primaryMarket') and x['p'][x['primaryMarket']]>=.5]
    wins=sum(x['correct'][x['primaryMarket']] for x in selected)
    return {'evaluatedMatches':len(done),'evaluatedPrimaryPredictions':len(selected),'correctPrimaryPredictions':wins,'primaryHitRate':round(wins/len(selected),4) if selected else None,'method':'One frozen highest-probability market per match, selected before kickoff; not a calibration guarantee'}
