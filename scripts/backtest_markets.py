"""Read-only chronological market calibration for the deployed v30 calculation path."""
import csv, json, sqlite3, sys
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from analysis_engine import calculate, outcome, recent, when

BANDS=[(0.5,0.6),(0.6,0.7),(0.7,0.8),(0.8,1.000001)]

def main():
    db=sqlite3.connect(f'file:{ROOT / "match_archive.sqlite3"}?mode=ro',uri=True)
    rows=db.execute('SELECT payload FROM matches WHERE done=1 ORDER BY date, id').fetchall()
    db.close()
    events=[json.loads(row[0]) for row in rows]
    # Exclude repeated provider records for the same event id.
    events=list({e['id']:e for e in events}.values())
    events.sort(key=lambda e:(e['date'],str(e['id'])))
    stats=defaultdict(lambda: {'n':0,'hits':0,'brier':0.0,'sumP':0.0,'sumY':0.0})
    eligible=0
    for idx,m in enumerate(events):
        if not isinstance(m.get('home',{}).get('score'),int) or not isinstance(m.get('away',{}).get('score'),int):continue
        # No current/future data in inputs: calculation selects only earlier kickoffs.
        if min(len(recent(events,m[s],when(m['date']))) for s in ('home','away'))<10:continue
        pred=calculate(m,events)
        if not pred.get('ready'):continue
        eligible+=1
        h=m['home']['score']; a=m['away']['score']
        for market,p in pred['p'].items():
            y=int(outcome(market,h,a))
            # Include all predictions in calibration; >=50% buckets are UI-oriented.
            for band,lo,hi in [('ALL',0,1.000001)]+[(f'{int(lo*100)}-{int(min(hi,1)*100)}%',lo,hi) for lo,hi in BANDS]:
                if not (lo<=p<hi):continue
                s=stats[(market,band)]
                s['n']+=1;s['hits']+=y;s['brier']+=(p-y)**2;s['sumP']+=p;s['sumY']+=y
    output=[]
    for (market,band),s in sorted(stats.items()):
        n=s['n'];output.append({'market':market,'band':band,'n':n,'observed_rate':round(s['sumY']/n,4),'mean_probability':round(s['sumP']/n,4),'brier':round(s['brier']/n,5),'calibration_gap':round(s['sumY']/n-s['sumP']/n,4)})
    report={'note':'Read-only retrospective evaluation. Same model calculation as v30 update_data.py (calculate without Elo arguments); historical predictions are reconstructed, not previously locked forecasts. Individual markets from the same game are correlated. Do not treat this as betting profitability.', 'total_completed':len(events),'eligible':eligible,'markets':output}
    (ROOT/'market_backtest_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with (ROOT/'market_backtest_report.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['market','band','n','observed_rate','mean_probability','brier','calibration_gap']);writer.writeheader();writer.writerows(output)
    print(f'MARKET BACKTEST: {eligible}/{len(events)} eligible completed matches')
    for r in output:
        if r['band']=='ALL' and r['market'] in ('home','draw','away','over25','btts','homeOver15','goals23','over25Btts','marginHome1'):
            print(f"{r['market']}: n={r['n']} Brier={r['brier']} meanP={r['mean_probability']} observed={r['observed_rate']}")
    print('Reports: market_backtest_report.json, market_backtest_report.csv (database unchanged)')
if __name__=='__main__':main()
