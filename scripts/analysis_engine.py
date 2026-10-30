"""Deterministic pre-match probability model and immutable prediction tracking."""
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone

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
    # Give recently finished matches a three-hour finalization buffer so a
    # result that was still in progress at kickoff cannot leak into features.
    cutoff=kickoff-timedelta(hours=3)
    rows=[m for m in events if m.get('done') and when(m['date']) < cutoff
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
    p.update(expanded_markets(lh,la))
    limitations=['No verified historical odds, starting lineups, injuries, shots or corners',
                 'Three-hour buffer approximates final-score availability']
    limitations.append('Elo ratings use only available completed matches' if elo else 'Elo is disabled in the baseline comparison')
    return {'ready':True,'p':{k:round(v,6) for k,v in p.items()},'lh':round(lh,3),'la':round(la,3),'hf':hf,'af':af,'nh':len(h),'na':len(a),'model':'poisson-v5-elo' if elo else 'poisson-v4-baseline','elo':elo,'opponentAdjustment':adj,'limitations':limitations}

# Additional markets are derived from the SAME full-time score distribution.
# No extra API calls, no independent models and no multiplication of correlated events.
def expanded_markets(lh, la):
    keys = ['over05','under05','over45','under45','over55','under55',
            'homeUnder05','homeOver15','homeUnder25','homeOver25',
            'awayUnder05','awayOver15','awayUnder25','awayOver25',
            'goals01','goals23','goals45','goals6plus',
            'marginHome1','marginHome2','marginHome3plus','marginAway1','marginAway2','marginAway3plus',
            
            'homeOver15Win','homeOver25Win','awayOver15Win','awayOver25Win',
            'homeWinBtts','awayWinBtts','over25Btts','under25Btts']
    p={k:0.0 for k in keys}
    hp=[math.exp(-lh)]; ap=[math.exp(-la)]
    for i in range(1,21):hp.append(hp[-1]*lh/i);ap.append(ap[-1]*la/i)
    mass=0.0
    for i in range(21):
        for j in range(21):
            v=hp[i]*ap[j];mass+=v;g=i+j
            conditions={
                'over05':g>=1,'under05':g==0,'over45':g>=5,'under45':g<=4,'over55':g>=6,'under55':g<=5,
                'homeUnder05':i==0,'homeOver15':i>=2,'homeUnder25':i<=2,'homeOver25':i>=3,
                'awayUnder05':j==0,'awayOver15':j>=2,'awayUnder25':j<=2,'awayOver25':j>=3,
                'goals01':g<=1,'goals23':2<=g<=3,'goals45':4<=g<=5,'goals6plus':g>=6,
                'marginHome1':i-j==1,'marginHome2':i-j==2,'marginHome3plus':i-j>=3,
                'marginAway1':j-i==1,'marginAway2':j-i==2,'marginAway3plus':j-i>=3,
                'homeOver15Win':i>j and g>=2,'homeOver25Win':i>j and g>=3,
                'awayOver15Win':j>i and g>=2,'awayOver25Win':j>i and g>=3,
                'homeWinBtts':i>j and j>0,'awayWinBtts':j>i and i>0,
                'over25Btts':g>=3 and i>0 and j>0,'under25Btts':g<=2 and i>0 and j>0,
            }
            for k,yes in conditions.items():
                if yes:p[k]+=v
    return {k:round(v/mass,6) for k,v in p.items()}

def outcome(k,h,a):
    total=h+a
    return {'home':h>a,'draw':h==a,'away':h<a,'homeOrDraw':h>=a,'awayOrDraw':h<=a,'noDraw':h!=a,'over15':total>1,'under15':total<=1,'over25':total>2,'under25':total<=2,'over35':total>3,'under35':total<=3,'btts':h>0 and a>0,'noBtts':h==0 or a==0,'homeOver05':h>0,'awayOver05':a>0,'homeUnder15':h<2,'awayUnder15':a<2,'even':total%2==0,'odd':total%2==1,
            'over05':total>=1,'under05':total==0,'over45':total>=5,'under45':total<=4,'over55':total>=6,'under55':total<=5,
            'homeUnder05':h==0,'homeOver15':h>=2,'homeUnder25':h<=2,'homeOver25':h>=3,
            'awayUnder05':a==0,'awayOver15':a>=2,'awayUnder25':a<=2,'awayOver25':a>=3,
            'goals01':total<=1,'goals23':2<=total<=3,'goals45':4<=total<=5,'goals6plus':total>=6,
            'marginHome1':h-a==1,'marginHome2':h-a==2,'marginHome3plus':h-a>=3,
            'marginAway1':a-h==1,'marginAway2':a-h==2,'marginAway3plus':a-h>=3,
            'homeOver15Win':h>a and total>=2,'homeOver25Win':h>a and total>=3,
            'awayOver15Win':a>h and total>=2,'awayOver25Win':a>h and total>=3,
            'homeWinBtts':h>a and a>0,'awayWinBtts':a>h and h>0,
            'over25Btts':total>=3 and h>0 and a>0,'under25Btts':total<=2 and h>0 and a>0}[k]

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

def settle_model_comparison(archive,events):
    """Settle only new, frozen A/B snapshots; never rewrites old score data."""
    lookup={e['id']:e for e in events}
    for row in archive:
        if row.get('settled') or not isinstance(row.get('models'),dict):continue
        match=lookup.get(row.get('matchId'))
        if not match or not match.get('done'):continue
        h=match.get('home',{}).get('score');a=match.get('away',{}).get('score')
        if isinstance(h,int) and not isinstance(h,bool) and isinstance(a,int) and not isinstance(a,bool):
            row['settled']=True;row['score']=[h,a]

def model_comparison_metrics(archive,confidence_threshold=.60):
    """Prospective, same-match comparison for baseline vs Elo probabilities."""
    done=[r for r in archive if r.get('settled') and isinstance(r.get('score'),list)
          and len(r['score'])==2 and isinstance(r.get('models'),dict)]
    outcomes=('home','draw','away')
    actuals=[]
    for r in done:
        h,a=r['score']
        if not isinstance(h,int) or isinstance(h,bool) or not isinstance(a,int) or isinstance(a,bool):continue
        actuals.append((r,0 if h>a else 1 if h==a else 2))
    def summarize(rows,key):
        valid=[];hits=0;brier=0.0;logloss=0.0;confident=0;confident_hits=0
        for row,actual in rows:
            p=row['models'].get(key,{})
            values=[p.get(name) for name in outcomes]
            if any(not isinstance(x,(int,float)) or not 0<=x<=1 for x in values):continue
            valid.append((row,actual,values))
            pick=max(range(3),key=lambda i:values[i]);hit=pick==actual
            hits+=int(hit)
            brier+=sum((values[i]-int(i==actual))**2 for i in range(3))
            logloss-=math.log(max(values[actual],1e-12))
            if values[pick]>=confidence_threshold:
                confident+=1;confident_hits+=int(hit)
        n=len(valid)
        return {'correct':hits,'total':n,'hitRate':round(hits/n,4) if n else None,
                'brier':round(brier/n,5) if n else None,'logLoss':round(logloss/n,5) if n else None,
                'highConfidence':{'threshold':confidence_threshold,'correct':confident_hits,
                    'total':confident,'rate':round(confident_hits/confident,4) if confident else None,
                    'coverage':round(confident/n,4) if n else None}}
    return {'evaluatedMatches':len(actuals),
            'models':{name:summarize(actuals,name) for name in ('baseline','elo')},
            'method':'Prospective frozen predictions on the same fixtures; highest 1-X-2 probability; high-confidence rate includes coverage.'}
