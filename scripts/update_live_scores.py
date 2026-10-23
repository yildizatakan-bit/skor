#!/usr/bin/env python3
"""Publish-only live scoreboard snapshot. Does not read/write the match archive or predictions.

GitHub Actions saves this JSON in a dedicated `live-scores` branch, separate from Pages.
ESPN endpoints are unofficial: availability and five-minute delivery are not guaranteed.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

LEAGUES = [
    'tur.1', 'eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1',
    'uefa.champions', 'uefa.europa', 'uefa.europa.conf',
]
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'live_scores.json'
TZ = ZoneInfo('Europe/Istanbul')
BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/'


def clean_score(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        x = int(str(value))
        return x if x >= 0 and str(value).strip() == str(x) else None
    except (ValueError, TypeError):
        return None


def parse_event(e, league):
    comp = (e.get('competitions') or [{}])[0]
    participants = comp.get('competitors') or []
    home = next((x for x in participants if x.get('homeAway') == 'home'), None)
    away = next((x for x in participants if x.get('homeAway') == 'away'), None)
    if not home or not away or not e.get('date') or e.get('id') is None:
        return None
    s = e.get('status') or {}
    typ = s.get('type') or {}
    done = typ.get('completed') is True
    live = not done and str(typ.get('state') or '').lower() == 'in'

    def team(x):
        info = x.get('team') or {}
        logos = info.get('logos') or []
        return {
            'id': str(info.get('id') or ''),
            'name': str(info.get('displayName') or info.get('name') or '?'),
            'logo': str(info.get('logo') or (logos[0].get('href') if logos else '') or ''),
            'score': clean_score(x.get('score')) if (live or done) else None,
        }

    return {
        'id': league + ':' + str(e['id']),
        'league': league,
        'date': e['date'],
        'done': done,
        'live': live,
        'clock': str(s.get('displayClock') or s.get('clock') or ''),
        'home': team(home),
        'away': team(away),
    }


def fetch_json(league, from_day, to_day=None):
    dates = from_day.strftime('%Y%m%d')
    if to_day and to_day != from_day:
        dates += '-' + to_day.strftime('%Y%m%d')
    url = BASE + urllib.parse.quote(league, safe='') + '/scoreboard?' + urllib.parse.urlencode({
        'dates': dates, 'limit': 200,
    })
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; FootballLiveSnapshot/1.0)',
        'Accept': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=13) as res:
        if res.status != 200:
            raise RuntimeError('HTTP ' + str(res.status))
        payload = json.load(res)
    if not isinstance(payload.get('events'), list):
        raise ValueError('Missing events array')
    return payload['events']


def get_league_events(league, start, end):
    """Range query preferred; retry each day if ESPN rejects or returns empty."""
    try:
        events = fetch_json(league, start, end)
        if events:
            return events, None
    except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
        first_error = str(exc)
    else:
        first_error = 'Range returned zero events'

    all_events = []
    successful_days = 0
    errors = []
    day = start
    while day <= end:
        try:
            all_events.extend(fetch_json(league, day))
            successful_days += 1
        except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
            errors.append(f'{day.isoformat()}: {exc}')
        day += dt.timedelta(days=1)
    if not successful_days:
        raise RuntimeError(f'Range: {first_error}; days: {errors[:2]}')
    return all_events, '; '.join(errors[:2]) if errors else None


def build_snapshot():
    now = dt.datetime.now(dt.timezone.utc)
    local_day = now.astimezone(TZ).date()
    start, end = local_day - dt.timedelta(days=1), local_day + dt.timedelta(days=1)
    events = {}
    errors = []
    good = 0
    for league in LEAGUES:
        try:
            rows, warning = get_league_events(league, start, end)
            good += 1
            for raw in rows:
                event = parse_event(raw, league)
                if event:
                    events[event['id']] = event
            if warning:
                errors.append(f'{league}: {warning}')
            print(f'{league}: {len(rows)} scoreboard events', flush=True)
        except Exception as exc:
            errors.append(f'{league}: {type(exc).__name__}: {exc}')
            print(f'{league}: failed: {exc}', flush=True)
        time.sleep(0.08)

    # A network outage must NOT overwrite the last good scoreboard with an empty feed.
    if good == 0:
        raise RuntimeError('No league scoreboard available; keeping previous live-scores branch intact')
    result = {
        'version': 1,
        'updatedAt': now.isoformat().replace('+00:00', 'Z'),
        'source': 'ESPN soccer scoreboard (unofficial)',
        'successfulLeagues': good,
        'leagueCount': len(LEAGUES),
        'events': sorted(events.values(), key=lambda x: (x['date'], x['id'])),
        'errors': errors,
    }
    return result


def main():
    snapshot = build_snapshot()
    OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    live = [x for x in snapshot['events'] if x['live']]
    print(f"LIVE SCORES: leagues={snapshot['successfulLeagues']}/{snapshot['leagueCount']} "
          f"events={len(snapshot['events'])} live={len(live)} errors={len(snapshot['errors'])}")
    print('OUTPUT live_scores.json; analysis, history and database untouched')


if __name__ == '__main__':
    main()
