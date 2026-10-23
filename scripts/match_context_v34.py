#!/usr/bin/env python3
"""v34: read-only, observable pre-match context. Never adjusts model probabilities.

Inputs are the existing match_archive.sqlite3 rows. Missing standings, squads,
UEFA qualification probabilities, and competition rules are NEVER invented.
"""
import argparse
import collections
import csv
import datetime as dt
import json
import pathlib
import sqlite3
import statistics
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / 'match_archive.sqlite3'
DOMESTIC = {'tur.1':'Türkiye Süper Lig', 'eng.1':'Premier League',
            'esp.1':'La Liga', 'ger.1':'Bundesliga', 'ita.1':'Serie A',
            'fra.1':'Ligue 1', 'ned.1':'Eredivisie', 'eng.2':'Championship'}
UEFA = {'uefa.champions','uefa.europa','uefa.europa.conf'}
CUP = {'tur.cup','eng.fa','eng.league_cup','esp.copa_del_rey',
       'ger.dfb_pokal','ita.coppa_italia','fra.coupe_de_france','ned.knvb_beker'}
MAIN = set(DOMESTIC) | UEFA

FACTORS = [
    ('title_race','Şampiyonluk yarışı'),
    ('europe_spots','Avrupa kupalarına katılma'),
    ('relegation','Küme düşme hattı'),
    ('uefa_advancement','UEFA tur ihtimali'),
    ('uefa_guaranteed','UEFA hedefinin garantilenmesi'),
    ('uefa_elimination','UEFA elenme durumu'),
    ('congestion','Fikstür yoğunluğu'),
    ('upcoming_key_match','Yaklaşan UEFA/kupa maçı'),
    ('injuries','Sakatlık ve cezalar'),
    ('rotation','Kadro rotasyonu'),
    ('new_coach','Teknik direktör değişikliği'),
    ('draw_suffices','Beraberliğin yetmesi'),
    ('opponent_stakes','Rakibin lig hedefi'),
    ('season_phase','Sezon ve oynanan maç sayısı'),
]


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace('Z','+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def slug(value):
    s = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ''.join(c for c in s if c.isalnum() and not unicodedata.combining(c))


def season_start(at):
    # Domestic fall-spring leagues currently listed in DOMESTIC.
    year = at.year if at.month >= 7 else at.year - 1
    return dt.datetime(year, 7, 1, tzinfo=dt.timezone.utc)


def load_events(db_path):
    if not db_path.is_file():
        raise FileNotFoundError(f'Veritabanı bulunamadı: {db_path}')
    db = sqlite3.connect(f'{db_path.resolve().as_uri()}?mode=ro', uri=True)
    try:
        rows = db.execute('SELECT payload FROM matches ORDER BY date,id').fetchall()
    finally:
        db.close()
    parsed, malformed = [], 0
    for (raw,) in rows:
        try:
            match = json.loads(raw)
            stamp = parse_time(match.get('date'))
            if not stamp or not isinstance(match.get('home'),dict) or not isinstance(match.get('away'),dict):
                malformed += 1
                continue
            match['_at'] = stamp
            match['_hk'] = slug(match['home'].get('name'))
            match['_ak'] = slug(match['away'].get('name'))
            if not match['_hk'] or not match['_ak'] or match['_hk'] == match['_ak']:
                malformed += 1
                continue
            parsed.append(match)
        except (ValueError, TypeError, AttributeError):
            malformed += 1
    return parsed, malformed


def finished(e, asof):
    # Conservative temporal safety: never use a score from a match kicked off
    # less than three hours before the report cutoff (no final-status timestamp).
    return (e.get('done') is True and e['_at'] + dt.timedelta(hours=3) <= asof and
            isinstance(e['home'].get('score'), int) and not isinstance(e['home'].get('score'), bool) and
            isinstance(e['away'].get('score'), int) and not isinstance(e['away'].get('score'), bool))


def team_matches(events, key, cutoff, completed=False):
    items = [m for m in events if m['_at'] < cutoff and key in (m['_hk'],m['_ak'])
             and (not completed or finished(m,cutoff))]
    return sorted(items, key=lambda x:x['_at'])


def standings(events, league, asof):
    start = season_start(asof)
    season_events = [m for m in events if m.get('league') == league and
                     start <= m['_at'] < asof and finished(m,asof)]
    stats = collections.defaultdict(lambda: dict(games=0,wins=0,draws=0,losses=0,goalsFor=0,goalsAgainst=0,points=0))
    team_name = {}
    for m in season_events:
        h,a = m['_hk'], m['_ak']
        sh,sa = m['home']['score'],m['away']['score']
        for key, scored, conceded in ((h,sh,sa),(a,sa,sh)):
            x=stats[key];x['games']+=1;x['goalsFor']+=scored;x['goalsAgainst']+=conceded
            x['points'] += 3 if scored>conceded else 1 if scored==conceded else 0
            x['wins']+=int(scored>conceded);x['draws']+=int(scored==conceded);x['losses']+=int(scored<conceded)
        team_name[h]=m['home']['name'];team_name[a]=m['away']['name']
    table = sorted(stats, key=lambda k:(-stats[k]['points'],-(stats[k]['goalsFor']-stats[k]['goalsAgainst']),-stats[k]['goalsFor'],team_name[k]))
    # ESPN historical backfill can omit matches and entire clubs; no authoritative rank.
    played = [v['games'] for v in stats.values()]
    coverage = {'observedCompletedGames':len(season_events),'observedClubs':len(played),
                'minMatchesPerClub':min(played) if played else 0,
                'maxMatchesPerClub':max(played) if played else 0,
                'medianMatchesPerClub':statistics.median(played) if played else 0,
                'status':'partial_unverified',
                'explanation':'Arşivde görülen sonuçlardan türetilmiştir; resmi puan tablosu veya tam fikstür değildir.'}
    ranks = {}
    for i,key in enumerate(table):
        st=stats[key]
        ranks[key]={'name':team_name[key],'provisionalRank':i+1,'observedClubs':len(table),
                    'games':st['games'],'points':st['points'],
                    'goalDifference':st['goalsFor']-st['goalsAgainst'],
                    'pointsBehindObservedLeader':stats[table[0]]['points']-st['points'],
                    'pointsAheadObservedLast':st['points']-stats[table[-1]]['points'],
                    'status':'partial_unverified'}
    return ranks, coverage


def observe(team, match, events, asof, table_cache):
    key=slug(team.get('name'))
    kick=match['_at']
    # Calculate strictly from information available at report creation time.
    past=team_matches(events,key,asof,completed=True)
    last=past[-1] if past else None
    rest=(kick-last['_at']).total_seconds()/86400 if last else None
    past14=[p for p in past if (asof-p['_at']).total_seconds()<=14*86400]
    # Only scheduled / uncompleted known matches; all within target kickoff.
    ahead=sorted((e for e in events if not e.get('done') and
                  asof <= e['_at'] <= kick+dt.timedelta(days=7) and
                  key in (e['_hk'], e['_ak']) and e.get('id')!=match.get('id')),
                 key=lambda x:x['_at'])
    before=[e for e in ahead if e['_at'] < kick]
    next7=[e for e in ahead if kick < e['_at'] <= kick+dt.timedelta(days=7)]
    nearby= [e for e in next7 if e.get('league') in UEFA | CUP]
    prev4=[e for e in before if (kick-e['_at']).total_seconds()<=4*86400]
    l=match.get('league')
    rank=None; table_info=None
    if l in DOMESTIC:
        if l not in table_cache:
            table_cache[l]=standings(events,l,asof)
        rank=table_cache[l][0].get(key)
        table_info=table_cache[l][1]
    factors={k:{'label':v,'status':'not_verifiable','evidence':None} for k,v in FACTORS}
    def set_factor(k,status,evidence):
        factors[k].update(status=status,evidence=evidence)
    set_factor('congestion','observed' if last else 'insufficient_data',
               {'completedMatchesPast14Days':len(past14),
                'daysFromLastCompletedMatchToKickoff':round(rest,2) if rest is not None else None,
                'scheduledMatchesWithin4DaysBeforeKickoff':len(prev4),
                'note':'Son tamamlanan maçtan bu maça süre; aradaki planlı maç ayrıca gösterilir.'})
    set_factor('upcoming_key_match','partial' if (ahead or last) else 'insufficient_data',
               {'knownUEFAOrCupGamesNext7Days':[
                   {'league':e['league'],'date':e['date'],'opponent':e['away']['name'] if e['_hk']==key else e['home']['name']}
                   for e in nearby[:5]],
                'scheduledMatchesBeforeThisKickoff':len(before),
                'note':'Arşivde bulunmaması maç olmadığı anlamına gelmez.'})
    set_factor('season_phase','partial' if rank else 'not_verifiable',
               {'playedDomesticMatchesInObservedArchive':rank['games'] if rank else None,
                'seasonStartUTC':season_start(asof).date().isoformat() if l in DOMESTIC else None})
    for f in ('title_race','europe_spots','relegation'):
        set_factor(f,'partial' if rank else 'not_verifiable',
                   {'provisionalStanding':rank,'note':'Hedef/garanti/tehlike kararı için resmi tam puan tablosu ve sezona özgü kurallar gerekli.'}
                   if rank else None)
    # UEFA fixtures are observable; standings/qualification and match's strategic value are not.
    uefa_involved=l in UEFA or bool(nearby)
    for f in ('uefa_advancement','uefa_guaranteed','uefa_elimination'):
        set_factor(f,'not_verifiable', {'uefaFixturePresent':uefa_involved,
                   'note':'UEFA puan durumu, tüm kalan maçlar, averaj/tie-break ve tur kuralları olmadan çıkarılamaz.'})
    set_factor('rotation','not_verifiable',{'schedulePressureObserved':len(past14)>=3 or len(nearby)>0,
        'note':'Bu bilgi rotasyonun gerçekleştiği anlamına gelmez; kesin/olası 11 verisi yok.'})
    set_factor('opponent_stakes','partial' if rank else 'not_verifiable',
               {'note':'Rakibin hedefi maç kaydındaki diğer tarafın ayrı raporunda yer alır; durum resmi sıralama değil.'})
    # New coach, injury, draw enough require external time-stamped sources.
    return {'team':team.get('name'),'teamKey':key,'restDays':round(rest,2) if rest is not None else None,
            'completedMatchesPast14Days':len(past14),
            'provisionalStanding':rank,'tableCoverage':table_info,
            'factorEvidence':factors,
            'caveats':['Takım eşleştirmeleri tam ad normalizasyonuna dayanır; farklı adlar eşleşmeyebilir.',
                       'Kadro, sakatlık, UEFA garantisi veya motivasyon katsayısı tahmin edilmez.']}


def build(events, asof, horizon_days=7, cap=250):
    cutoff=asof+dt.timedelta(days=horizon_days)
    fixtures=sorted((e for e in events if e.get('league') in MAIN and not e.get('done') and
                     asof <= e['_at'] < cutoff),key=lambda e:(e['_at'],e.get('id','')))[:cap]
    cache={}
    result=[]
    for m in fixtures:
        result.append({'matchId':m.get('id'),'league':m.get('league'),
            'kickoffUTC':m['_at'].isoformat(),
            'home':observe(m['home'],m,events,asof,cache),
            'away':observe(m['away'],m,events,asof,cache)})
    return {'version':'v34-observation-only','generatedAtUTC':dt.datetime.now(dt.timezone.utc).isoformat(),
            'asOfUTC':asof.isoformat(),'modelPredictionsModified':False,
            'source':'Local, unverified ESPN-derived match_archive.sqlite3 snapshot',
            'scope':{'lookaheadDays':horizon_days,'matchesAnalyzed':len(result),
                     'completedEventsAvailableAtAsOf':sum(finished(e,asof) for e in events)},
            'dataIntegrity':{'noPostAsOfResultsUsed':True,'standingsOfficial':False,
                 'notes':['Rapor, maç sonucunu veya motivasyon yüzdesini tahmin etmez.',
                          'Puan durumları eksik arşiv nedeniyle yalnızca gözlemseldir.',
                          'Gelecekteki maçları oynanmış kabul etmez.']},
            'matches':result}


def export(report, json_path, csv_path):
    pathlib.Path(json_path).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    headers=['matchId','kickoffUTC','league','side','team','provisionalRank','observedClubs',
             'observedDomesticGames','provisionalPoints','pointsBehindObservedLeader',
             'restDaysFromLastCompleted','completedMatchesPast14Days',
             'scheduledGamesBeforeKickoff','uefaCupGamesNext7Days','verifiedMotivationScore']
    with open(csv_path,'w',newline='',encoding='utf-8-sig') as fd:
        out=csv.DictWriter(fd,fieldnames=headers);out.writeheader()
        for game in report['matches']:
            for side in ('home','away'):
                entry=game[side]; r=entry['provisionalStanding'] or {}
                fut=entry['factorEvidence']['upcoming_key_match']['evidence'] or {}
                out.writerow({'matchId':game['matchId'],'kickoffUTC':game['kickoffUTC'],
                    'league':game['league'],'side':side,'team':entry['team'],
                    'provisionalRank':r.get('provisionalRank'),
                    'observedClubs':r.get('observedClubs'),
                    'observedDomesticGames':r.get('games'),
                    'provisionalPoints':r.get('points'),
                    'pointsBehindObservedLeader':r.get('pointsBehindObservedLeader'),
                    'restDaysFromLastCompleted':entry['restDays'],
                    'completedMatchesPast14Days':entry['completedMatchesPast14Days'],
                    'scheduledGamesBeforeKickoff':fut.get('scheduledMatchesBeforeThisKickoff'),
                    'uefaCupGamesNext7Days':len(fut.get('knownUEFAOrCupGamesNext7Days',[])),
                    'verifiedMotivationScore':''})


def main():
    parser=argparse.ArgumentParser(description='v34: read-only pre-match context')
    parser.add_argument('--db',type=pathlib.Path,default=DB_DEFAULT)
    parser.add_argument('--as-of',help='UTC ISO 8601; useful for reproducible audits')
    parser.add_argument('--days',type=int,default=7)
    parser.add_argument('--json-out',default='match_context_v34.json')
    parser.add_argument('--csv-out',default='match_context_v34.csv')
    a=parser.parse_args()
    if not 1<=a.days<=14: parser.error('--days must be between 1 and 14')
    asof=parse_time(a.as_of) if a.as_of else dt.datetime.now(dt.timezone.utc)
    if asof is None: parser.error('invalid --as-of date')
    events, bad=load_events(a.db)
    report=build(events,asof,a.days)
    report['scope']['archiveEventsLoaded']=len(events)
    report['scope']['malformedRowsIgnored']=bad
    export(report,a.json_out,a.csv_out)
    print(f"V34 CONTEXT: loaded={len(events)} malformed={bad} "+
          f"upcoming={len(report['matches'])} completed={report['scope']['completedEventsAvailableAtAsOf']}")
    print('MODEL UNCHANGED: no predictions, database, data.json or history file modified')
    print('Reports:',a.json_out,a.csv_out)
    if not report['matches']:
        print('NOTE: no upcoming supported fixtures in this DB and date window; check updater/archived fixture coverage')
    for m in report['matches'][:8]:
        home,away=m['home'],m['away']
        print(f"{m['kickoffUTC'][:10]} {home['team']} - {away['team']} "+
              f"| rest={home['restDays']}/{away['restDays']} "+
              f"| 14d={home['completedMatchesPast14Days']}/{away['completedMatchesPast14Days']}")


if __name__=='__main__':
    main()
