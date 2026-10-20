"""Chronological, pre-match Elo ratings from stored final scores only.

No player/lineup features are fabricated. Ratings are relative to the available
historical match pool, not a global ranking of all football clubs.
"""
import math
from analysis_engine import team_key, when

BASE = 1500.0
HOME_ADVANTAGE = 55.0
K = 22.0

def ratings_before(events, kickoff):
    ratings = {}
    counts = {}
    ordered = sorted((e for e in events if e.get('done') and e.get('date') and when(e['date']) < kickoff
                      and isinstance(e.get('home', {}).get('score'), int)
                      and isinstance(e.get('away', {}).get('score'), int)), key=lambda e: (e['date'], e.get('id', '')))
    seen = set()
    for m in ordered:
        # Avoid counting duplicated games returned under multiple competitions.
        h = team_key(m['home'].get('name')); a = team_key(m['away'].get('name'))
        if not h or not a or h == a: continue
        duplicate = (m['date'][:10], tuple(sorted((h, a))), m['home']['score'] if h < a else m['away']['score'], m['away']['score'] if h < a else m['home']['score'])
        if duplicate in seen: continue
        seen.add(duplicate)
        rh = ratings.get(h, BASE); ra = ratings.get(a, BASE)
        expected = 1 / (1 + 10 ** ((ra - rh - HOME_ADVANTAGE) / 400))
        sh = m['home']['score']; sa = m['away']['score']
        actual = 1.0 if sh > sa else 0.5 if sh == sa else 0.0
        margin = min(1.6, 1 + 0.14 * max(0, abs(sh - sa) - 1))
        change = K * margin * (actual - expected)
        ratings[h] = rh + change; ratings[a] = ra - change
        counts[h] = counts.get(h, 0) + 1; counts[a] = counts.get(a, 0) + 1
    return ratings, counts

def matchup(events, match):
    ratings, counts = ratings_before(events, when(match['date']))
    h = team_key(match['home'].get('name')); a = team_key(match['away'].get('name'))
    return {'home': round(ratings.get(h, BASE), 1), 'away': round(ratings.get(a, BASE), 1),
            'homeMatches': counts.get(h, 0), 'awayMatches': counts.get(a, 0),
            'homeAdvantage': HOME_ADVANTAGE}
