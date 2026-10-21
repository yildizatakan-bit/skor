"""Read-only, chronological baseline backtest. Does not modify match data.

Uses the deployed Poisson calculate() function without Elo/opponent corrections.
Those optional components are deliberately excluded from this baseline and
must be evaluated separately before claiming any model improvement.
"""
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from analysis_engine import calculate, outcome, team_key, when


def main():
    db = sqlite3.connect(f'file:{ROOT / "match_archive.sqlite3"}?mode=ro', uri=True)
    events = []
    for (payload,) in db.execute('SELECT payload FROM matches WHERE done=1'):
        try:
            m = json.loads(payload)
            if (m.get('done') and m.get('date') and team_key(m['home']['name'])
                    and team_key(m['away']['name']) and isinstance(m['home'].get('score'), int)
                    and isinstance(m['away'].get('score'), int)):
                events.append(m)
        except (ValueError, KeyError, TypeError):
            continue
    db.close()
    # Prevent same final being counted twice across competition feeds.
    seen = set()
    unique = []
    for m in sorted(events, key=lambda x: (x['date'], x['id'])):
        h, a = team_key(m['home']['name']), team_key(m['away']['name'])
        key = (m['date'][:10], h, a, m['home']['score'], m['away']['score'])
        if key not in seen:
            seen.add(key)
            unique.append(m)
    totals = defaultdict(lambda: {'n': 0, 'hits': 0, 'positives': 0, 'brier': 0.0, 'logloss': 0.0})
    eligible = 0
    skipped = 0
    # All prior fixtures are selected using a STRICT kickoff timestamp.
    # Historical data never includes the evaluated game or a later game.
    # Cheap chronological eligibility gate avoids expensive full-history scans.
    counts = defaultdict(int)
    for m in unique:
        home_key = team_key(m['home']['name'])
        away_key = team_key(m['away']['name'])
        if counts[home_key] < 10 or counts[away_key] < 10:
            skipped += 1
            counts[home_key] += 1
            counts[away_key] += 1
            continue
        result = calculate(m, unique)
        counts[home_key] += 1
        counts[away_key] += 1
        if not result['ready'] or result['nh'] != 10 or result['na'] != 10:
            skipped += 1
            continue
        eligible += 1
        hs, as_ = m['home']['score'], m['away']['score']
        for market, prob in result['p'].items():
            observed = int(outcome(market, hs, as_))
            row = totals[market]
            row['n'] += 1
            row['hits'] += int((prob >= 0.5) == bool(observed))
            row['positives'] += observed
            row['brier'] += (prob - observed) ** 2
            row['logloss'] += -(observed * math.log(max(prob, 1e-12)) + (1-observed) * math.log(max(1-prob, 1e-12)))
    report = {
        'model': 'poisson-v4 baseline WITHOUT optional Elo/opponent adjustments',
        'method': 'Retrospective chronological replay; only completed matches strictly before each kickoff; 10 prior games per team; no training/test split; not a prospective accuracy claim. thresholdAccuracy includes correct negatives and must not be interpreted as prediction win rate.',
        'archiveFinished': len(events), 'uniqueFinished': len(unique),
        'eligible': eligible, 'skippedInsufficientHistory': skipped,
        'markets': {k: {'n': v['n'], 'thresholdAccuracy': round(v['hits']/v['n'], 4),
                         'observedRate': round(v['positives']/v['n'], 4),
                         'brier': round(v['brier']/v['n'], 4),
                         'logLoss': round(v['logloss']/v['n'], 4)}
                    for k, v in sorted(totals.items()) if v['n']},
    }
    path = ROOT / 'backtest_report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'BACKTEST: {eligible}/{len(unique)} eligible completed games; {skipped} insufficient')
    for key in ('home', 'draw', 'away', 'over25', 'under25', 'btts', 'noBtts'):
        if key in report['markets']:
            print(f'  {key}: {report["markets"][key]}')
    print('Report: backtest_report.json (database unchanged)')

if __name__ == '__main__':
    main()
