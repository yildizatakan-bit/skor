#!/usr/bin/env python3
"""Collect 7-day fixtures and past results; derive statistics locally in the PWA."""
import datetime as dt
import json
import sqlite3
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from analysis_engine import calculate, settle_archive, metrics, when

LEAGUES = ['tur.1','eng.1','esp.1','ger.1','ita.1','fra.1','ned.1','eng.2','uefa.champions','uefa.europa','uefa.europa.conf','fifa.world','uefa.euro','uefa.nations','conmebol.america','concacaf.gold.cup','afc.asian.cup','caf.nations']
# Extra competitions are used ONLY for historical team form, not displayed as fixtures.
# ESPN codes are unofficial; unsupported competitions are skipped gracefully.
CUP_LEAGUES = ['tur.cup','eng.fa','eng.league_cup','esp.copa_del_rey','ger.dfb_pokal','ita.coppa_italia','fra.coupe_de_france', 'ned.knvb_beker', 'fifa.worldq.uefa', 'fifa.worldq.conmebol', 'fifa.worldq.concacaf', 'fifa.worldq.afc', 'fifa.worldq.caf', 'fifa.worldq.ofc', 'fifa.friendly']
ROOT = pathlib.Path(__file__).resolve().parents[1]
TODAY = dt.datetime.now(dt.timezone.utc).date()
HISTORY_DAYS = 730
FUTURE_DAYS = 7


def parse_event(e, league):
    comp = (e.get('competitions') or [{}])[0]
    sides = comp.get('competitors') or []
    home = next((x for x in sides if x.get('homeAway') == 'home'), None)
    away = next((x for x in sides if x.get('homeAway') == 'away'), None)
    if not home or not away or not e.get('date'):
        return None
    status = e.get('status') or {}
    status_type = status.get('type') or {}
    completed = status_type.get('completed') is True
    live = not completed and str(status_type.get('state') or '').lower() == 'in'
    clock = status.get('displayClock') or status.get('clock') or ''
    def team(t):
        info = t.get('team') or {}
        logos = info.get('logos') or []
        score = t.get('score')
        return {'id': str(info.get('id') or ''),
                'name': info.get('displayName') or info.get('name') or '?',
                'logo': info.get('logo') or (logos[0].get('href') if logos else '') or '',
                'score': int(score) if (completed or live) and str(score).isdigit() else None}
    return {'id': league + ':' + str(e.get('id')), 'league': league,
            'date': e['date'], 'done': completed, 'live': live, 'clock': str(clock), 'home': team(home), 'away': team(away)}


def request(league, start, end=None):
    date_arg = start.strftime('%Y%m%d')
    if end is not None and end != start:
        date_arg += '-' + end.strftime('%Y%m%d')
    url = ('https://site.api.espn.com/apis/site/v2/sports/soccer/'
           + urllib.parse.quote(league, safe='') + '/scoreboard?'
           + urllib.parse.urlencode({'dates': date_arg, 'limit': 200}))
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=12) as response:
        data = json.load(response)
    return [v for e in data.get('events', []) if (v := parse_event(e, league))]


def team_schedule(league, team_id, season):
    """Read ESPN team schedule and return parsed events plus diagnostics.

    A 200 response with zero events is not evidence of an available history.
    """
    base = ('https://site.api.espn.com/apis/site/v2/sports/soccer/'
            + urllib.parse.quote(league, safe='') + '/teams/'
            + urllib.parse.quote(str(team_id), safe='') + '/schedule')
    url = base + '?' + urllib.parse.urlencode({'season': season, 'limit': 500})
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0', 'Accept':'application/json'})
    with urllib.request.urlopen(req, timeout=15) as response:
        payload = json.load(response)
    raw = payload.get('events') or []
    return [m for e in raw if (m := parse_event(e, league)) is not None], len(raw), list(payload)[:8]


def merge_events(events, rows):
    added = 0
    for m in rows:
        old = events.get(m['id'])
        if old is None or (m.get('done') and not old.get('done')):
            events[m['id']] = m
            if m.get('done') and not (old or {}).get('done'):
                added += 1
    return added


