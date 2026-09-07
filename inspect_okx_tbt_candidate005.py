#!/usr/bin/env python3
import csv, gzip, io, json, requests
from pathlib import Path

API='https://www.okx.com/api/v5/public/market-data-history'
EVENTS=[
    {'name':'sweep1','t0':1771128171025,'qty':0.14347261,'binance_entry':69699.71,'capital':10000},
    {'name':'sweep2','t0':1771128192274,'qty':0.11469564,'binance_entry':69749.82,'capital':8000},
    {'name':'sweep3','t0':1771128275429,'qty':0.05726560,'binance_entry':69849.96,'capital':4000},
]
DELAYS=[0,50,100,250,500]
OUT=Path('output/candidate005_okx_tbt'); OUT.mkdir(parents=True,exist_ok=True)
params={
    'module':'6','instType':'SPOT','dateAggrType':'daily',
    'begin':'1771113600000','end':'1771200000000','instIdList':'BTC-USDT'
}

def book_vwap(row, qty):
    rem=qty; value=0.0; used=[]
    for i in range(1,51):
        ps=row.get(f'bid_{i}_px',''); qs=row.get(f'bid_{i}_qty','')
        if ps in ('',None) or qs in ('',None): continue
        p=float(ps); q=float(qs)
        if q<=0: continue
        take=min(rem,q)
        if take>0:
            value += take*p; used.append({'level':i,'price':p,'available_qty':q,'used_qty':take})
            rem -= take
        if rem <= 1e-12: break
    if rem > 1e-10:
        return {'covered':False,'vwap':None,'covered_qty':qty-rem,'levels_used':used}
    return {'covered':True,'vwap':value/qty,'covered_qty':qty,'levels_used':used}

r=requests.get(API,params=params,timeout=30); r.raise_for_status(); js=r.json()
groups=js['data'][0]['details'][0]['groupDetails']
item=next(x for x in groups if x['filename']=='BTC-USDT.OK.csv.gz' and x['dateTs']=='1771113600000')
print('signed_url obtained', item['sizeMB'],'MB')
resp=requests.get(item['url'],stream=True,timeout=90); resp.raise_for_status(); resp.raw.decode_content=False

arrivals=[]
for e in EVENTS:
    for d in DELAYS:
        arrivals.append((e['t0']+d,e,d))
arrivals.sort(key=lambda x:x[0])
results=[]; idx=0; prev_row=None; prev_ts=None; count=0
first_seen=None; last_seen=None
max_target=arrivals[-1][0]+2000

with gzip.GzipFile(fileobj=resp.raw,mode='rb') as gz:
    txt=io.TextIOWrapper(gz,encoding='utf-8',errors='strict',newline='')
    rd=csv.DictReader(txt)
    for row in rd:
        count += 1
        ts=int(row['exchTimeMs'])
        if first_seen is None: first_seen=ts
        last_seen=ts
        while idx < len(arrivals) and arrivals[idx][0] < ts:
            arr,e,d=arrivals[idx]
            if prev_row is None:
                rec={'event':e['name'],'t0_ms':e['t0'],'delay_ms':d,'arrival_ms':arr,'book_exchTimeMs':None,'book_age_ms':None,'covered':False,'reason':'no_prior_book'}
            else:
                ex=book_vwap(prev_row,e['qty']); v=ex['vwap']
                edge=None if v is None else (v/e['binance_entry']-1)*10000
                rec={'event':e['name'],'t0_ms':e['t0'],'delay_ms':d,'arrival_ms':arr,
                     'qty_btc':e['qty'],'capital_usd':e['capital'],'binance_entry':e['binance_entry'],
                     'book_exchTimeMs':prev_ts,'book_age_ms':arr-prev_ts,
                     'best_bid':float(prev_row['bid_1_px']),'best_bid_qty':float(prev_row['bid_1_qty']),
                     'covered':ex['covered'],'sell_vwap_usdt':v,'gross_edge_bp':edge,
                     'gross_pnl':None if edge is None else e['capital']*edge/10000,
                     'residual_after_20bp_bp':None if edge is None else edge-20,
                     'levels_used':ex['levels_used']}
            results.append(rec); idx += 1
        prev_row=row; prev_ts=ts
        if idx>=len(arrivals) and ts>max_target: break

while idx < len(arrivals):
    arr,e,d=arrivals[idx]
    ex=book_vwap(prev_row,e['qty']) if prev_row else {'covered':False,'vwap':None,'levels_used':[]}
    v=ex['vwap']; edge=None if v is None else (v/e['binance_entry']-1)*10000
    results.append({'event':e['name'],'t0_ms':e['t0'],'delay_ms':d,'arrival_ms':arr,
                    'qty_btc':e['qty'],'capital_usd':e['capital'],'binance_entry':e['binance_entry'],
                    'book_exchTimeMs':prev_ts,'book_age_ms':None if prev_ts is None else arr-prev_ts,
                    'best_bid':None if prev_row is None else float(prev_row['bid_1_px']),
                    'best_bid_qty':None if prev_row is None else float(prev_row['bid_1_qty']),
                    'covered':ex['covered'],'sell_vwap_usdt':v,'gross_edge_bp':edge,
                    'gross_pnl':None if edge is None else e['capital']*edge/10000,
                    'residual_after_20bp_bp':None if edge is None else edge-20,
                    'levels_used':ex['levels_used']}); idx+=1

report={'source':'OKX module=6 historical 50-level order book','filename':item['filename'],'sizeMB':item['sizeMB'],
        'rows_streamed':count,'first_exchTimeMs':first_seen,'last_exchTimeMs':last_seen,'events':EVENTS,'delays_ms':DELAYS,'results':results}
(OUT/'vwap_matrix.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
with open(OUT/'vwap_matrix.csv','w',newline='',encoding='utf-8-sig') as f:
    fields=['event','t0_ms','delay_ms','arrival_ms','qty_btc','capital_usd','binance_entry','book_exchTimeMs','book_age_ms','best_bid','best_bid_qty','covered','sell_vwap_usdt','gross_edge_bp','gross_pnl','residual_after_20bp_bp']
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
    for x in results: w.writerow({k:x.get(k) for k in fields})
print(json.dumps({'rows_streamed':count,'results':[{k:x.get(k) for k in ['event','delay_ms','book_exchTimeMs','book_age_ms','best_bid','best_bid_qty','covered','sell_vwap_usdt','gross_edge_bp','residual_after_20bp_bp']} for x in results]},ensure_ascii=False,indent=2))
