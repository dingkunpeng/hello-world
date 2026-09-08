#!/usr/bin/env python3
import csv, io, zipfile, requests
from pathlib import Path
from datetime import datetime, timezone

REPO_OUT = Path('output/annual_ev')
REPO_OUT.mkdir(parents=True, exist_ok=True)
EVENT_CSV = Path('output/btc_2026_30m_candidate_events.csv')
SPOT_TMPL='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{date}.zip'
INDEX_TMPL='https://data.binance.vision/data/futures/um/daily/indexPriceKlines/BTCUSDT/1m/BTCUSDT-1m-{date}.zip'
LADDER_BPS=[-40,-60,-80,-100,-120]
LAYER_USD=2000


def get_zip_rows(url):
    r=requests.get(url,timeout=60); r.raise_for_status()
    z=zipfile.ZipFile(io.BytesIO(r.content))
    name=next(n for n in z.namelist() if n.endswith('.csv'))
    raw=z.read(name).decode('utf-8-sig').strip().splitlines()
    rows=list(csv.reader(raw))
    if rows and not rows[0][0].replace('.','',1).isdigit(): rows=rows[1:]
    return rows

def norm_ts(x):
    v=int(float(x))
    return v//1000 if v>10**14 else v

def load_day(date):
    srows=get_zip_rows(SPOT_TMPL.format(date=date))
    irows=get_zip_rows(INDEX_TMPL.format(date=date))
    spot={norm_ts(r[0]):r for r in srows}
    idx={norm_ts(r[0]):r for r in irows}
    return spot,idx

def iso(ms): return datetime.fromtimestamp(ms/1000,tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

events=[]
with EVENT_CSV.open(encoding='utf-8-sig') as f:
    for r in csv.DictReader(f): events.append(r)

cache={}
out=[]
for ev in events:
    start=datetime.strptime(ev['start_utc'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    start_ms=int(start.timestamp()*1000); end_ms=start_ms+30*60*1000
    date=start.strftime('%Y-%m-%d')
    if date not in cache:
        print('download',date)
        cache[date]=load_day(date)
    spot,idx=cache[date]
    mins=[]
    t=start_ms
    while t<end_ms:
        if t in spot and t in idx:
            sl=float(spot[t][3]); il=float(idx[t][3]); bp=(sl/il-1)*10000
            mins.append((bp,t,sl,il,float(spot[t][1]),float(spot[t][4]),float(idx[t][1]),float(idx[t][4])))
        t+=60_000
    if not mins: continue
    worst=min(mins,key=lambda x:x[0])
    bp,t,sl,il,so,sc,io,ic=worst
    layers=sum(1 for x in LADDER_BPS if bp<=x)
    out.append({
        'event_rank_30m':ev['event_rank'],'start_30m_utc':ev['start_utc'],
        'worst_1m_utc':iso(t),'spot_low_1m':sl,'index_low_1m':il,
        'deviation_1m_bp':bp,'deviation_30m_bp':ev['min_deviation_bp'],
        'potential_layers_proxy':layers,'potential_notional_proxy_usd':layers*LAYER_USD,
        'ladder_bps':'/'.join(map(str,LADDER_BPS)),
        'status_1m':'L2_PRIORITY' if bp<=-40 else ('TICK_REVIEW' if bp<=-30 else 'LOW_PRIORITY')
    })

out.sort(key=lambda r:r['deviation_1m_bp'])
for i,r in enumerate(out,1): r['rank_1m']=i
fields=['rank_1m','event_rank_30m','start_30m_utc','worst_1m_utc','spot_low_1m','index_low_1m','deviation_1m_bp','deviation_30m_bp','potential_layers_proxy','potential_notional_proxy_usd','ladder_bps','status_1m']
with (REPO_OUT/'annual_1m_triage.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)

summary={
    'events_total':len(out),
    'l2_priority_le_-40bp':sum(r['deviation_1m_bp']<=-40 for r in out),
    'tick_review_le_-30bp':sum(r['deviation_1m_bp']<=-30 for r in out),
    'ladder_rule_proxy':'Fair reference proxy = Binance Index; resting bid offsets -40/-60/-80/-100/-120 bp; $2k each. This is a screening proxy, not executable P&L.',
}
with (REPO_OUT/'annual_1m_triage_summary.txt').open('w',encoding='utf-8') as f:
    for k,v in summary.items(): f.write(f'{k}: {v}\n')
    f.write('\nTOP EVENTS\n')
    for r in out[:15]: f.write(str(r)+'\n')
print(summary)
for r in out[:15]: print(r)