def targeted_scoreboard_history(league, team_id, kickoff, events, target=10):
    """Fallback when ESPN team schedules are empty: scan league scoreboard.

    Keep requests bounded and stop when ten completed games are found for
    the exact competition-scoped ESPN team ID. This is a *fallback*, not
    proof that all competitions for the club have been discovered.
    """
    end = min(TODAY, kickoff.date() - dt.timedelta(days=1))
    found = 0
    failed = 0
    added = 0
    # Six 30-day windows = 180 days; future runs may revisit older seasons.
    for offset in range(0, 180, 30):
        hi = end - dt.timedelta(days=offset)
        lo = hi - dt.timedelta(days=29)
        try:
            rows = request(league, lo, hi)
            found += sum(1 for m in rows if m.get('done') and
                         (m['home']['id'] == str(team_id) or m['away']['id'] == str(team_id)))
            added += merge_events(events, rows)
        except Exception:
            failed += 1
        n = sum(1 for m in events.values() if m.get('done') and
                m['league'] == league and when(m['date']) < kickoff and
                (m['home']['id'] == str(team_id) or m['away']['id'] == str(team_id)))
        if n >= target:
            break
    return added, found, failed


# Domestic competitions for clubs appearing in European fixtures. Extend this
# mapping as new clubs are encountered; an unverified league is never guessed.
DOMESTIC_CLUBS = {
    'den.1': 'FC København|FC Midtjylland|FC Nordsjælland|AGF|Brøndby|Randers',
    'sco.1': 'Celtic|Rangers|Heart of Midlothian|Hibernian|Aberdeen',
    'ukr.1': 'Shakhtar Donetsk|Dynamo Kyiv',
    'nor.1': 'Viking FK|SK Brann|Lillestrom|Bodø/Glimt|Molde',
    'bel.1': 'Club Brugge|KAA Gent|Union St.-Gilloise|Anderlecht|Genk',
    'gre.1': 'Panathinaikos|Olympiacos|PAOK|AEK Athens|OFI Crete',
    'aut.1': 'SK Sturm Graz|RB Salzburg|LASK Linz',
    'sui.1': 'FC Lugano|FC Thun|Young Boys|Basel',
    'cro.1': 'Dinamo Zagreb|Hajduk Split',
    'cze.1': 'Sparta Prague|Jablonec|Viktoria Plzen|Slavia Prague',
    'pol.1': 'Lech Poznan|Jagiellonia Bialystok|Legia Warsaw',
    'hun.1': 'Ferencvaros',
    'srb.1': 'Red Star Belgrade|Partizan',
    'cyp.1': 'Omonia Nicosia|Pafos|APOEL',
    'bul.1': 'CSKA Sofia|Levski Sofia|Ludogorets',
    'rou.1': 'CSU Craiova|FCSB|CFR Cluj',
    'svk.1': 'Slovan Bratislava',
    'swe.1': 'Mjällby AIF|Malmö FF|AIK',
    'irl.1': 'Shamrock Rovers',
    'isr.1': "Hapoel Be'er|Maccabi Tel Aviv|Maccabi Haifa",
    'kaz.1': 'Kairat Almaty|Astana',
    'fin.1': 'KuPS Kuopio|HJK Helsinki',
    'svn.1': 'NK Celje|Maribor',
    'arm.1': 'Ararat-Armenia',
    'geo.1': 'Iberia 1999',
    'and.1': "Inter D'Escaldes",
    'gib.1': 'Lincoln Red Imps',
    'alb.1': 'Egnatia',
    'lat.1': 'Riga FC',
    'ltu.1': 'Kauno Zalgiris',
    'bih.1': 'Borac Banja Luka',
}

def normalized_name(name):
    name = unicodedata.normalize('NFKD', str(name or '')).casefold()
    return ''.join(c for c in name if c.isalnum() and not unicodedata.combining(c))

DOMESTIC_BY_NAME = {normalized_name(name): league
                    for league, names in DOMESTIC_CLUBS.items()
                    for name in names.split('|')}

