#!/usr/bin/env python3
import csv, gzip, io, json, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

API='https://www.okx.com/api/v5/public/market-data-history'
EVENTS=[
 {'name':'2026-02-07','t0':1770450371402,'binance_low':67300.0},
 {'name':'2026-07-10','t0':1783685656306,'binance_low':63999.0},
 {'name':'2026-08-22','t0':1787375457287,'binance_low':76618.7},
]
LADDER_OFFSETS_BP=[40,60,80,100,120]
LAYER_USD=2000
DELAYS=[0,50,100,250,500]
OUT=Path('output/annual_ev/top3_okx_l2'); OUT.mkdir(parents=True,exist_ok=True)


def day_bounds(ms):
    dt=datetime.fromtimestamp(ms/1000,tz=timezone.utc)
    day=datetime(dt.year,dt.month,dt.day,tzinfo=timezone.utc)
    return int(day.timestamp()*1000), int((day+timedelta(days=1)).timestamp()*1000)

def book_vwap(row, qty):
    rem=qty; value=0.0; levels=[]
    if qty<=0: return None,0,[]
    for i in range(1,51):
        ps=row.get(f'bid_{i}_px'); qs=row.get(f'bid_{i}_qty')
        if not ps or not qs: continue
        p=float(ps); q=float(qs)
        if q<=0: continue
        take=min(rem,q)
        if take>0:
            value+=take*p; rem-=take; levels.append((i,p,q,take))
        if rem<=1e-12: break
    return (value/qty if rem<=1e-10 else None), qty-rem, levels

def signed_file(t0):
    begin,end=day_bounds(t0)
    params={'module':'6','instType':'SPOT','dateAggrType':'daily','begin':str(begin),'end':str(end),'instIdList':'BTC-USDT'}
    r=requests.get(API,params=params,timeout=30); r.raise_for_status(); js=r.json()
    details=js['data'][0]['details'][0]['groupDetails']
    item=next(x for x in details if x['filename']=='BTC-USDT.OK.csv.gz')
    return item

all_results=[]
for ev in EVENTS:
    item=signed_file(ev['t0'])
    print('EVENT',ev['name'],'file',item['sizeMB'],'MB')
    arrivals=[ev['t0']+d for d in DELAYS]
    needed=set(arrivals)
    snaps={}
    prev=None; prev_ts=None
    resp=requests.get(item['url'],stream=True,timeout=120); resp.raise_for_status(); resp.raw.decode_content=False
    with gzip.GzipFile(fileobj=resp.raw,mode='rb') as gz:
        txt=io.TextIOWrapper(gz,encoding='utf-8',errors='strict',newline='')
        rd=csv.DictReader(txt)
        for row in rd:
            ts=int(row['exchTimeMs'])
            for arr in arrivals:
                if arr not in snaps and ts>arr:
                    snaps[arr]=(prev_ts,prev.copy() if prev else None)
            prev=row; prev_ts=ts
            if len(snaps)==len(arrivals) and ts>max(arrivals)+50: break
    # pre-event book for ladder anchor = latest book <= t0
    pre_ts,pre=snaps[ev['t0']]
    if pre is None: raise RuntimeError('no pre book '+ev['name'])
    fair=float(pre['bid_1_px'])
    ladder=[]
    for off in LADDER_OFFSETS_BP:
        px=fair*(1-off/10000)
        # strict price-through only; touching low is not counted as guaranteed fill
        filled=ev['binance_low'] < px
        ladder.append({'offset_bp':off,'limit_px':px,'filled_price_through':filled})
    filled=[x for x in ladder if x['filled_price_through']]
    capital=LAYER_USD*len(filled)
    qty=sum(LAYER_USD/x['limit_px'] for x in filled)
    entry=(capital/qty) if qty>0 else None
    for d in DELAYS:
        bts,row=snaps[ev['t0']+d]
        vwap,covered,levels=book_vwap(row,qty) if row and qty>0 else (None,0,[])
        gross=((vwap/entry-1)*10000) if vwap and entry else None
        all_results.append({
          'event':ev['name'],'t0_ms':ev['t0'],'binance_low':ev['binance_low'],
          'okx_pre_book_ms':pre_ts,'okx_pre_best_bid':fair,
          'filled_layers':len(filled),'capital_usd':capital,'qty_btc':qty,'binance_ladder_entry':entry,
          'delay_ms':d,'okx_book_ms':bts,'book_age_ms':(ev['t0']+d-bts) if bts else None,
          'sell_vwap_usdt':vwap,'covered_qty':covered,'gross_edge_bp':gross,
          'net_bp_bnb_plus_okx_plus_5bp':(gross-32.5) if gross is not None else None,
          'net_bp_standard_plus_okx_plus_5bp':(gross-35.0) if gross is not None else None,
          'ladder_json':json.dumps(ladder,separators=(',',':'))
        })

fields=list(all_results[0].keys())
with (OUT/'top3_okx_l2_matrix.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(all_results)
(OUT/'top3_okx_l2_matrix.json').write_text(json.dumps(all_results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(all_results,ensure_ascii=False,indent=2))
