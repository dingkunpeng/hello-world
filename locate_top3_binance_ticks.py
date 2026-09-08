#!/usr/bin/env python3
import csv, io, json, zipfile, requests
from datetime import datetime, timezone
from pathlib import Path

EVENTS=[
 {'name':'2026-02-07','minute':'2026-02-07 07:46:00'},
 {'name':'2026-07-10','minute':'2026-07-10 12:14:00'},
 {'name':'2026-08-22','minute':'2026-08-22 05:10:00'},
]
ARCH='https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-{date}.zip'
OUT=Path('output/annual_ev/top3_ticks'); OUT.mkdir(parents=True,exist_ok=True)

def ms(s): return int(datetime.strptime(s,'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()*1000)
def norm_ms(x):
    v=int(float(x)); return v//1000 if v>10**14 else v

def load_day(date):
    r=requests.get(ARCH.format(date=date),timeout=90); r.raise_for_status()
    z=zipfile.ZipFile(io.BytesIO(r.content))
    name=next(n for n in z.namelist() if n.endswith('.csv'))
    text=io.TextIOWrapper(z.open(name),encoding='utf-8-sig',newline='')
    rd=csv.reader(text)
    out=[]
    for row in rd:
        if not row: continue
        if not row[0].replace('.','',1).isdigit(): continue
        # a,p,q,f,l,T,m,M
        out.append({'a':int(row[0]),'p':float(row[1]),'q':float(row[2]),'f':int(row[3]),'l':int(row[4]),'T':norm_ms(row[5]),'m':str(row[6]).lower()=='true'})
    return out

rows=[]
for e in EVENTS:
    st=ms(e['minute']); en=st+60_000
    day=e['minute'][:10]
    all_day=load_day(day)
    tr=[x for x in all_day if st<=x['T']<en]
    if not tr: continue
    low=min(x['p'] for x in tr)
    lowtr=[x for x in tr if x['p']==low]
    first=min(lowtr,key=lambda x:x['T'])
    thr=low*(1+0.00005)
    near=[x for x in tr if x['p']<=thr]
    rows.append({
      'event':e['name'],'minute_utc':e['minute'],'aggtrade_count':len(tr),'low':low,
      'first_low_ms':first['T'],'first_low_utc':datetime.fromtimestamp(first['T']/1000,tz=timezone.utc).isoformat(),
      'low_trade_qty':sum(x['q'] for x in lowtr),'low_trade_count':len(lowtr),
      'seller_aggressor_low_qty':sum(x['q'] for x in lowtr if x['m']),
      'near_low_first_ms':min(x['T'] for x in near) if near else None,
      'source':'Binance official daily aggTrades archive'
    })
with (OUT/'top3_binance_tick_locations.csv').open('w',newline='',encoding='utf-8-sig') as f:
    fields=list(rows[0].keys()); w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
(OUT/'top3_binance_tick_locations.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rows,ensure_ascii=False,indent=2))