def domestic_history(name, kickoff, cache):
    """Look up a club by *name* in its domestic competition's scoreboards.

    ESPN team ids are competition-scoped. Never reuse a European competition id
    in a domestic league. Scan the league, match the club name, and preserve
    all completed events, which may also help its domestic opponents.
    """
    league = DOMESTIC_BY_NAME.get(normalized_name(name))
    if not league:
        return None, [], 0
    end = min(TODAY, kickoff.date() - dt.timedelta(days=1))
    rows = []
    failed = 0
    for offset in range(0, 120, 14):
        hi = end - dt.timedelta(days=offset)
        lo = hi - dt.timedelta(days=13)
        key = (league, lo, hi)
        if key not in cache:
            try:
                cache[key] = (request(league, lo, hi), False)
            except Exception:
                cache[key] = ([], True)
        batch, error = cache[key]
        failed += int(error)
        rows.extend(batch)
        matches = [m for m in rows if m.get('done') and
                   any(normalized_name(m[side]['name']) == normalized_name(name)
                       for side in ('home','away'))]
        if len(matches) >= 10:
            break
    # Store all verified results for the league, not just one team.
    return league, rows, failed


def fill_team_gaps(db, events, competition_codes):
    """Target teams with fewer than ten finished games, without deleting old rows.

    Bounded work per run; team schedules can be missing or incomplete in ESPN.
    """
    from analysis_engine import recent, team_key, when
    db.execute('CREATE TABLE IF NOT EXISTS team_backfill (team_key TEXT PRIMARY KEY, '
               'last_checked TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)')
    now = dt.datetime.now(dt.timezone.utc)
    future = sorted((m for m in events.values() if m['league'] in LEAGUES
                     and not m.get('done') and when(m['date']) >= now - dt.timedelta(hours=4)
                     and when(m['date']) <= now + dt.timedelta(days=FUTURE_DAYS)),
                    key=lambda m:m['date'])
    candidates = {}
    for m in future:
        for side in ('home','away'):
            team = m[side]
            key = team_key(team.get('name'))
            if not key or not team.get('id'):
                continue
            if key not in candidates:
                candidates[key] = {'team':team, 'kickoff':when(m['date']), 'leagues':[]}
            if m['league'] not in candidates[key]['leagues']:
                candidates[key]['leagues'].append(m['league'])
    todo=[]
    for key, item in candidates.items():
        n=len(recent(list(events.values()), item['team'], item['kickoff']))
        if n >= 10: continue
        row=db.execute('SELECT last_checked,attempts FROM team_backfill WHERE team_key=?',(key,)).fetchone()
        # Prioritize never-checked teams, then oldest attempts; never starve other teams.
        todo.append((row[0] if row else '', n, key, item, (row[1] if row else 0)))
    todo.sort(key=lambda x:(x[0], x[1], x[2]))
    limit=max(1,int(os.environ.get('TEAM_BATCH','12')))
    selected=todo[:limit]
    print(f'TEAM BACKFILL: {len(candidates)} teams in fixture window, '
          f'{len(todo)} below ten, checking {len(selected)}', flush=True)
    domestic_cache = {}
    for _, before, key, item, attempts in selected:
        team=item['team']; added=0; successful=0; errors=[]; raw_events=0; fallback_calls=0; fallback_found=0
        # The source fixture's competition is the only safe place to reuse its ESPN team id.
        # Additional leagues are discovered via matching team names from known events.
        sources=[]
        for league in item['leagues']:
            sources.append((league,team['id']))
        for m in events.values():
            if not m.get('done'):continue
            for side in ('home','away'):
                t=m[side]
                if team_key(t.get('name'))==key and t.get('id'):
                    pair=(m['league'],t['id'])
                    if pair not in sources:sources.append(pair)
        # Limit expensive remote requests; fallback competition sweep still runs separately.
        for league, tid in sources[:4]:
            if league not in competition_codes:continue
            for season in (TODAY.year, TODAY.year-1):
                try:
                    rows, raw_count, keys = team_schedule(league,tid,season)
                    successful+=1
                    raw_events+=raw_count
                    added+=merge_events(events,rows)
                    if raw_count == 0 and attempts == 0:
                        print(f'  EMPTY SCHEDULE {league}/{tid}/{season}; response keys={keys}',flush=True)
                except Exception as exc:
                    errors.append(f'{league}/{season}: {type(exc).__name__}')
            # ESPN may return HTTP 200 with an empty team schedule.
            # Use the same competition scoreboard as a bounded fallback.
            if len(recent(list(events.values()),team,item['kickoff']))<10 and raw_events == 0:
                nadd, nfound, nfailed = targeted_scoreboard_history(
                    league,tid,item['kickoff'],events)
                added+=nadd; fallback_calls+=1; fallback_found+=nfound
                if nfailed: errors.append(f'{league}: {nfailed} scoreboard windows failed')
            if len(recent(list(events.values()),team,item['kickoff']))>=10:break
        domestic_league = None
        domestic_matches = 0
        if len(recent(list(events.values()), team, item['kickoff'])) < 10:
            domestic_league, domestic_rows, domestic_failures = domestic_history(
                team['name'], item['kickoff'], domestic_cache)
            if domestic_league:
                domestic_matches = sum(1 for m in domestic_rows if m.get('done') and
                    any(normalized_name(m[side]['name']) == normalized_name(team['name'])
                        for side in ('home','away')))
                added += merge_events(events, domestic_rows)
                if domestic_failures:
                    errors.append(f'{domestic_league}: {domestic_failures} domestic windows failed')
        after=len(recent(list(events.values()),team,item['kickoff']))
        db.execute('INSERT INTO team_backfill(team_key,last_checked,attempts) VALUES(?,?,?) '
                   'ON CONFLICT(team_key) DO UPDATE SET last_checked=excluded.last_checked,'
                   'attempts=excluded.attempts',
                   (key,now.isoformat(),attempts+1))
        print(f'TEAM {team["name"]}: {before}-> {after}/10; new finals={added}; '
              f'schedule calls ok={successful}; raw events={raw_events}; fallback leagues={fallback_calls}; fallback team matches={fallback_found}; domestic={domestic_league or "unmapped"}; domestic matches={domestic_matches}; failures={len(errors)}'
              + (f' ({", ".join(errors[:2])})' if errors else ''),flush=True)
    db.commit()


