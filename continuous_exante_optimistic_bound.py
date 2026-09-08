#!/usr/bin/env python3
import csv, io, json, zipfile, requests, time
from pathlib import Path
from datetime import datetime, timezone

START=datetime(2026,1,1,tzinfo=timezone.utc); END=datetime(2026,9,6,tzinfo=timezone.utc)
BPS=[40,60,80,100,120]; LAYER=2000; COST=32.5
OUT=Path('output/continuous_exante_bound'); OUT.mkdir(parents=True,exist_ok=True)
SPOT_M='https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-{ym}.zip'
SPOT_D='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{ymd}.zip'
IDX_M='https://data.binance.vision/data/futures/um/monthly/indexPriceKlines/BTCUSDT/1m/BTCUSDT-1m-{ym}.zip'
IDX_D='https://data.binance.vision/data/futures/um/daily/indexPriceKlines/BTCUSDT/1m/BTCUSDT-1m-{ymd}.zip'

def get(url):
    for i in range(5):
        try:
            r=requests.get(url,timeout=90)
            if r.status_code==200:return r
        except Exception: pass
        time.sleep(min(2**i,10))
    raise RuntimeError(url)

def rows(url):
    z=zipfile.ZipFile(io.BytesIO(get(url).content)); n=next(x for x in z.namelist() if x.endswith('.csv'))
    rr=list(csv.reader(z.read(n).decode('utf-8-sig').splitlines()))
    if rr and not rr[0][0].replace('.','',1).isdigit(): rr=rr[1:]
    return rr

def ms(x):
    v=int(float(x)); return v//1000 if v>10**14 else v

def load(mt,dt):
    d={}
    for m in range(1,9):
        for r in rows(mt.format(ym=f'2026-{m:02d}')): d[ms(r[0])]=r
    for day in range(1,6):
        for r in rows(dt.format(ymd=f'2026-09-{day:02d}')): d[ms(r[0])]=r
    return d

spot=load(SPOT_M,SPOT_D); idx=load(IDX_M,IDX_D)
start=int(START.timestamp()*1000); end=int(END.timestamp()*1000)
# backfill any missing index day
for dct,tmpl in [(spot,SPOT_D),(idx,IDX_D)]:
    missing=sorted({datetime.fromtimestamp(t/1000,tz=timezone.utc).strftime('%Y-%m-%d') for t in range(start,end,60000) if t not in dct})
    for ymd in missing:
        for r in rows(tmpl.format(ymd=ymd)): dct[ms(r[0])]=r

out=[]; totals={'low':0.0,'close':0.0,'high':0.0}; caps=0.0
keys=list(range(start,end,60000))
for i,t in enumerate(keys[1:],1):
    p=keys[i-1]; anchor=float(idx[p][4]); low=float(spot[t][3])
    filled=[]
    for bp in BPS:
        px=anchor*(1-bp/10000)
        if low<px: filled.append(px)
    if not filled: continue
    cap=LAYER*len(filled); qty=sum(LAYER/x for x in filled); entry=cap/qty; caps+=cap
    il=float(idx[t][3]); ic=float(idx[t][4]); ih=float(idx[t][2])
    rec={'minute_utc':datetime.fromtimestamp(t/1000,tz=timezone.utc).isoformat(),'layers':len(filled),'capital':cap,'entry':entry,'index_low':il,'index_close':ic,'index_high':ih}
    for k,pv in [('low',il),('close',ic),('high',ih)]:
        gross=(pv/entry-1)*10000; net=gross-COST; pnl=cap*net/10000
        rec[f'{k}_gross_bp']=gross; rec[f'{k}_net_bp']=net; rec[f'{k}_pnl_usd']=pnl; totals[k]+=pnl
    out.append(rec)

with (OUT/'trigger_bound.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
summary={
 'period':'2026-01-01..2026-09-05 UTC','trigger_minutes':len(out),'traded_capital_proxy_usd':caps,'cost_bp':COST,
 'interpretation':{
  'index_high':'OPTIMISTIC UPPER-BOUND PROXY: assumes every fill can hedge near the highest Binance index price of that same minute. Not executable evidence.',
  'index_close':'diagnostic proxy only','index_low':'pessimistic diagnostic proxy only'
 },
 'total_pnl_usd':totals,
 'positive_minutes_after_cost':{k:sum(1 for r in out if r[f'{k}_net_bp']>0) for k in totals},
 'negative_minutes_after_cost':{k:sum(1 for r in out if r[f'{k}_net_bp']<0) for k in totals},
 'max_minute_high_proxy_pnl':max(r['high_pnl_usd'] for r in out),'min_minute_high_proxy_pnl':min(r['high_pnl_usd'] for r in out)
}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
