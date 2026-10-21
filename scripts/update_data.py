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
    chosen = incomplete[:batch]
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