def main():
    path = ROOT / 'data.json'
    previous = {}
    if path.exists():
        try:
            previous = {e['id']: e for e in json.loads(path.read_text('utf-8')).get('events', []) if e.get('id')}
        except (ValueError, KeyError, TypeError):
            pass
    # Persistent SQLite history survives regenerated data.json snapshots.
    db = sqlite3.connect(str(ROOT / 'match_archive.sqlite3'))
    db.execute('CREATE TABLE IF NOT EXISTS matches (id TEXT PRIMARY KEY, league TEXT NOT NULL, date TEXT NOT NULL, done INTEGER NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS seeded_leagues (league TEXT PRIMARY KEY, seeded_at TEXT NOT NULL)')
    for (payload,) in db.execute('SELECT payload FROM matches'):
        try:
            old = json.loads(payload)
            if old.get('id') and old.get('done'):
                previous[old['id']] = old if not previous.get(old['id'], {}).get('done') else previous[old['id']]
        except (ValueError, TypeError):
            continue
    events = dict(previous)
    errors = []
    # Fetch recent results for every competition, then progressively backfill
    # ALL competitions instead of spending an entire run on a single league.
    first = TODAY - dt.timedelta(days=HISTORY_DAYS)
    last = TODAY + dt.timedelta(days=FUTURE_DAYS)
    scan_leagues = list(dict.fromkeys(LEAGUES + CUP_LEAGUES))
    db.execute('CREATE TABLE IF NOT EXISTS backfill_progress (league TEXT PRIMARY KEY, next_end TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0)')
    db.commit()

    def fetch_range(league, start, end):
        """Try a 14-day window; recursively split on endpoint errors.
        A failed day is logged but does not erase previously saved matches.
        """
        try:
            return request(league, start, end), 0
        except Exception as exc:
            if start == end:
                errors.append(f'{league} {start}: {type(exc).__name__}: {exc}')
                return [], 1
            middle = start + dt.timedelta(days=(end-start).days // 2)
            left, nf1 = fetch_range(league, start, middle)
            right, nf2 = fetch_range(league, middle + dt.timedelta(days=1), end)
            return left + right, nf1 + nf2

    # Short refresh is cheap and keeps scores/fixtures current.
    for league in scan_leagues:
        rows, failures = fetch_range(league, TODAY - dt.timedelta(days=3), last)
        for e in rows:
            events[e['id']] = e if not (events.get(e['id'], {}).get('done') and not e.get('done')) else events[e['id']]
        print(f'{league}: recent {len(rows)} events, {failures} unavailable days', flush=True)

    # Target the teams that actually appear in upcoming fixtures first.
    fill_team_gaps(db, events, scan_leagues)

    # A bounded historical slice per competition each run. All leagues progress
    # together, including club cups and international competitions used for form.
    # A 90-day slice is enough to reach ten games for most active teams quickly.
    batch = max(1, int(os.environ.get('SEED_BATCH', '6')))
    slice_days = max(14, int(os.environ.get('SEED_DAYS', '90')))
    incomplete = []
    for league in scan_leagues:
        row = db.execute('SELECT next_end,completed FROM backfill_progress WHERE league=?', (league,)).fetchone()
        if row is None:
            incomplete.append((league, TODAY - dt.timedelta(days=4)))
        elif not row[1]:
            incomplete.append((league, dt.date.fromisoformat(row[0])))
    # Rotate between competitions on successive runs; never repeatedly restart
    # the Turkish league after one isolated ESPN failure.
    # Rotate by the least recently progressed cursor (new leagues first).
    # Otherwise the first six competitions monopolize every run.
    chosen = sorted(incomplete, key=lambda x: (x[1] != TODAY - dt.timedelta(days=4), -x[1].toordinal()))[:batch]
    print('Historical backfill batch:', [x[0] for x in chosen], flush=True)
    for league, end_date in chosen:
        start_date = max(first, end_date - dt.timedelta(days=slice_days-1))
        found = []
        failures = 0
        day = start_date
        while day <= end_date:
            chunk_end = min(end_date, day + dt.timedelta(days=13))
            rows, failed = fetch_range(league, day, chunk_end)
            found.extend(rows)
            failures += failed
            day = chunk_end + dt.timedelta(days=1)
        for e in found:
            events[e['id']] = e if not (events.get(e['id'], {}).get('done') and not e.get('done')) else events[e['id']]
        finished = start_date <= first
        next_end = (start_date - dt.timedelta(days=1)).isoformat()
        db.execute('INSERT INTO backfill_progress(league,next_end,completed) VALUES(?,?,?) '
                   'ON CONFLICT(league) DO UPDATE SET next_end=excluded.next_end,completed=excluded.completed',
                   (league, next_end, int(finished)))
        if finished:
            db.execute('INSERT OR REPLACE INTO seeded_leagues VALUES (?,?)',
                       (league, dt.datetime.now(dt.timezone.utc).isoformat()))
        db.commit()
        print(f'BACKFILL {league}: {start_date}..{end_date}; {len(found)} events; '
              f'{failures} unavailable days; complete={finished}', flush=True)
    print(f'Historical progress: {db.execute("SELECT COUNT(*) FROM backfill_progress WHERE completed=1").fetchone()[0]}'
          f'/{len(scan_leagues)} competitions fully scanned', flush=True)
    # Upsert only observed fixtures; never remove prior completed matches.
    for event in events.values():
        if not event.get('id') or not event.get('date'):
            continue
        db.execute('INSERT INTO matches (id,league,date,done,payload) VALUES (?,?,?,?,?) '
                   'ON CONFLICT(id) DO UPDATE SET league=excluded.league,date=excluded.date,'
                   'done=excluded.done,payload=excluded.payload',
                   (event['id'],event['league'],event['date'],int(bool(event.get('done'))),
                    json.dumps(event,ensure_ascii=False,separators=(',',':'))))
    db.commit()
    db.close()
    if not any(e.get('done') or e.get('date', '') >= TODAY.isoformat() for e in events.values()):
        raise RuntimeError('Veri alınamadı; önceki snapshot korunuyor')
    # Preserve all previously collected final scores; never discard old history.
    lower = '1900-01-01'
    upper = (last + dt.timedelta(days=1)).isoformat()
    selected = [e for e in events.values() if lower <= e.get('date', '') < upper and
                (e.get('done') or (e['league'] in LEAGUES and e.get('date', '') >= TODAY.isoformat()))]
    now = dt.datetime.now(dt.timezone.utc)
    # Read immutable predictions from earlier successful runs. Never retroactively
    # create a prediction for a match that has already kicked off.
    archive_path = ROOT / 'prediction_history.json'
    try:
        archive = json.loads(archive_path.read_text('utf-8')).get('predictions', [])
        if not isinstance(archive, list): archive = []
    except (OSError, ValueError, TypeError):
        archive = []
    by_id = {x['matchId'] for x in archive if isinstance(x, dict) and x.get('matchId')}
    # Reuse previous probabilities when neither team's latest results have changed.
    previous_analyses = {}
    if path.exists():
        try:
            previous_analyses = json.loads(path.read_text('utf-8')).get('analyses', {})
        except (ValueError, OSError, TypeError):
            pass
    from analysis_engine import recent
    def fingerprint(match):
        kick = when(match['date'])
        parts = []
        for side in ('home', 'away'):
            parts.append([(r['id'], r['home']['score'], r['away']['score'])
                          for r in recent(selected, match[side], kick)])
        # Elo depends on the wider completed-match pool, not just last 10 games.
        # A changed result anywhere in the archive invalidates this cache.
        return json.dumps([parts, history_version], separators=(',', ':'))
    import hashlib
    final_rows = sorted((e['id'], e['date'], e['home']['score'], e['away']['score'])
                        for e in selected if e.get('done') and isinstance(e['home'].get('score'),int)
                        and isinstance(e['away'].get('score'),int))
    history_version = hashlib.sha256(json.dumps(final_rows, separators=(',', ':')).encode()).hexdigest()[:16]
    analyses = {}
    diagnostics = {}
    cache_hits = 0
    for match in selected:
        if match['league'] not in LEAGUES or match.get('done') or (when(match['date']) < now - dt.timedelta(hours=4) and not match.get('live')):
            continue
        fp = fingerprint(match)
        cached = previous_analyses.get(match['id'], {})
        if cached.get('historyFingerprint') == fp and cached.get('model') == 'poisson-v2-all-competitions':
            result = cached
            cache_hits += 1
        else:
            result = calculate(match, selected)
            result['historyFingerprint'] = fp
        # v25: Never publish a prediction unless both sides have ten verified
        # completed matches strictly before kickoff. Keep collecting history.
        if result.get('nh', 0) < 10 or result.get('na', 0) < 10:
            result = dict(result)
            result['ready'] = False
            result.pop('p', None)
        analyses[match['id']] = result
        diagnostics[match['id']] = {'home': match['home']['name'], 'away': match['away']['name'], 'homeMatches': result.get('nh',0), 'awayMatches': result.get('na',0), 'ready': result['ready']}
        if result['ready'] and not match.get('live') and match['id'] not in by_id:
            # Select one market per match before kickoff for auditable hit rate.
            market = max(result['p'], key=result['p'].get)
            archive.append({'matchId':match['id'],'league':match['league'],
                'kickoff':match['date'],'createdAt':now.isoformat(),
                'p':result['p'],'primaryMarket':market,'model':result['model'],
                'settled':False})
            by_id.add(match['id'])
    settle_archive(archive, selected)
    archive = sorted(archive,key=lambda x:x['kickoff'])[-2000:]
    archive_path.write_text(json.dumps({'predictions':archive},ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    payload = {'analyses':analyses,'validation':metrics(archive),'updatedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
               'events': sorted(selected, key=lambda e: e['date']), 'predictionArchive': [r for r in archive if r.get('league') in LEAGUES and r.get('p')], 'diagnostics': diagnostics,
               'errors': errors, 'historyDays': HISTORY_DAYS, 'fixtureDays': FUTURE_DAYS,
               'source': 'ESPN unofficial public scoreboards; historical results, Elo and Poisson model computed in GitHub Actions'}
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), 'utf-8')
    print(f'Reused {cache_hits} unchanged analyses', flush=True)
    print(f'Saved {len(selected)} events; {sum(e["done"] for e in selected)} completed')
    print(f'Analysis ready: {sum(x["ready"] for x in diagnostics.values())}/{len(diagnostics)}', flush=True)
    for x in diagnostics.values():
        if not x['ready']:
            print(f'INSUFFICIENT {x["home"]} ({x["homeMatches"]}) vs {x["away"]} ({x["awayMatches"]})', flush=True)

if __name__ == '__main__':
    main()
