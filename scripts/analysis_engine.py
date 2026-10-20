"""Deterministic pre-match probability model and immutable prediction tracking."""
import math
import re
import unicodedata
from datetime import datetime, timezone

MARKETS = ('home','draw','away','homeOrDraw','awayOrDraw','noDraw','over15','under15','over25','under25','over35','under35','btts','noBtts','homeOver05','awayOver05','homeUnder15','awayUnder15','even','odd')

def when(s):
    return datetime.fromisoformat(s.replace('Z','+00:00'))

# Conservative canonical names for known cross-competition ESPN variants.
# Never merge two clubs merely because their numeric IDs match across leagues.
ALIASES = {
    'as roma':'roma', 'roma':'roma', 'roma as':'roma',
    'fc porto':'porto', 'porto':'porto',
    'manchester city':'manchester city', 'man city':'manchester city',
    'ssc napoli':'napoli', 'napoli':'napoli', 'napoli ssc':'napoli',
    'psv eindhoven':'psv', 'psv':'psv',
    'paris saint germain':'paris saint germain', 'paris sg':'paris saint germain', 'psg':'paris saint germain',
    'fc barcelona':'barcelona', 'barcelona':'barcelona',
    'real madrid':'real madrid', 'real madrid cf':'real madrid',
    'atletico madrid':'atletico madrid', 'atletico de madrid':'atletico madrid',
    'vfb stuttgart':'stuttgart', 'stuttgart':'stuttgart',
    'bodo glimt':'bodo glimt', 'fk bodo glimt':'bodo glimt',
    'slovan bratislava':'slovan bratislava', 'sk slovan bratislava':'slovan bratislava',
    'aek athens':'aek athens', 'aek athina':'aek athens',
    'liverpool':'liverpool', 'liverpool fc':'liverpool',
    'villarreal':'villarreal', 'villarreal cf':'villarreal',
}

def team_key(name):
    text=unicodedata.normalize('NFKD', name or '').casefold()
    text=''.join(c for c in text if not unicodedata.combining(c))
    text=re.sub(r'[^a-z0-9]+',' ',text).strip()
    text=re.sub(r'\s+',' ',text)
    return ALIASES.get(text,text) if len(text)>=4 else None

def same_team(side, target):
    x,y=team_key(side.get('name')),team_key(target.get('name'))
    # A numeric ESPN team ID is not assumed globally unique across competitions.
    return bool(x and y and x==y)

def recent(events, team, kickoff, league=None):
    rows=[m for m in events if m.get('done') and when(m['date']) < kickoff
          and (same_team(m['home'],team) or same_team(m['away'],team))
          and isinstance(m['home'].get('score'),int) and isinstance(m['away'].get('score'),int)]
    return sorted(rows,key=lambda x:x['date'],reverse=True)[:10]

def form(rows,team):
    gf=ga=w=d=l=0
    for m in rows:
        ishome=same_team(m['home'],team)
        x=m['home']['score'] if ishome else m['away']['score']
        y=m['away']['score'] if ishome else m['home']['score']
        gf+=x;ga+=y
        if x>y:w+=1
        elif x==y:d+=1
        else:l+=1
    n=len(rows)
    return {'w':w,'d':d,'l':l,'gf':round(gf/n,3),'ga':round(ga/n,3),'n':n}

def calculate(match,events,elo=None,opponent_adjustment=None):
    kick=when(match['date']); h=recent(events,match['home'],kick); a=recent(events,match['away'],kick)
    if len(h)<5 or len(a)<5:return {'ready':False,'nh':len(h),'na':len(a)}
    hf=form(h,match['home']);af=form(a,match['away'])
    lh=min(4.5,max(.15,(hf['gf']+af['ga'])/2*1.12));la=min(4.5,max(.15,(af['gf']+hf['ga'])/2*.9))
    # Elo adjustment is deliberately conservative; incomplete history must not
    # be interpreted as certainty about team strength.
    if elo and min(elo.get('homeMatches',0),elo.get('awayMatches',0)) >= 5:
        delta=max(-350,min(350,elo['home']-elo['away']))
        factor=math.exp(delta / 1000.0)
        lh=min(4.5,max(.15,lh*factor))
        la=min(4.5,max(.15,la/factor))
    # Correct observed attacking strength for the quality of recent opposition.
    # The effect is deliberately small and shrunk toward neutral.
    adj = opponent_adjustment or {}
    ah = max(.92, min(1.08, float(adj.get('home', {}).get('factor', 1.0))))
    aa = max(.92, min(1.08, float(adj.get('away', {}).get('factor', 1.0))))
    lh=min(4.5,max(.15,lh*ah/aa))
    la=min(4.5,max(.15,la*aa/ah))
    # Regression toward a conservative baseline for limited Elo samples.
    if elo:
        confidence=min(1.0, min(elo.get('homeMatches',0),elo.get('awayMatches',0))/25.0)
        lh=1.4+(lh-1.4)*(0.7+0.3*confidence)
        la=1.1+(la-1.1)*(0.7+0.3*confidence)
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
    return {'ready':True,'p':{k:round(v,6) for k,v in p.items()},'lh':round(lh,3),'la':round(la,3),'hf':hf,'af':af,'nh':len(h),'na':len(a),'model':'poisson-v4-opponent-adjusted','elo':elo,'opponentAdjustment':adj,'limitations':['No player or lineup data','Elo ratings relative to available history','Heuristic coefficients not backtested']}

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
