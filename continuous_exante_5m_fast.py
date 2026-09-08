#!/usr/bin/env python3
from __future__ import annotations
import csv, io, json, zipfile, requests
from datetime import datetime, timezone
from pathlib import Path

TIERS_BP=[40,60,80,100,120]
CAPITAL_PER_TIER=2000.0
OUT=Path('output/continuous_exante_5m_fast'); OUT.mkdir(parents=True,exist_ok=True)

def read_zip(url):
    r=requests.get(url,timeout=90); r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name=next(n for n in z.namelist() if n.endswith('.csv'))
        lines=z.read(name).decode('utf-8').strip().splitlines()
    out=[]
    for ln in lines:
        p=ln.split(',')
        try: t=int(float(p[0]))
        except: continue
        if t>10**14: t//=1000
        out.append((t,float(p[1]),float(p[2]),float(p[3]),float(p[4])))
    return out

def spot_month(ym): return f'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/5m/BTCUSDT-5m-{ym}.zip'
def idx_month(ym): return f'https://data.binance.vision/data/futures/um/monthly/indexPriceKlines/BTCUSDT/5m/BTCUSDT-5m-{ym}.zip'
def spot_day(d): return f'https://data.binance.vision/data/spot/daily/klines/BTCUSDT/5m/BTCUSDT-5m-{d}.zip'
def idx_day(d): return f'https://data.binance.vision/data/futures/um/daily/indexPriceKlines/BTCUSDT/5m/BTCUSDT-5m-{d}.zip'

spot=[]; idx=[]
for m in range(1,9):
    ym=f'2026-{m:02d}'
    spot += read_zip(spot_month(ym))
    idx += read_zip(idx_month(ym))
for d in range(1,6):
    ds=f'2026-09-{d:02d}'
    spot += read_zip(spot_day(ds))
    idx += read_zip(idx_day(ds))

spot=sorted({r[0]:r for r in spot}.values())
idx_map={r[0]:r for r in idx}
rows=[]; trigger_days=set(); prev_idx_close=None; evaluated=0
for s in spot:
    t,so,sh,sl,sc=s
    ir=idx_map.get(t)
    if ir is None: continue
    evaluated += 1
    ic=ir[4]
    if prev_idx_close is not None:
        prices=[prev_idx_close*(1-bp/10000) for bp in TIERS_BP]
        layers=sum(sl<p for p in prices)
        if layers:
            dt=datetime.fromtimestamp(t/1000,tz=timezone.utc)
            ds=dt.strftime('%Y-%m-%d'); trigger_days.add(ds)
            rows.append({'bar_open_ms':t,'bar_open_utc':dt.isoformat(),'date':ds,
                         'prev_completed_5m_index_close':prev_idx_close,'spot_5m_low':sl,
                         'potential_layers':layers,'potential_capital_usd':layers*CAPITAL_PER_TIER,
                         'tier_prices':'|'.join(f'{p:.8f}' for p in prices)})
    prev_idx_close=ic

with open(OUT/'potential_trigger_bars.csv','w',newline='',encoding='utf-8-sig') as f:
    fields=list(rows[0].keys()) if rows else ['bar_open_ms']; w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
summary={
 'policy':'STRICT EX-ANTE 5m stale peg; previous completed 5m Binance index close; static -40/-60/-80/-100/-120bp tiers; $2k each; no intra-bar repricing.',
 'evaluated_5m_bars':evaluated,'potential_trigger_bars':len(rows),'trigger_days':len(trigger_days),
 'potential_capital_usd':sum(r['potential_capital_usd'] for r in rows),
 'layer_distribution':{str(k):sum(r['potential_layers']==k for r in rows) for k in range(1,6)},
 'trigger_day_list':sorted(trigger_days)}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
