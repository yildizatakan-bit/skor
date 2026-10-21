#!/usr/bin/env python3
"""Read-only probe of football-data.org v4 free-plan coverage. Never prints credentials."""
import json, os, urllib.request, urllib.error, urllib.parse, time
TOKEN=os.getenv('FOOTBALL_DATA_TOKEN','').strip()
BASE='https://api.football-data.org/v4'
if not TOKEN:
    raise SystemExit('FOOTBALL_DATA_TOKEN missing from GitHub Actions secrets')
def get(path):
    req=urllib.request.Request(BASE+path,headers={'X-Auth-Token':TOKEN,'Accept':'application/json','User-Agent':'MacAnalizPro/24'})
    try:
        with urllib.request.urlopen(req,timeout=25) as response:
            return json.load(response),None
    except urllib.error.HTTPError as e:
        return None,f'HTTP {e.code}'
    except Exception as e:
        return None,type(e).__name__

print('FOOTBALL-DATA V24 READ-ONLY COVERAGE TEST',flush=True)
comps,err=get('/competitions')
if err:
    print('Competition list unavailable:',err,flush=True)
    raise SystemExit(0)
items=comps.get('competitions',[])
print('Accessible competitions:',len(items),flush=True)
for c in items:
    print('COMP',c.get('code'),c.get('name'),c.get('area',{}).get('name'),flush=True)
# Test only available competitions, and only a few team match endpoints.
# No guesses about club ID; derive IDs from the provider's own /teams response.
targets=['Celtic','Club Brugge','Panathinaikos','FC København','Shakhtar Donetsk','Ajax']
found={}
for c in items:
    code=c.get('code')
    if not code or len(found)==len(targets):break
    teams,err=get('/competitions/'+urllib.parse.quote(code,safe='')+'/teams')
    if err:
        print('TEAM LIST',code,err,flush=True)
        continue
    for t in teams.get('teams',[]):
        name=t.get('name','')
        for target in targets:
            if target.lower() in name.lower() and target not in found:
                found[target]=(t.get('id'),name,code)
    time.sleep(6.2)
for target in targets:
    if target not in found:
        print('UNMATCHED',target,'not found in accessible competition team lists',flush=True)
        continue
    tid,name,code=found[target]
    payload,err=get(f'/teams/{tid}/matches?status=FINISHED&limit=10')
    if err:
        print('HISTORY',target,'competition',code,err,flush=True)
    else:
        games=payload.get('matches',[])
        print('HISTORY',target,'competition',code,'finished_matches',len(games),flush=True)
    time.sleep(6.2)
print('Test complete. No database or predictions modified.',flush=True)
