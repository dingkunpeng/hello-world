#!/usr/bin/env python3
import csv,io,json,tarfile,zipfile,requests
from datetime import datetime,timezone
from pathlib import Path

IN=Path('output/annual_ev/annual_1m_triage.csv')
OUT=Path('output/annual_ev/residual_screen'); OUT.mkdir(parents=True,exist_ok=True)
FIRST_LAYER_BP=40

def ms(s): return int(datetime.strptime(s,'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()*1000)
def norm_ts(v):
    x=int(float(v)); return x//1000 if x>10**14 else x

def binance_low(date,minute):
    url=f'https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-{date}.zip'
    r=requests.get(url,timeout=120); r.raise_for_status(); z=zipfile.ZipFile(io.BytesIO(r.content)); name=next(n for n in z.namelist() if n.endswith('.csv'))
    rows=list(csv.reader(z.read(name).decode('utf-8-sig').strip().splitlines()))
    if rows and not rows[0][0].replace('.','',1).isdigit(): rows=rows[1:]
    st=ms(minute); en=st+60000; tr=[]
    for x in rows:
        t=norm_ts(x[5])
        if st<=t<en: tr.append((float(x[1]),float(x[2]),t,str(x[6]).lower()=='true'))
    low=min(x[0] for x in tr); lows=[x for x in tr if x[0]==low]; t0=min(x[2] for x in lows)
    return low,t0,sum(x[1] for x in lows),sum(x[1] for x in lows if x[3])

def okx_pre_bid(date,t0):
    ymd=date.replace('-','')
    url=f'https://static.okx.com/cdn/okx/match/orderbook/L2/5000lv/daily/{ymd}/BTC-USDT-L2orderbook-5000lv-{date}.tar.gz'
    r=requests.get(url,stream=True,timeout=120); r.raise_for_status()
    tmp=Path('/tmp')/f'okx5000-{date}.tar.gz'
    with tmp.open('wb') as f:
        for chunk in r.iter_content(1024*1024):
            if chunk: f.write(chunk)
    bids={}; last_ts=None; last_best=None
    with tarfile.open(tmp,'r:gz') as tf:
        member=next(m for m in tf.getmembers() if m.isfile()); raw=tf.extractfile(member)
        for line in io.TextIOWrapper(raw,encoding='utf-8',errors='strict'):
            obj=json.loads(line); ts=int(obj['ts'])
            if ts>t0: break
            action=obj.get('action')
            if action=='snapshot': bids={float(p):float(q) for p,q,*rest in obj.get('bids',[]) if float(q)>0}
            else:
                for item in obj.get('bids',[]):
                    p=float(item[0]); q=float(item[1])
                    if q<=0: bids.pop(p,None)
                    else: bids[p]=q
            if bids:
                last_ts=ts; last_best=max(bids)
    try: tmp.unlink()
    except: pass
    return last_best,last_ts,url

rows=[]
with IN.open(encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        if r['status_1m']!='L2_PRIORITY': rows.append(r)

out=[]
for i,r in enumerate(rows,1):
    minute=r['worst_1m_utc']; date=minute[:10]
    print(i,'/',len(rows),date,minute,r['status_1m'])
    try:
        low,t0,lowqty,sellqty=binance_low(date,minute)
        bid,bts,url=okx_pre_bid(date,t0)
        gap=(low/bid-1)*10000 if bid else None
        layer_px=bid*(1-FIRST_LAYER_BP/10000) if bid else None
        fills=bool(bid and low<layer_px)
        out.append({'rank_1m':r['rank_1m'],'date':date,'minute_utc':minute,'status_1m':r['status_1m'],'spot_index_1m_bp':r['deviation_1m_bp'],
                    'binance_low':low,'t0_ms':t0,'low_qty':lowqty,'seller_aggressor_low_qty':sellqty,'okx_book_ms':bts,'okx_pre_best_bid':bid,
                    'cross_spot_gap_bp':gap,'first_layer_limit_px':layer_px,'strict_price_through_40bp':fills,'promote_to_level_a':fills,'source_url':url})
    except Exception as e:
        out.append({'rank_1m':r['rank_1m'],'date':date,'minute_utc':minute,'status_1m':r['status_1m'],'spot_index_1m_bp':r['deviation_1m_bp'],
                    'error':repr(e),'promote_to_level_a':False})

fields=sorted({k for x in out for k in x.keys()})
with (OUT/'residual_screen.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)
summary={'residual_candidates':len(out),'promoted':sum(bool(x.get('promote_to_level_a')) for x in out),
         'promoted_rows':[x for x in out if x.get('promote_to_level_a')],
         'rejected_rows':[x for x in out if not x.get('promote_to_level_a') and not x.get('error')],
         'errors':[x for x in out if x.get('error')]}
(OUT/'residual_screen_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
