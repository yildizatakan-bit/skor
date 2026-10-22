"""Evaluate frozen pre-kickoff probabilities against completed results, without changing data."""
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANDS = [(0.5, 0.6, '50-59'), (0.6, 0.7, '60-69'), (0.7, 0.8, '70-79'), (0.8, 1.0000001, '80-100')]


def parse_date(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def outcome(k, h, a):
    t = h + a
    conditions = {
        'home': h > a, 'draw': h == a, 'away': h < a,
        'homeOrDraw': h >= a, 'awayOrDraw': h <= a, 'noDraw': h != a,
        'over05': t >= 1, 'under05': t == 0,
        'over15': t >= 2, 'under15': t <= 1,
        'over25': t >= 3, 'under25': t <= 2,
        'over35': t >= 4, 'under35': t <= 3,
        'over45': t >= 5, 'under45': t <= 4,
        'over55': t >= 6, 'under55': t <= 5,
        'btts': h > 0 and a > 0, 'noBtts': h == 0 or a == 0,
        'homeOver05': h >= 1, 'homeUnder05': h == 0,
        'homeOver15': h >= 2, 'homeUnder15': h <= 1,
        'homeOver25': h >= 3, 'homeUnder25': h <= 2,
        'awayOver05': a >= 1, 'awayUnder05': a == 0,
        'awayOver15': a >= 2, 'awayUnder15': a <= 1,
        'awayOver25': a >= 3, 'awayUnder25': a <= 2,
        'even': t % 2 == 0, 'odd': t % 2 == 1,
        'goals01': t <= 1, 'goals23': 2 <= t <= 3,
        'goals45': 4 <= t <= 5, 'goals6plus': t >= 6,
        'marginHome1': h-a == 1, 'marginHome2': h-a == 2,
        'marginHome3plus': h-a >= 3,
        'marginAway1': a-h == 1, 'marginAway2': a-h == 2,
        'marginAway3plus': a-h >= 3,
        'homeOver15Win': h > a and t >= 2,
        'homeOver25Win': h > a and t >= 3,
        'awayOver15Win': a > h and t >= 2,
        'awayOver25Win': a > h and t >= 3,
        'homeWinBtts': h > a and a > 0,
        'awayWinBtts': a > h and h > 0,
        'over25Btts': t >= 3 and h > 0 and a > 0,
        'under25Btts': t <= 2 and h > 0 and a > 0,
    }
    return conditions.get(k)


def aggregate(items):
    n = len(items)
    if not n:
        return {'n': 0, 'hits': 0, 'hitRate': None, 'meanProbability': None, 'brier': None, 'calibrationGap': None}
    hits = sum(y for p, y in items)
    mean_p = sum(p for p, y in items) / n
    return {'n': n, 'hits': hits, 'hitRate': round(hits/n, 4),
            'meanProbability': round(mean_p, 4),
            'brier': round(sum((p-y)**2 for p,y in items)/n, 5),
            'calibrationGap': round(hits/n - mean_p, 4)}


def main():
    archive_path = ROOT / 'prediction_history.json'
    db_path = ROOT / 'match_archive.sqlite3'
    if not archive_path.exists() or not db_path.exists():
        raise SystemExit('Missing prediction_history.json or match_archive.sqlite3; run archive update first.')
    predictions = json.loads(archive_path.read_text(encoding='utf-8')).get('predictions', [])
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as conn:
        rows = conn.execute('SELECT payload FROM matches WHERE done=1').fetchall()
    results = {}
    for (raw,) in rows:
        e = json.loads(raw)
        h, a = e.get('home', {}), e.get('away', {})
        if isinstance(h.get('score'), int) and isinstance(a.get('score'), int):
            results[str(e.get('id'))] = (h['score'], a['score'])

    seen = set()
    by_market = defaultdict(list)
    primary = []
    settled = pending = rejected = unsupported = 0
    for row in predictions:
        if not isinstance(row, dict):
            rejected += 1; continue
        mid = str(row.get('matchId', ''))
        if not mid or mid in seen:
            rejected += 1; continue
        seen.add(mid)
        created, kickoff = parse_date(row.get('createdAt')), parse_date(row.get('kickoff'))
        if not created or not kickoff or created >= kickoff:
            rejected += 1; continue
        score = results.get(mid)
        if score is None:
            pending += 1; continue
        settled += 1
        h, a = score
        pmap = row.get('p', {})
        if not isinstance(pmap, dict):
            rejected += 1; continue
        for market, prob in pmap.items():
            if not isinstance(prob, (int, float)) or not 0 <= prob <= 1:
                continue
            yes = outcome(market, h, a)
            if yes is None:
                unsupported += 1; continue
            if prob >= .5:
                by_market[(market, 'ALL_50_PLUS')].append((prob, int(yes)))
                for lo, hi, name in BANDS:
                    if lo <= prob < hi:
                        by_market[(market, name)].append((prob, int(yes)))
                        break
        primary_market = row.get('primaryMarket')
        if primary_market in pmap and isinstance(pmap[primary_market], (int, float)):
            y = outcome(primary_market, h, a)
            if y is not None:
                primary.append((pmap[primary_market], int(y)))

    records = []
    for (market, band), items in sorted(by_market.items()):
        records.append({'market': market, 'band': band, **aggregate(items)})
    report = {
        'description': 'Prospective evaluation of frozen pre-kickoff predictions; no model recalculation.',
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'archiveRecords': len(predictions), 'settledMatches': settled,
        'pendingMatches': pending, 'rejectedRecords': rejected,
        'unsupportedMarketValues': unsupported,
        'primary': aggregate(primary), 'markets': records,
        'warnings': [
            'Multiple markets from one match are correlated; market row counts are not independent matches.',
            'Accuracy and calibration do not establish betting profitability.',
            'Only markets with probability >= 50% are reported; small samples are unreliable.',
            'Results require matching match IDs in persistent database; no guessed results.'
        ]
    }
    (ROOT/'live_tracking_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (ROOT/'live_tracking_report.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['market', 'band', 'n', 'hits', 'hitRate', 'meanProbability', 'brier', 'calibrationGap'])
        writer.writeheader(); writer.writerows(records)
    print(f'LIVE TRACKING: archived={len(predictions)} settled={settled} pending={pending} rejected={rejected}')
    print('PRIMARY:', report['primary'])
    for item in records:
        if item['band'] == 'ALL_50_PLUS' and item['n'] >= 10:
            print(f"{item['market']}: n={item['n']} hit={item['hitRate']} meanP={item['meanProbability']} Brier={item['brier']}")
    print('Reports: live_tracking_report.json, live_tracking_report.csv (read-only)')

if __name__ == '__main__':
    main()
