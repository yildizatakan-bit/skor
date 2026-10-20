#!/usr/bin/env python3
"""Fetch public scoreboard data, validate and publish a static snapshot; no dependencies."""
import json, urllib.request, datetime, pathlib, time
LEAGUES=['tur.1','eng.1','esp.1','ger.1','ita.1','fra.1','uefa.champions','uefa.europa','uefa.europa.conf']
ROOT=pathlib.Path(__file__).resolve().parents[1]
def event(e,league):
    c=(e.get('competitions') or [{}])[0]
    comps=c.get('competitors') or []
    h=next((x for x in comps if x.get('homeAway')=='home'),None)
    a=next((x for x in comps if x.get('homeAway')=='away'),None)
    if not h or not a:return None
    done=e.get('status',{}).get('type',{}).get('completed') is True
    def team(t):
        info=t.get('team') or {}
        score=t.get('score')
        return {'id':str(info.get('id','')),'name':info.get('displayName') or info.get('name') or '?','logo':info.get('logo') or ((info.get('logos') or [{}])[0].get('href') or ''),'score':int(score) if done and str(score).isdigit() else None}
    return {'id':league+':'+str(e.get('id')),'league':league,'date':e.get('date'),'done':done,'home':team(h),'away':team(a)}
def main():
    today=datetime.datetime.now(datetime.timezone.utc).date()
    start=(today-datetime.timedelta(days=135)).strftime('%Y%m%d')
    end=(today+datetime.timedelta(days=30)).strftime('%Y%m%d')
    events={}; errors=[]
    for league in LEAGUES:
        url=f'https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={start}-{end}&limit=1000'
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 MaçAnalizPro/4.0','Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=25) as r: raw=json.load(r)
            count=0
            for item in raw.get('events',[]):
                parsed=event(item,league)
                if parsed and parsed['date']:
                    events[parsed['id']]=parsed;count+=1
            if not count:errors.append(f'{league}: boş yanıt')
            print(league,count)
        except Exception as exc:
            errors.append(f'{league}: {type(exc).__name__}: {exc}')
        time.sleep(.3)
    if not events:
        raise RuntimeError('Hiçbir ligden veri gelmedi; önceki snapshot korunuyor: '+str(errors))
    # Keep old data for failed leagues, so transient failures do not empty the UI.
    out=ROOT/'data.json'
    if out.exists():
        try:
            previous=json.loads(out.read_text('utf-8'))
            good={e['league'] for e in events.values()}
            for e in previous.get('events',[]):
                if e['league'] not in good and e['id'] not in events:events[e['id']]=e
        except Exception:pass
    payload={'updatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'events':sorted(events.values(),key=lambda x:x['date']),'errors':errors,'source':'ESPN public scoreboard endpoints; unofficial third-party use'}
    out.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print('Saved',len(events),'events; errors:',errors)
if __name__=='__main__':main()
