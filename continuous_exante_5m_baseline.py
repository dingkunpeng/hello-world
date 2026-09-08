#!/usr/bin/env python3
"""
Strict 5-minute ex-ante baseline screen.

At the start of each 5m bar, anchor to the PREVIOUS COMPLETED 5m Binance index close.
Place five static BTCUSDT spot bids at -40/-60/-80/-100/-120bp, $2k each.
Do not update them for the full 5 minutes.
This file performs the cheap annual prefilter only; exact fills + OKX L2 are replayed separately.
"""
from __future__ import annotations
import csv, io, json, zipfile, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

START='2026-01-01'
END='2026-09-05'
TIERS_BP=[40,60,80,100,120]
CAPITAL_PER_TIER=2000.0
OUT=Path('output/continuous_exante_5m')
OUT.mkdir(parents=True,exist_ok=True)

SPOT_DAY='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/5m/BTCUSDT-5m-{date}.zip'
IDX_DAY='https://data.binance.vision/data/futures/um/daily/indexPriceKlines/BTCUSDT/5m/BTCUSDT-5m-{date}.zip'

def get_zip_csv(url):
    r=requests.get(url,timeout=60); r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name=next(n for n in z.namelist() if n.endswith('.csv'))
        raw=z.read(name).decode('utf-8').strip().splitlines()
    rows=[]
    for ln in raw:
        p=ln.split(',')
        try: ot=int(float(p[0]))
        except: continue
        # timestamps may be microseconds in spot archives from 2025 onward
        if ot>10**14: ot//=1000
        rows.append((ot,float(p[1]),float(p[2]),float(p[3]),float(p[4])))
    return rows

def daterange(a,b):
    d=datetime.strptime(a,'%Y-%m-%d').date(); e=datetime.strptime(b,'%Y-%m-%d').date()
    while d<=e:
        yield d.isoformat(); d+=timedelta(days=1)

all_rows=[]
trigger_days=set()
prev_index_close=None
prev_time=None
for ds in daterange(START,END):
    spot=get_zip_csv(SPOT_DAY.format(date=ds))
    idx=get_zip_csv(IDX_DAY.format(date=ds))
    im={r[0]:r for r in idx}
    for s in spot:
        t,so,sh,sl,sc=s
        ir=im.get(t)
        if ir is None: continue
        io,ih,il,ic=ir[1:]
        if prev_index_close is not None:
            prices=[prev_index_close*(1-bp/10000) for bp in TIERS_BP]
            # cheap high-recall prefilter: current 5m spot low strictly below tier
            hit_layers=sum(sl<p for p in prices)
            if hit_layers>0:
                trigger_days.add(ds)
                all_rows.append({
                    'bar_open_ms':t,
                    'bar_open_utc':datetime.fromtimestamp(t/1000,tz=timezone.utc).isoformat(),
                    'date':ds,
                    'prev_completed_5m_index_close':prev_index_close,
                    'spot_5m_low':sl,
                    'potential_layers':hit_layers,
                    'potential_capital_usd':hit_layers*CAPITAL_PER_TIER,
                    'tier_prices':'|'.join(f'{p:.8f}' for p in prices),
                })
        prev_index_close=ic
        prev_time=t

with open(OUT/'potential_trigger_bars.csv','w',newline='',encoding='utf-8-sig') as f:
    fields=list(all_rows[0].keys()) if all_rows else ['bar_open_ms']
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(all_rows)
summary={
    'policy':'STRICT EX-ANTE 5m stale peg: each 5m bar anchored only to previous completed 5m Binance index close; static tiers -40/-60/-80/-100/-120bp, $2k each; no intra-bar repricing.',
    'period_start':START,'period_end':END,
    'potential_trigger_bars':len(all_rows),
    'trigger_days':len(trigger_days),
    'potential_capital_usd':sum(r['potential_capital_usd'] for r in all_rows),
    'trigger_day_list':sorted(trigger_days),
    'layer_distribution':{str(k):sum(r['potential_layers']==k for r in all_rows) for k in range(1,6)},
}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
