#!/usr/bin/env python3
import csv, io, json, zipfile, requests, time
from pathlib import Path
from datetime import datetime, timezone

START=datetime(2026,1,1,tzinfo=timezone.utc); END=datetime(2026,9,6,tzinfo=timezone.utc)
LADDER=[40,60,80,100,120]; LAYER=2000
OUT=Path('output/continuous_dynamic'); OUT.mkdir(parents=True,exist_ok=True)
SPM='https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-{ym}.zip'
SPD='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{ymd}.zip'
IXM='https://data.binance.vision/data/futures/um/monthly/indexPriceKlines/BTCUSDT/1m/BTCUSDT-1m-{ym}.zip'
IXD='https://data.binance.vision/data/futures/um/daily/indexPriceKlines/BTCUSDT/1m/BTCUSDT-1m-{ymd}.zip'

def get(u,tries=5):
    e=None
    for i in range(tries):
        try:
            r=requests.get(u,timeout=90)
            if r.status_code==200:return r
            e=RuntimeError(f'{r.status_code} {u}')
        except Exception as x:e=x
        time.sleep(min(2**i,10))
    raise e

def rows(u):
    z=zipfile.ZipFile(io.BytesIO(get(u).content)); n=next(x for x in z.namelist() if x.endswith('.csv'))
    a=list(csv.reader(z.read(n).decode('utf-8-sig').splitlines()))
    if a and not a[0][0].replace('.','',1).isdigit():a=a[1:]
    return a

def ms(x):
    v=int(float(x)); return v//1000 if v>10**14 else v

def add(d,a):
    for r in a:
        if r:d[ms(r[0])]=r

def load(mt,dt):
    d={}
    for m in range(1,9):
        ym=f'2026-{m:02d}'; add(d,rows(mt.format(ym=ym)))
    for q in range(1,6):
        y=f'2026-09-{q:02d}'; add(d,rows(dt.format(ymd=y)))
    return d

def iso(t):return datetime.fromtimestamp(t/1000,tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
def fill_missing(d,tmpl,s,e):
    miss=set(); t=s
    while t<e:
        if t not in d:miss.add(iso(t)[:10])
        t+=60000
    for y in sorted(miss):add(d,rows(tmpl.format(ymd=y)))
    return sorted(miss)

s=int(START.timestamp()*1000); e=int(END.timestamp()*1000)
spot=load(SPM,SPD); idx=load(IXM,IXD)
sb=fill_missing(spot,SPD,s,e); ib=fill_missing(idx,IXD,s,e)

out=[]; t=s; total=0
while t<e:
    if t not in spot or t not in idx:raise RuntimeError(f'missing {iso(t)}')
    sl=float(spot[t][3]); il=float(idx[t][3]); so=float(spot[t][1]); sc=float(spot[t][4]); ic=float(idx[t][4])
    dev=(sl/il-1)*10000
    guaranteed=[]
    for bp in LADDER:
        # index_low is a LOWER ENVELOPE of the live fair price during this minute.
        # Crossing this conservative level guarantees that the live dynamic bid at the spot-low instant
        # was at least as high, hence price-through must have occurred under an ideal continuously repriced peg.
        px=il*(1-bp/10000)
        if sl < px: guaranteed.append((bp,px))
    if guaranteed:
        cap=LAYER*len(guaranteed); qty=sum(LAYER/px for bp,px in guaranteed); entry=cap/qty
        out.append({'minute_utc':iso(t),'minute_ms':t,'spot_open':so,'spot_low':sl,'spot_close':sc,
                    'index_low':il,'index_close':ic,'spot_vs_index_low_bp':dev,
                    'guaranteed_layers':len(guaranteed),'deepest_guaranteed_bp':max(x[0] for x in guaranteed),
                    'conservative_capital_usd':cap,'conservative_entry':entry,
                    'levels_json':json.dumps([{'offset_bp':bp,'floor_limit_px':px} for bp,px in guaranteed],separators=(',',':'))})
    total+=1; t+=60000

if out:
    with (OUT/'guaranteed_trigger_minutes.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(out[0].keys()));w.writeheader();w.writerows(out)
clusters=[]
if out:
    cur=[out[0]]
    for r in out[1:]:
        if r['minute_ms']-cur[-1]['minute_ms']<=60000:cur.append(r)
        else:clusters.append(cur);cur=[r]
    clusters.append(cur)
cr=[]
for c in clusters:
    cr.append({'start_utc':c[0]['minute_utc'],'end_utc':c[-1]['minute_utc'],'minutes':len(c),
               'max_layers':max(x['guaranteed_layers'] for x in c),
               'worst_spot_vs_index_low_bp':min(x['spot_vs_index_low_bp'] for x in c)})
if cr:
    with (OUT/'guaranteed_trigger_clusters.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(cr[0].keys()));w.writeheader();w.writerows(cr)
summary={'period_utc':'2026-01-01 through 2026-09-05 inclusive','minutes':total,
         'method':'Full-year exhaustive 1m lower-envelope screen for an ideal continuously repriced fair-price peg. Current-minute index low is used only as a conservative offline proof of unavoidable dynamic price-through, never as a live decision input.',
         'guaranteed_trigger_minutes':len(out),'guaranteed_trigger_clusters':len(cr),
         'guaranteed_trigger_days':len(set(r['minute_utc'][:10] for r in out)),
         'layers_histogram':{str(k):sum(1 for r in out if r['guaranteed_layers']==k) for k in range(1,6)},
         'worst_spot_vs_index_low_bp':min([r['spot_vs_index_low_bp'] for r in out],default=None),
         'daily_backfill':{'spot':sb,'index':ib},
         'important_limitations':['This is a sufficient-trigger screen, not exact fill timing or P&L.','It undercounts opportunities when index was above its minute low at the spot-low instant.','Exact Binance aggTrades + OKX L2 at the trigger instant are required for final execution and 100ms P&L.','Ideal dynamic repricing ignores cancel/replace latency; stale-anchor risk must be tested separately.']}
(OUT/'dynamic_floor_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
