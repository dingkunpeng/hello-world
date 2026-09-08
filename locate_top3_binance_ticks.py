#!/usr/bin/env python3
import csv, json, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

EVENTS=[
 {'name':'2026-02-07','minute':'2026-02-07 07:46:00'},
 {'name':'2026-07-10','minute':'2026-07-10 12:14:00'},
 {'name':'2026-08-22','minute':'2026-08-22 05:10:00'},
]
URL='https://data-api.binance.vision/api/v3/aggTrades'
OUT=Path('output/annual_ev/top3_ticks'); OUT.mkdir(parents=True,exist_ok=True)

def ms(s): return int(datetime.strptime(s,'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()*1000)
rows=[]
for e in EVENTS:
    st=ms(e['minute']); en=st+60_000-1
    alltr=[]; cur=st
    while cur<=en:
        r=requests.get(URL,params={'symbol':'BTCUSDT','startTime':cur,'endTime':en,'limit':1000},timeout=30); r.raise_for_status(); data=r.json()
        if not data: break
        alltr.extend(data)
        nxt=max(int(x['T']) for x in data)+1
        if nxt<=cur: break
        cur=nxt
        if len(data)<1000: break
    if not alltr: continue
    low=min(float(x['p']) for x in alltr)
    lowtr=[x for x in alltr if float(x['p'])==low]
    first=min(lowtr,key=lambda x:int(x['T']))
    # earliest trade that reaches within 0.5bp of the minute low as an auxiliary sweep onset marker
    thr=low*(1+0.00005)
    near=[x for x in alltr if float(x['p'])<=thr]
    rows.append({
      'event':e['name'],'minute_utc':e['minute'],'aggtrade_count':len(alltr),'low':low,
      'first_low_ms':int(first['T']),'first_low_utc':datetime.fromtimestamp(int(first['T'])/1000,tz=timezone.utc).isoformat(),
      'low_trade_qty':sum(float(x['q']) for x in lowtr),'low_trade_count':len(lowtr),
      'seller_aggressor_low_qty':sum(float(x['q']) for x in lowtr if x['m']),
      'near_low_first_ms':min(int(x['T']) for x in near) if near else None,
    })
with (OUT/'top3_binance_tick_locations.csv').open('w',newline='',encoding='utf-8-sig') as f:
    fields=list(rows[0].keys()); w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
(OUT/'top3_binance_tick_locations.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rows,ensure_ascii=False,indent=2))
