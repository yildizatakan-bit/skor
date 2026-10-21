#!/usr/bin/env python3
"""Collect 7-day fixtures and past results; derive statistics locally in the PWA."""
import datetime as dt
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from analysis_engine import calculate, settle_archive, metrics, when
from elo_engine import matchup, opponent_form_adjustment

LEAGUES = ['tur.1','eng.1','esp.1','ger.1','ita.1','fra.1','uefa.champions','uefa.europa','uefa.europa.conf']
# Extra competitions are used ONLY for historical team form, not displayed as fixtures.
# ESPN codes are unofficial; unsupported competitions are skipped gracefully.
CUP_LEAGUES = ['tur.cup','eng.fa','eng.league_cup','esp.copa_del_rey','ger.dfb_pokal','ita.coppa_italia','fra.coupe_de_france']
ROOT = pathlib.Path(__file__).resolve().parents[1]
TODAY = dt.datetime.now(dt.timezone.utc).date()
HISTORY_DAYS = 180
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
    events = dict(previous)
    errors = []
    # Use short windows to avoid the HTTP 400 produced by a 165-day request.
    # Fall back to single days only for windows that are rejected.
    first = TODAY - dt.timedelta(days=HISTORY_DAYS)
    last = TODAY + dt.timedelta(days=FUTURE_DAYS)
    full_refresh = os.environ.get('FULL_REFRESH', '0') == '1'
    # Short refresh: only recently completed matches and upcoming fixtures.
    # Historical results are retained from the prior snapshot; twice daily
    # full scans restore coverage for missed updates and cup matches.
    scan_first = first if full_refresh else TODAY - dt.timedelta(days=2)
    scan_leagues = LEAGUES + CUP_LEAGUES
    print('Refresh mode:', 'full' if full_refresh else 'incremental', flush=True)
    for league in scan_leagues:
        successful = 0
        failures = 0
        day = scan_first
        while day <= last:
            end = min(day + dt.timedelta(days=2), last)
            try:
                found = request(league, day, end)
                for e in found:
                    events[e['id']] = e
                successful += 1
            except Exception as exc:
                # ESPN's unofficial endpoint may reject date ranges.
                for offset in range((end-day).days + 1):
                    d = day + dt.timedelta(days=offset)
                    try:
                        for e in request(league, d):
                            events[e['id']] = e
                        successful += 1
                    except Exception as single_exc:
                        failures += 1
                        if failures <= 2:
                            errors.append(f'{league} {d}: {type(single_exc).__name__}: {single_exc}')
            day = end + dt.timedelta(days=1)
            time.sleep(0.05)
        print(f'{league}: {successful} successful windows/days, {failures} failed days', flush=True)
        if failures and league in LEAGUES:
            errors.append(f'{league}: {failures} gün sorgulanamadı')
        elif failures and league in CUP_LEAGUES:
            print(f'Optional cup {league}: {failures} dates unavailable (non-fatal)', flush=True)
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
        if cached.get('historyFingerprint') == fp and cached.get('model') == 'poisson-v4-opponent-adjusted':
            result = cached
            cache_hits += 1
        else:
            result = calculate(match, selected, matchup(selected, match), opponent_form_adjustment(selected, match))
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
