#!/usr/bin/env python3
import csv,gzip,io,json,requests
from pathlib import Path
from datetime import datetime,timezone,timedelta

EVENTS=[
 {'date':'2026-02-03','t0':1770143385276,'binance_low':73801.8},
 {'date':'2026-04-02','t0':1775120393787,'binance_low':66180.0},
]
LADDER=[40,60,80,100,120]; LAYER=2000; DELAYS=[0,50,100,250,500]; COST=32.5
OUT=Path('output/annual_ev/promoted_residual_l2'); OUT.mkdir(parents=True,exist_ok=True)
API='https://www.okx.com/api/v5/public/market-data-history'

def day_bounds(t):
    d=datetime.fromtimestamp(t/1000,tz=timezone.utc); s=datetime(d.year,d.month,d.day,tzinfo=timezone.utc); return int(s.timestamp()*1000),int((s+timedelta(days=1)).timestamp()*1000)
def item_for(e):
    b,en=day_bounds(e['t0']); p={'module':'6','instType':'SPOT','dateAggrType':'daily','begin':str(b),'end':str(en),'instIdList':'BTC-USDT'}
    r=requests.get(API,params=p,timeout=30); r.raise_for_status(); g=r.json()['data'][0]['details'][0]['groupDetails']
    c=[x for x in g if x.get('filename')=='BTC-USDT.OK.csv.gz']; ex=[x for x in c if x.get('dateTs')==str(b)]; return (ex or c)[0]
def vwap(row,q):
    if not row or q<=0:return None,0
    rem=q; val=0
    for i in range(1,51):
        ps=row.get(f'bid_{i}_px'); qs=row.get(f'bid_{i}_qty')
        if not ps or not qs: continue
        p=float(ps); a=float(qs); take=min(rem,a); val+=take*p; rem-=take
        if rem<=1e-12: break
    return (val/q if rem<=1e-10 else None),q-rem

out=[]
for e in EVENTS:
    item=item_for(e); print(e['date'],item['sizeMB'])
    arr=[e['t0']+d for d in DELAYS]; snaps={}; prev=None; pts=None; idx=0
    r=requests.get(item['url'],stream=True,timeout=180); r.raise_for_status(); r.raw.decode_content=False
    with gzip.GzipFile(fileobj=r.raw,mode='rb') as gz:
        rd=csv.DictReader(io.TextIOWrapper(gz,encoding='utf-8',errors='strict',newline=''))
        for row in rd:
            ts=int(row['exchTimeMs'])
            while idx<len(arr) and arr[idx]<ts: snaps[DELAYS[idx]]=(pts,prev.copy() if prev else None); idx+=1
            prev=row; pts=ts
            if idx>=len(arr) and ts>arr[-1]+50: break
    while idx<len(arr): snaps[DELAYS[idx]]=(pts,prev.copy() if prev else None); idx+=1
    pre_ts,pre=snaps[0]; fair=float(pre['bid_1_px'])
    ladd=[]
    for bp in LADDER:
        px=fair*(1-bp/10000); ladd.append({'offset_bp':bp,'limit_px':px,'filled_price_through':e['binance_low']<px})
    filled=[x for x in ladd if x['filled_price_through']]; cap=LAYER*len(filled); qty=sum(LAYER/x['limit_px'] for x in filled); entry=cap/qty if qty else None
    for d in DELAYS:
        bts,row=snaps[d]; sell,cov=vwap(row,qty); gross=(sell/entry-1)*10000 if sell and entry else None; net=gross-COST if gross is not None else None
        out.append({'event':e['date'],'t0_ms':e['t0'],'binance_low':e['binance_low'],'okx_pre_book_ms':pre_ts,'okx_pre_best_bid':fair,'filled_layers':len(filled),'capital_usd':cap,'qty_btc':qty,'binance_entry':entry,'delay_ms':d,'okx_book_ms':bts,'book_age_ms':e['t0']+d-bts if bts else None,'sell_vwap':sell,'covered_qty':cov,'gross_edge_bp':gross,'net_after_32_5bp':net,'net_pnl_usd':cap*net/10000 if net is not None else None,'ladder_json':json.dumps(ladd,separators=(',',':'))})
fields=list(out[0].keys())
with (OUT/'matrix.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)
summary={'cost_bp':COST,'by_event':{},'total_net_pnl_100ms':0}
for e in EVENTS:
    r=next(x for x in out if x['event']==e['date'] and x['delay_ms']==100); summary['by_event'][e['date']]=r; summary['total_net_pnl_100ms']+=(r['net_pnl_usd'] or 0)
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
