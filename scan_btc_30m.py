#!/usr/bin/env python3
import csv, json, time, io, zipfile
from datetime import datetime, timezone
from pathlib import Path
import requests

SYMBOL='BTCUSDT'
INTERVAL='30m'
START=1767225600000  # 2026-01-01 00:00:00 UTC
END_EXCLUSIVE=1788652800000  # 2026-09-06 00:00:00 UTC
STEP=30*60*1000
OUT=Path('output')
OUT.mkdir(exist_ok=True)

SPOT_URL='https://data-api.binance.vision/api/v3/klines'
ARCHIVE='https://data.binance.vision/data/futures/um'


def get(url, params=None, tries=6):
    last=None
    for i in range(tries):
        try:
            r=requests.get(url, params=params, timeout=45)
            r.raise_for_status()
            return r
        except Exception as e:
            last=e
            time.sleep(min(2**i, 20))
    raise last


def get_json(url, params):
    return get(url, params=params).json()


def fetch_spot():
    rows=[]
    cur=START
    chunk_n=1000
    while cur<END_EXCLUSIVE:
        chunk_end=min(END_EXCLUSIVE-1, cur+chunk_n*STEP-1)
        data=get_json(SPOT_URL, {'symbol':SYMBOL,'interval':INTERVAL,'startTime':cur,'endTime':chunk_end,'limit':chunk_n})
        if not data:
            raise RuntimeError(f'empty spot batch at {cur}')
        rows.extend(data)
        nxt=int(data[-1][0])+STEP
        if nxt<=cur: raise RuntimeError('spot pagination stalled')
        cur=nxt
        print('spot', len(rows), datetime.fromtimestamp(data[-1][0]/1000,tz=timezone.utc).isoformat())
    return rows


def read_archive_csv(url):
    blob=get(url).content
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names=[n for n in zf.namelist() if n.lower().endswith('.csv')]
        if not names: raise RuntimeError(f'no csv in {url}')
        text=zf.read(names[0]).decode('utf-8-sig').splitlines()
    rows=list(csv.reader(text))
    if rows and rows[0] and not rows[0][0].replace('.','',1).isdigit():
        rows=rows[1:]
    return rows


def fetch_index():
    rows=[]
    # Full months Jan-Aug 2026
    for month in range(1,9):
        ym=f'2026-{month:02d}'
        url=f'{ARCHIVE}/monthly/indexPriceKlines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{ym}.zip'
        part=read_archive_csv(url)
        rows.extend(part)
        print('index month', ym, len(part), 'total', len(rows))
    # Partial September: Sep 1-5 daily archives
    for day in range(1,6):
        ymd=f'2026-09-{day:02d}'
        url=f'{ARCHIVE}/daily/indexPriceKlines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{ymd}.zip'
        part=read_archive_csv(url)
        rows.extend(part)
        print('index day', ymd, len(part), 'total', len(rows))
    return rows


def iso(ms):
    return datetime.fromtimestamp(ms/1000,tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def tier(bp):
    if bp<=-50: return 'P0_<=-50bp'
    if bp<=-30: return 'P1_<=-30bp'
    if bp<=-20: return 'P2_-20_to_-30bp'
    return 'REJECT'

spot=fetch_spot()
index=fetch_index()
spot_map={int(r[0]):r for r in spot if START<=int(r[0])<END_EXCLUSIVE}
idx_map={int(float(r[0])):r for r in index if START<=int(float(r[0]))<END_EXCLUSIVE}
keys=sorted(set(spot_map)&set(idx_map))
expected=(END_EXCLUSIVE-START)//STEP
print('expected',expected,'spot',len(spot_map),'index',len(idx_map),'sync',len(keys))
if len(keys)!=expected:
    missing=[]
    t=START
    while t<END_EXCLUSIVE:
        if t not in spot_map or t not in idx_map: missing.append(t)
        t+=STEP
    raise RuntimeError(f'synchronized rows {len(keys)} != {expected}; first missing={missing[:10]}')

full=[]
for t in keys:
    s=spot_map[t]; ix=idx_map[t]
    sl=float(s[3]); il=float(ix[3])
    bp=(sl/il-1)*10000
    full.append({
        'open_time_utc':iso(t), 'open_time_ms':t,
        'spot_open':float(s[1]),'spot_high':float(s[2]),'spot_low':sl,'spot_close':float(s[4]),
        'index_open':float(ix[1]),'index_high':float(ix[2]),'index_low':il,'index_close':float(ix[4]),
        'deviation_bp':bp,'tier':tier(bp)
    })

candidates=[r for r in full if r['deviation_bp']<=-20]
candidates.sort(key=lambda r:(r['deviation_bp'],r['open_time_ms']))
for i,r in enumerate(candidates,1): r['rank']=i

events=[]
if candidates:
    chrono=sorted(candidates,key=lambda r:r['open_time_ms'])
    groups=[]; g=[chrono[0]]
    for r in chrono[1:]:
        if r['open_time_ms']-g[-1]['open_time_ms']<=STEP:
            g.append(r)
        else:
            groups.append(g); g=[r]
    groups.append(g)
    for g in groups:
        worst=min(g,key=lambda r:r['deviation_bp'])
        events.append({
            'start_utc':g[0]['open_time_utc'],
            'end_utc':iso(g[-1]['open_time_ms']+STEP),
            'windows':len(g),
            'min_deviation_bp':worst['deviation_bp'],
            'worst_window_utc':worst['open_time_utc'],
            'spot_low_at_worst':worst['spot_low'],
            'index_low_at_worst':worst['index_low'],
            'priority':tier(worst['deviation_bp'])
        })
    events.sort(key=lambda r:r['min_deviation_bp'])
    for i,r in enumerate(events,1): r['event_rank']=i


def write_csv(path, rows):
    if not rows:
        Path(path).write_text('',encoding='utf-8'); return
    fields=list(rows[0].keys())
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

write_csv(OUT/'btc_2026_30m_spot_index_full.csv', full)
write_csv(OUT/'btc_2026_30m_candidates_le_20bp.csv', candidates)
write_csv(OUT/'btc_2026_30m_candidate_events.csv', events)

summary={
    'period_start_utc':'2026-01-01 00:00:00',
    'period_end_inclusive_utc':'2026-09-05 23:59:59',
    'expected_30m_rows':expected,
    'synchronized_rows':len(keys),
    'candidate_windows_le_-20bp':len(candidates),
    'strong_windows_le_-30bp':sum(1 for r in full if r['deviation_bp']<=-30),
    'extreme_windows_le_-50bp':sum(1 for r in full if r['deviation_bp']<=-50),
    'candidate_events':len(events),
    'worst_deviation_bp':min(r['deviation_bp'] for r in full),
    'top_20_windows':[{k:r[k] for k in ['rank','open_time_utc','spot_low','index_low','deviation_bp','tier']} for r in candidates[:20]],
    'top_20_events':events[:20],
}
(OUT/'scan_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
