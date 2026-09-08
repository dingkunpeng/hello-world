#!/usr/bin/env python3
import csv,gzip,io,json,zipfile,requests
from datetime import datetime,timezone,timedelta
from pathlib import Path

EVENTS=[
 {'date':'2026-06-09','minute':'2026-06-09 00:28:00'},
 {'date':'2026-02-14','minute':'2026-02-14 13:18:00'},
 {'date':'2026-02-20','minute':'2026-02-20 13:39:00'},
 {'date':'2026-05-01','minute':'2026-05-01 04:29:00'},
]
LADDER_OFFSETS_BP=[40,60,80,100,120]; LAYER_USD=2000; DELAYS=[0,50,100,250,500]; COST_BP=32.5
OUT=Path('output/annual_ev/remaining_l2'); OUT.mkdir(parents=True,exist_ok=True)
OKX_API='https://www.okx.com/api/v5/public/market-data-history'

def ms(s): return int(datetime.strptime(s,'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()*1000)
def norm_ts(v):
    x=int(float(v)); return x//1000 if x>10**14 else x

def binance_sweeps(date,minute):
    url=f'https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-{date}.zip'
    r=requests.get(url,timeout=120); r.raise_for_status(); z=zipfile.ZipFile(io.BytesIO(r.content)); name=next(n for n in z.namelist() if n.endswith('.csv'))
    rows=list(csv.reader(z.read(name).decode('utf-8-sig').strip().splitlines()))
    if rows and not rows[0][0].replace('.','',1).isdigit(): rows=rows[1:]
    st=ms(minute); en=st+60_000; tr=[]
    for x in rows:
        t=norm_ts(x[5])
        if st<=t<en: tr.append({'p':float(x[1]),'q':float(x[2]),'T':t,'m':str(x[6]).lower()=='true'})
    low=min(x['p'] for x in tr); lows=sorted([x for x in tr if x['p']==low],key=lambda x:x['T'])
    groups=[]; g=[]; last=None
    for x in lows:
        if last is None or x['T']-last<=5000: g.append(x)
        else: groups.append(g); g=[x]
        last=x['T']
    if g: groups.append(g)
    sweeps=[]
    for i,g in enumerate(groups,1):
        t0=min(x['T'] for x in g)
        sweeps.append({'name':f'{date}_sweep{i}','t0':t0,'t0_utc':datetime.fromtimestamp(t0/1000,tz=timezone.utc).isoformat(),
                       'binance_low':low,'low_qty':sum(x['q'] for x in g),'seller_aggressor_low_qty':sum(x['q'] for x in g if x['m']),'low_trade_count':len(g)})
    return {'date':date,'minute':minute,'minute_trade_count':len(tr),'minute_low':low,'low_trade_count':len(lows),'sweeps':sweeps}

def okx_item(date):
    begin=ms(date+' 00:00:00'); end=begin+86400000
    p={'module':'6','instType':'SPOT','dateAggrType':'daily','begin':str(begin),'end':str(end),'instIdList':'BTC-USDT'}
    r=requests.get(OKX_API,params=p,timeout=30); r.raise_for_status(); groups=r.json()['data'][0]['details'][0]['groupDetails']
    c=[x for x in groups if x.get('filename')=='BTC-USDT.OK.csv.gz']; exact=[x for x in c if x.get('dateTs')==str(begin)]
    return (exact or c)[0]

def vwap(row,qty):
    if not row or qty<=0: return None,0
    rem=qty; val=0.0
    for i in range(1,51):
        ps=row.get(f'bid_{i}_px'); qs=row.get(f'bid_{i}_qty')
        if not ps or not qs: continue
        p=float(ps); q=float(qs); take=min(rem,q)
        if take>0: val+=take*p; rem-=take
        if rem<=1e-12: break
    return (val/qty if rem<=1e-10 else None),qty-rem

all_results=[]; detection=[]
for ev in EVENTS:
    b=binance_sweeps(ev['date'],ev['minute']); detection.append(b); sweeps=b['sweeps']; print('BINANCE',json.dumps(b,ensure_ascii=False))
    item=okx_item(ev['date']); print('OKX',ev['date'],item['sizeMB'],'MB')
    arrivals=[]
    for s in sweeps:
        for d in DELAYS: arrivals.append((s['t0']+d,s['name'],d))
    arrivals.sort(); snaps={}; prev=None; prev_ts=None; idx=0
    resp=requests.get(item['url'],stream=True,timeout=180); resp.raise_for_status(); resp.raw.decode_content=False
    with gzip.GzipFile(fileobj=resp.raw,mode='rb') as gz:
        rd=csv.DictReader(io.TextIOWrapper(gz,encoding='utf-8',errors='strict',newline=''))
        for row in rd:
            ts=int(row['exchTimeMs'])
            while idx<len(arrivals) and arrivals[idx][0]<ts:
                arr,name,d=arrivals[idx]; snaps[(name,d)]=(prev_ts,prev.copy() if prev else None); idx+=1
            prev=row; prev_ts=ts
            if idx>=len(arrivals) and arrivals and ts>max(a[0] for a in arrivals)+50: break
    while idx<len(arrivals):
        arr,name,d=arrivals[idx]; snaps[(name,d)]=(prev_ts,prev.copy() if prev else None); idx+=1
    for s in sweeps:
        pre_ts,pre=snaps[(s['name'],0)]; fair=float(pre['bid_1_px'])
        ladder=[]
        for off in LADDER_OFFSETS_BP:
            px=fair*(1-off/10000); ladder.append({'offset_bp':off,'limit_px':px,'filled_price_through':s['binance_low']<px})
        filled=[x for x in ladder if x['filled_price_through']]; capital=LAYER_USD*len(filled); qty=sum(LAYER_USD/x['limit_px'] for x in filled); entry=capital/qty if qty else None
        for d in DELAYS:
            bts,row=snaps[(s['name'],d)]; sell,covered=vwap(row,qty); gross=(sell/entry-1)*10000 if sell and entry else None; net=gross-COST_BP if gross is not None else None
            all_results.append({'event':ev['date'],'minute':ev['minute'],'sweep':s['name'],'t0_utc':s['t0_utc'],'t0_ms':s['t0'],'binance_low':s['binance_low'],
                'low_qty':s['low_qty'],'seller_aggressor_low_qty':s['seller_aggressor_low_qty'],'okx_pre_book_ms':pre_ts,'okx_pre_best_bid':fair,
                'filled_layers':len(filled),'capital_usd':capital,'qty_btc':qty,'binance_ladder_entry':entry,'delay_ms':d,'okx_book_ms':bts,
                'book_age_ms':(s['t0']+d-bts) if bts else None,'sell_vwap_usdt':sell,'covered_qty':covered,'gross_edge_bp':gross,
                'net_after_32_5bp':net,'net_pnl_usd':capital*net/10000 if net is not None else None,'ladder_json':json.dumps(ladder,separators=(',',':'))})

fields=list(all_results[0].keys())
with (OUT/'remaining_l2_matrix.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(all_results)
by_event={}
for ev in EVENTS:
    rows=[r for r in all_results if r['event']==ev['date'] and r['delay_ms']==100]
    by_event[ev['date']]={'rows_100ms':rows,'net_pnl_100ms':sum((r['net_pnl_usd'] or 0) for r in rows),
        'binance_capital_no_rebalance':sum(r['capital_usd'] for r in rows),'total_prepositioned_capital_estimate':2*sum(r['capital_usd'] for r in rows)}
summary={'cost_bp':COST_BP,'detection':detection,'by_event':by_event,'total_net_pnl_100ms':sum(v['net_pnl_100ms'] for v in by_event.values())}
(OUT/'remaining_l2_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
