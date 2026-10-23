#!/usr/bin/env python3
"""Append BSD observations to the EXISTING SQLite football archive.

Never edits original ESPN rows. Duplicates are linked via a separate table.
Publication uses a derived view; old frozen predictions keep their match IDs.
Must run after a verified, durable pre-migration backup.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = 'https://sports.bzzoiro.com/api/v2'
UTC = dt.timezone.utc
DISPLAYED = ('tur.1','eng.1','esp.1','ger.1','ita.1','fra.1','ned.1','eng.2',
             'uefa.champions','uefa.europa','uefa.europa.conf',
             'fifa.world','uefa.euro','uefa.nations','conmebol.america','concacaf.gold.cup','afc.asian.cup','caf.nations')

# Country+name gate prevents confusing, for example, England's Premier League
# with unrelated competitions that also call themselves Premier League.
COMPETITIONS = {
 'tur.1': (('turkey','turkiye','tr'), ('super lig','super league','trendyol super lig')),
 'eng.1': (('england','ingiltere','united kingdom','gb','uk','en'), ('premier league','english premier league')),
 'eng.2': (('england','ingiltere','united kingdom','gb','uk','en'), ('championship','efl championship')),
 'esp.1': (('spain','espana','ispanya','es'), ('la liga','laliga','primera division','la liga ea sports')),
 'ger.1': (('germany','almanya','de'), ('bundesliga','1 bundesliga')),
 'ita.1': (('italy','italia','italya','it'), ('serie a','serie a enilive')),
 'fra.1': (('france','fransa','fr'), ('ligue 1','ligue 1 mcdonalds','ligue 1 uber eats')),
 'ned.1': (('netherlands','holland','hollanda','nl'), ('eredivisie',)),
 'uefa.champions': (('europe','international','uefa','world'), ('uefa champions league','champions league')),
 'uefa.europa': (('europe','international','uefa','world'), ('uefa europa league','europa league')),
 'uefa.europa.conf': (('europe','international','uefa','world'), ('uefa conference league','europa conference league','conference league')),
}
ALIASES = {
 'man utd':'manchester united','man united':'manchester united','man city':'manchester city',
 'psg':'paris saint germain','paris sg':'paris saint germain',
 'atletico de madrid':'atletico madrid','atletico madrid':'atletico madrid',
 'vfb stuttgart':'stuttgart','fc stuttgart':'stuttgart',
 'bayern munich':'bayern munchen','fc bayern munchen':'bayern munchen',
 'borussia m gladbach':'borussia monchengladbach',
 'b monchengladbach':'borussia monchengladbach',
 'inter milan':'internazionale',
}

def key(v):
    s=unicodedata.normalize('NFKD',str(v or '').casefold().replace('ı','i').replace('ß','ss'))
    s=''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9]+',' ',s)).strip()

def team_key(v):
    n=key(v)
    return ALIASES.get(n,n) if len(n)>=4 else ''

def league_code(name,country):
    n,c=key(name),key(country)
    for code,(countries,aliases) in COMPETITIONS.items():
        if n in aliases and (c in countries or (code.startswith('uefa.') and not c)):
            return code
    return None

def parse_time(v):
    try:
        d=dt.datetime.fromisoformat(str(v).replace('Z','+00:00'))
        if d.tzinfo is None:return None
        return d.astimezone(UTC)
    except (ValueError,TypeError):return None

def safe_score(v):
    if v is None or v=='':return None
    try:
        n=int(v)
        return n if str(v).strip() in (str(n),str(n)+'.0') and 0<=n<=99 else None
    except (ValueError,TypeError):return None

def team_name(x):
    if isinstance(x,dict):return str(x.get('name') or x.get('display_name') or '').strip()
    return str(x or '').strip()

def normalize(raw,code):
    if not isinstance(raw,dict):return None
    provider_id=raw.get('id') or raw.get('event_id')
    kick=parse_time(raw.get('event_date') or raw.get('date') or raw.get('start_time'))
    home=team_name(raw.get('home_team') or raw.get('home'))
    away=team_name(raw.get('away_team') or raw.get('away'))
    if not provider_id or not kick or not home or not away or team_key(home)==team_key(away):return None
    status=str(raw.get('status') or '').strip().lower()
    if status not in {'upcoming','live','finished','cancelled','postponed','unresolved'}:return None
    done=status=='finished';live=status=='live'
    def side(s):
        v=raw.get(s+'_team_id')
        if v is None and isinstance(raw.get(s+'_team'),dict):v=raw[s+'_team'].get('id')
        score=safe_score(raw.get(s+'_score')) if done or live else None
        return {'id':str(v or ''),'name':home if s=='home' else away,'logo':'','score':score}
    if (live or done) and (safe_score(raw.get('home_score')) is None or safe_score(raw.get('away_score')) is None):
        # Never claim score 0-0 when API didn't provide both scores.
        return None
    minute=raw.get('current_minute')
    clock=f"{minute}'" if live and minute is not None and str(minute).isdigit() else ''
    return {'source':'BSD','providerId':str(provider_id),'id':'bsd:'+str(provider_id),
            'league':code,'date':kick.isoformat().replace('+00:00','Z'),
            'live':live,'done':done,'status':status,'clock':clock,
            'home':side('home'),'away':side('away')}

def fetch(path,token,timeout=22):
    if not path.startswith('/') or '://' in path:raise ValueError('unsafe path')
    req=urllib.request.Request(BASE+path,headers={'Authorization':'Token '+token,'Accept':'application/json','User-Agent':'MacAnalizPro/45'})
    with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)

def rows(result):
    if isinstance(result,list):return result
    if isinstance(result,dict):
        for p in ('results','events','matches'):
            if isinstance(result.get(p),list):return result[p]
    return []

def leagues_from_api(token):
    collection=[]
    for offset in (0,200):
        j=fetch('/leagues/?limit=200&offset='+str(offset),token)
        collection.extend(rows(j))
        if not j.get('next'):break
    found={};ambiguous=set()
    for l in collection:
        c=l.get('country')
        c=c.get('name') if isinstance(c,dict) else c
        code=league_code(l.get('name'),c)
        if code:
            if code in found:ambiguous.add(code)
            else:found[code]=str(l['id'])
    for c in ambiguous:found.pop(c,None)
    return found,sorted(ambiguous)

def collect(token,now):
    found,ambiguous=leagues_from_api(token)
    if not found:raise RuntimeError('BSD lig eşleştirmesi boş — hiç veri yazılmadı')
    start=(now-dt.timedelta(days=2)).date().isoformat()
    end=(now+dt.timedelta(days=7)).date().isoformat()
    all_matches={};errors=[];raw_n=0
    for code,lid in found.items():
        try:
            for offset in (0,200,400):
                args=urllib.parse.urlencode({'league_id':lid,'date_from':start,'date_to':end,'limit':200,'offset':offset})
                j=fetch('/events/?'+args,token)
                batch=rows(j);raw_n+=len(batch)
                for r in batch:
                    e=normalize(r,code)
                    if e:all_matches[e['providerId']]=e
                if not j.get('next'):break
                if offset==400:errors.append(code+': 3 sayfadan sonrası alınamadı')
        except Exception as exc:
            errors.append(code+': '+type(exc).__name__+': '+str(exc)[:85])
    try:
        for r in rows(fetch('/events/live/',token)):
            lid=str(r.get('league_id') or '')
            code=next((c for c,x in found.items() if x==lid),None)
            if code:
                e=normalize(r,code)
                if e:all_matches[e['providerId']]=e
    except Exception as exc:errors.append('live: '+str(exc)[:100])
    if not all_matches:raise RuntimeError('BSD yeni maç verisi bulunamadı; mevcut arşiv korunuyor. '+str(errors[:2]))
    return list(all_matches.values()),errors,{'leagues':found,'ambiguous':ambiguous,'raw':raw_n}

def tables(db):
    db.executescript('''
CREATE TABLE IF NOT EXISTS bsd_events_current(
 provider_id TEXT PRIMARY KEY, league TEXT NOT NULL, date TEXT NOT NULL,
 observed_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bsd_event_links(
 provider_id TEXT PRIMARY KEY, original_match_id TEXT NOT NULL,
 evidence TEXT NOT NULL, linked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bsd_event_changes(
 provider_id TEXT, observed_at TEXT, payload_sha256 TEXT, payload TEXT,
 PRIMARY KEY(provider_id,payload_sha256));
CREATE TABLE IF NOT EXISTS bsd_team_aliases(
 provider_team_id TEXT, league TEXT, bsd_name TEXT, canonical_name TEXT,
 evidence TEXT, PRIMARY KEY(provider_team_id,league));
CREATE TABLE IF NOT EXISTS bsd_squad_snapshots(
 provider_team_id TEXT, observed_at TEXT, payload TEXT,
 PRIMARY KEY(provider_team_id,observed_at));
CREATE TABLE IF NOT EXISTS bsd_team_profiles(
 provider_team_id TEXT PRIMARY KEY, observed_at TEXT, raw_json TEXT);
CREATE TABLE IF NOT EXISTS bsd_score_conflicts(
 provider_id TEXT, original_match_id TEXT, espn_score TEXT, bsd_score TEXT,
 checked_at TEXT, PRIMARY KEY(provider_id,original_match_id,espn_score,bsd_score));
CREATE TABLE IF NOT EXISTS bsd_players(
 provider_player_id TEXT PRIMARY KEY, provider_team_id TEXT NOT NULL,
 bsd_name TEXT NOT NULL, canonical_name TEXT NOT NULL, name_key TEXT NOT NULL,
 observed_at TEXT NOT NULL, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bsd_sync_meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
''')

def original_events(db):
    return {r[0]:json.loads(r[1]) for r in db.execute("SELECT id,payload FROM matches WHERE id NOT LIKE 'bsd:%'")}

def existing_new(db):
    return {r[0]:json.loads(r[1]) for r in db.execute("SELECT provider_id,payload FROM bsd_events_current")}

def match_existing(e,es,links):
    """Strict matching: exact canonical pair & <=3h or one exact team & <=15m.
    Always same mapped competition. Ambiguous candidates are rejected.
    """
    if e['providerId'] in links:return links[e['providerId']]
    a,b=team_key(e['home']['name']),team_key(e['away']['name'])
    t=parse_time(e['date'])
    candidates=[]
    for m in es.values():
        if m.get('league')!=e['league']:continue
        mt=parse_time(m.get('date'))
        if not mt:continue
        diff=abs((mt-t).total_seconds())
        if diff>3*3600:continue
        h=team_key(m['home']['name'])==a and bool(a)
        w=team_key(m['away']['name'])==b and bool(b)
        if (h and w and diff<=3*3600) or ((h or w) and diff<=15*60):candidates.append(m)
    return candidates[0]['id'] if len(candidates)==1 else None

def canonical_name(raw,existing,code):
    names=collections.defaultdict(set)
    for m in existing.values():
        # Cross-competition aliases only if globally unambiguous names.
        for s in ('home','away'):names[team_key(m[s]['name'])].add(m[s]['name'])
    values=names.get(team_key(raw),set())
    return next(iter(values)) if len(values)==1 else raw

def snapshot_players(db,token,observations,now,max_requests=10):
    # ESPN archive has no player rows; DO NOT claim a cross-provider player-ID match.
    teamids=[]
    for e in observations:
        if e['done']:continue
        for s in ('home','away'):
            tid=e[s]['id']
            if tid and tid.isdigit() and tid not in teamids:teamids.append(tid)
    checked=0;players=0;errors=[]
    for tid in teamids[:max_requests]:
        if db.execute('SELECT 1 FROM bsd_squad_snapshots WHERE provider_team_id=? AND observed_at>? LIMIT 1',
            (tid,(now-dt.timedelta(hours=20)).isoformat())).fetchone():continue
        try:
            team_url='/teams/'+urllib.parse.quote(tid,safe='')+'/'
            team_profile=fetch(team_url,token)
            db.execute('INSERT INTO bsd_team_profiles VALUES (?,?,?) '
                'ON CONFLICT(provider_team_id) DO UPDATE SET observed_at=excluded.observed_at,raw_json=excluded.raw_json',
                (tid,now.isoformat(),json.dumps(team_profile,ensure_ascii=False)))
            j=fetch(team_url+'squad/',token)
            db.execute('INSERT OR IGNORE INTO bsd_squad_snapshots VALUES (?,?,?)',
                      (tid,now.isoformat(),json.dumps(j,ensure_ascii=False,separators=(',',':'))))
            checked+=1
            # Actual BSD response may use a nested squad list; preserve ALL raw data.
            p=rows(j)
            if not p and isinstance(j,dict):
                p=j.get('players') or j.get('squad') or []
            if isinstance(p,dict):p=rows(p)
            for v in p if isinstance(p,list) else []:
                if not isinstance(v,dict):continue
                pid=v.get('id') or v.get('player_id')
                name=team_name(v.get('player') or v.get('name') or v.get('display_name'))
                if pid is None or not name:continue
                db.execute('INSERT INTO bsd_players VALUES (?,?,?,?,?,?,?) '
                  'ON CONFLICT(provider_player_id) DO UPDATE SET provider_team_id=excluded.provider_team_id, '
                  'bsd_name=excluded.bsd_name,canonical_name=excluded.canonical_name,name_key=excluded.name_key, '
                  'observed_at=excluded.observed_at,raw_json=excluded.raw_json',
                  (str(pid),tid,name,name,key(name),now.isoformat(),json.dumps(v,ensure_ascii=False)))
                players+=1
        except Exception as exc:errors.append(tid+':'+str(exc)[:80])
    return {'squads':checked,'players':players,'errors':errors}

def merge_display(original,bsd,link):
    x=json.loads(json.dumps(original))
    if not link:return x
    # ESPN final results are immutable even if BSD later disagrees.
    if x.get('done') and all(isinstance(x[s].get('score'),int) for s in ('home','away')):return x
    # Do not regress a known completed match back to live or upcoming.
    x['done']=bsd['done'] or bool(x.get('done'))
    x['live']=not x['done'] and bsd['live']
    x['clock']=bsd['clock'] if x['live'] else ''
    if bsd['live'] or bsd['done']:
        for s in ('home','away'):x[s]['score']=bsd[s]['score']
    return x

def publish(db,now,errors):
    from analysis_engine import calculate, settle_archive, metrics, when, recent
    original=original_events(db);bsd=existing_new(db)
    links=dict(db.execute('SELECT provider_id,original_match_id FROM bsd_event_links'))
    compiled={k:json.loads(json.dumps(v)) for k,v in original.items()}
    # Only publish BSD fixtures admitted to the main matches table.
    # Quarantined potential duplicates remain provider observations, not fixtures.
    admitted={k:json.loads(p) for k,p in db.execute("SELECT id,payload FROM matches WHERE id LIKE 'bsd:%'")}
    for pid,e in bsd.items():
        if pid in links and links[pid] in compiled:
            compiled[links[pid]]=merge_display(compiled[links[pid]],e,True)
        elif pid not in links and e['id'] in admitted:
            compiled[e['id']]=admitted[e['id']]
    events=list(compiled.values())
    # Never delete historic finished matches from public snapshot.
    future_limit=(now+dt.timedelta(days=8)).isoformat()
    selected=[e for e in events if e.get('date','')<=future_limit and
        (e.get('done') or e.get('league') in DISPLAYED)]
    # frozen predictions remain immutable after kickoff.
    f=ROOT/'prediction_history.json'
    try:archive=json.loads(f.read_text('utf-8')).get('predictions',[])
    except (OSError,ValueError):archive=[]
    if not isinstance(archive,list):archive=[]
    by_id={m.get('matchId') for m in archive if isinstance(m,dict)}
    analyses={};diagnostics={}
    previous={}
    try:previous=json.loads((ROOT/'data.json').read_text('utf-8')).get('analyses',{})
    except (OSError,ValueError):pass
    for m in selected:
        if m['league'] not in DISPLAYED or m.get('done') or when(m['date'])<now-dt.timedelta(hours=4):continue
        # Absolutely no new factors/coefficient changes; same installed engine.
        fp=json.dumps([[(x['id'],x['home']['score'],x['away']['score']) for x in recent(selected,m[s],when(m['date']))]
                      for s in ('home','away')],sort_keys=True)
        cached=previous.get(m['id'],{})
        # Avoid expensive re-evaluation only when all prior effective results match.
        if cached.get('bsd45Fingerprint')==fp and 'p' in cached: result=dict(cached)
        else:
            result=calculate(m,selected)
            result['bsd45Fingerprint']=fp
        if result.get('nh',0)<10 or result.get('na',0)<10:
            result=dict(result);result['ready']=False;result.pop('p',None)
        analyses[m['id']]=result
        diagnostics[m['id']]={'home':m['home']['name'],'away':m['away']['name'],
                              'homeMatches':result.get('nh',0),'awayMatches':result.get('na',0),'ready':bool(result['ready'])}
        if result['ready'] and not m.get('live') and when(m['date'])>now and m['id'] not in by_id:
            primary=max(result['p'],key=result['p'].get)
            archive.append({'matchId':m['id'],'league':m['league'],'kickoff':m['date'],
                            'createdAt':now.isoformat(),'p':result['p'],
                            'primaryMarket':primary,'model':result['model'],'settled':False})
            by_id.add(m['id'])
    settle_archive(archive,selected)
    archive=sorted(archive,key=lambda a:a.get('kickoff',''))[-2000:]
    data={'analyses':analyses,'validation':metrics(archive),'updatedAt':now.isoformat(),
          'events':sorted(selected,key=lambda x:x['date']),'predictionArchive':
          [r for r in archive if r.get('league') in DISPLAYED and r.get('p')],
          'diagnostics':diagnostics,'errors':errors,'historyDays':730,'fixtureDays':7,
          'source':'ESPN historical archive preserved; BSD new fixtures and scores'}
    # Write temp files and atomically swap only after every calculation succeeds.
    write_atomic(f,{'predictions':archive});write_atomic(ROOT/'data.json',data)
    return {'events':len(selected),'completed':sum(bool(e.get('done')) for e in selected),
            'ready':sum(bool(x['ready']) for x in analyses.values())}

def write_atomic(path,obj):
    tmp=path.with_name(path.name+'.bsd45.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,separators=(',',':')),encoding='utf8')
    tmp.replace(path)

def sync(db,observations,now,token,do_squads=True):
    original=original_events(db)
    links=dict(db.execute('SELECT provider_id,original_match_id FROM bsd_event_links'))
    inserted=0;matched=0;review=[];alias_added=0
    for e in observations:
        pid=e['providerId'];match_id=match_existing(e,original,links)
        old=db.execute('SELECT payload FROM bsd_events_current WHERE provider_id=?',(pid,)).fetchone()
        if old:
            old=json.loads(old[0]);
            if old['done'] and not e['done']:e=old
        if match_id and match_id in original:
            match=original[match_id]
            if pid not in links:
                db.execute('INSERT INTO bsd_event_links VALUES (?,?,?,?)',
                    (pid,match_id,'same league, kickoff, unique verified team fixture',now.isoformat()))
                links[pid]=match_id
            for side in ('home','away'):
                tid=e[side]['id'];raw_name=e[side]['name'];canon=match[side]['name']
                if tid and tid.isdigit() and raw_name and canon:
                    # Only a paired, uniquely matched fixture can approve an alias.
                    before=db.execute('SELECT canonical_name FROM bsd_team_aliases WHERE provider_team_id=? AND league=?',(tid,e['league'])).fetchone()
                    if before and before[0]!=canon:
                        review.append('alias conflict for BSD team '+tid)
                    elif not before:
                        db.execute('INSERT INTO bsd_team_aliases VALUES (?,?,?,?,?)',
                                   (tid,e['league'],raw_name,canon,'unique match pair'))
                        alias_added+=1
            if e['done'] and match.get('done') and all(isinstance(match[s].get('score'),int) for s in ('home','away')):
                oldscore=[match['home']['score'],match['away']['score']]
                newscore=[e['home']['score'],e['away']['score']]
                if oldscore!=newscore:
                    db.execute('INSERT OR IGNORE INTO bsd_score_conflicts VALUES (?,?,?,?,?)',
                               (pid,match_id,json.dumps(oldscore),json.dumps(newscore),now.isoformat()))
                    review.append('skor uyuşmazlığı '+match_id)
            matched+=1
        else:
            # If existing ESPN match at approximately same kickoff might be this game,
            # quarantine instead of creating a possible duplicate.
            kick=parse_time(e['date'])
            nearby=[m for m in original.values() if m['league']==e['league'] and
                  parse_time(m.get('date')) and abs((parse_time(m['date'])-kick).total_seconds())<=20*60]
            has_both_names=any(team_key(m['home']['name'])==team_key(e['home']['name']) and
                  team_key(m['away']['name'])==team_key(e['away']['name']) for m in nearby)
            if nearby and any(team_key(m['home']['name']) in (team_key(e['home']['name']),team_key(e['away']['name'])) or team_key(m['away']['name']) in (team_key(e['home']['name']),team_key(e['away']['name'])) for m in nearby) and not has_both_names and not old:
                # We do NOT assume it is a duplicate; it remains in BSD-only table,
                # but no new main archive fixture until confirmed.
                review.append('possible duplicate: '+e['home']['name']+' - '+e['away']['name'])
            else:
                # Preserve any original BSD spelling in bsd_events_current;
                # the derived row has canonical ESPN display names when exact & unique.
                derived=json.loads(json.dumps(e))
                for side in ('home','away'):
                    tid=e[side]['id'];alias=db.execute('SELECT canonical_name FROM bsd_team_aliases WHERE provider_team_id=? AND league=?',
                                                     (tid,e['league'])).fetchone()
                    derived[side]['name']=alias[0] if alias else canonical_name(e[side]['name'],original,e['league'])
                oldrow=db.execute('SELECT payload FROM matches WHERE id=?',(derived['id'],)).fetchone()
                if oldrow:
                    oldm=json.loads(oldrow[0]);
                    if oldm.get('done') and not derived['done']:derived=oldm
                db.execute('INSERT INTO matches(id,league,date,done,payload) VALUES (?,?,?,?,?) '
                  'ON CONFLICT(id) DO UPDATE SET league=excluded.league,date=excluded.date,done=excluded.done,payload=excluded.payload',
                  (derived['id'],derived['league'],derived['date'],int(derived['done']),json.dumps(derived,ensure_ascii=False)))
                inserted+=not bool(oldrow)
        txt=json.dumps(e,ensure_ascii=False,separators=(',',':'))
        digest=hashlib.sha256(txt.encode('utf-8')).hexdigest()
        db.execute('INSERT OR IGNORE INTO bsd_event_changes VALUES (?,?,?,?)',(pid,now.isoformat(),digest,txt))
        db.execute('INSERT INTO bsd_events_current VALUES (?,?,?,?,?) '
                   'ON CONFLICT(provider_id) DO UPDATE SET league=excluded.league,date=excluded.date,observed_at=excluded.observed_at,payload=excluded.payload',
                   (pid,e['league'],e['date'],now.isoformat(),txt))
    squad_info=snapshot_players(db,token,observations,now) if do_squads else {'skipped':True}
    return {'linked':matched,'newArchiveRows':inserted,'aliases':alias_added,'reviewCount':len(review),
            'review':review[:20], 'squads':squad_info}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--input-json',help='local fixture for offline testing; cannot be used for production')
    args=p.parse_args()
    token=os.getenv('BSD_API_TOKEN')
    if not args.input_json and not token:raise SystemExit('BSD_API_TOKEN GitHub secret gerekli')
    if not args.dry_run and args.input_json:raise SystemExit('Production cannot ingest local test fixture')
    if not args.dry_run and os.getenv('BSD_BACKUP_VERIFIED')!='1':
        raise SystemExit('STOP: Önce kalıcı ESPN yedeği doğrulanmalı (BSD_BACKUP_VERIFIED=1)')
    path=ROOT/'match_archive.sqlite3'
    if not path.is_file():raise SystemExit('STOP: Mevcut match_archive.sqlite3 bulunamadı; yeni DB oluşturulmayacak')
    db=sqlite3.connect(':memory:' if args.dry_run else str(path))
    if args.dry_run:
        src=sqlite3.connect(str(path));src.backup(db);src.close()
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('SQLite integrity_check başarısız')
    before=db.execute('SELECT COUNT(*) FROM matches').fetchone()[0]
    now=dt.datetime.now(UTC)
    if args.input_json:
        contents=json.loads(pathlib.Path(args.input_json).read_text('utf8'))
        observations=contents['events'];errors=[];meta={'offlineFixture':True}
    else:
        observations,errors,meta=collect(token,now)
    if not observations:raise SystemExit('BSD: sıfır geçerli maç, veri yazılmadı')
    try:
        tables(db)
        db.execute('BEGIN')
        res=sync(db,observations,now,token or '',do_squads=not args.dry_run and not args.input_json)
        if args.dry_run:
            db.rollback()
            print('DRY RUN — database, predictions, data.json UNCHANGED')
        else:
            # Publish a derived snapshot; original ESPN rows are never updated.
            report=publish(db,now,errors)
            db.commit()
            review_path=ROOT/'bsd_alignment_report.json'
            discrepancies=db.execute('SELECT provider_id,original_match_id,espn_score,bsd_score FROM bsd_score_conflicts LIMIT 30').fetchall()
            approved=db.execute('SELECT provider_team_id,league,bsd_name,canonical_name FROM bsd_team_aliases LIMIT 60').fetchall()
            aliases=[dict(zip(('bsdTeamId','league','bsd','espnCanonical'),r)) for r in approved]
            conflicts=[dict(zip(('bsdMatchId','espnMatchId','espnScore','bsdScore'),r)) for r in discrepancies]
            write_atomic(review_path,{'version':45,'updatedAt':now.isoformat(),
                 'backupVerified':True,'historicalEspnRowsKept':before,
                 'newObservations':len(observations),'linkedFixtures':res['linked'],
                 'newFixtures':res['newArchiveRows'],'approvedTeamAliases':aliases,
                 'potentialDuplicatesOrConflicts':res['review'],
                 'scoreDisagreements':conflicts,
                 'playerComparison':'No historical ESPN player roster in match_archive.sqlite3; BSD snapshots preserved without invented player matches',
                 'teamsAndSquads':res['squads'],'published':report,
                 'providerErrors':errors[:20]})
            print('PUBLISHED',report)
        print('BSD SYNC',json.dumps({'before':before,'after':db.execute('SELECT COUNT(*) FROM matches').fetchone()[0],
                    'received':len(observations),'meta':meta,'result':res,'errors':errors[:5]},ensure_ascii=False))
    except Exception:
        db.rollback();raise
    finally:db.close()

if __name__=='__main__':main()
