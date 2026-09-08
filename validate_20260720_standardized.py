#!/usr/bin/env python3
import csv,gzip,io,json,zipfile,requests
from datetime import datetime,timezone,timedelta
from pathlib import Path

DATE='2026-07-20'; MINUTE='2026-07-20 08:37:00'
LADDER_OFFSETS_BP=[40,60,80,100,120]; LAYER_USD=2000; DELAYS=[0,50,100,250,500]
COST_BP=32.5
OUT=Path('output/annual_ev/2026-07-20'); OUT.mkdir(parents=True,exist_ok=True)
BINANCE_URL=f'https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-{DATE}.zip'
OKX_API='https://www.okx.com/api/v5/public/market-data-history'

def ms(s): return int(datetime.strptime(s,'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()*1000)
def norm_ts(v):
    x=int(float(v)); return x//1000 if x>10**14 else x

def load_binance_sweeps():
    r=requests.get(BINANCE_URL,timeout=90); r.raise_for_status()
    z=zipfile.ZipFile(io.BytesIO(r.content)); name=next(n for n in z.namelist() if n.endswith('.csv'))
    rows=list(csv.reader(z.read(name).decode('utf-8-sig').strip().splitlines()))
    if rows and not rows[0][0].replace('.','',1).isdigit(): rows=rows[1:]
    st=ms(MINUTE); en=st+60_000
    tr=[]
    for x in rows:
        t=norm_ts(x[5])
        if st<=t<en:
            tr.append({'a':int(x[0]),'p':float(x[1]),'q':float(x[2]),'T':t,'m':str(x[6]).lower()=='true'})
    low=min(x['p'] for x in tr)
    lows=sorted([x for x in tr if x['p']==low], key=lambda x:x['T'])
    groups=[]; g=[]; last=None
    for x in lows:
        if last is None or x['T']-last<=1000:
            g.append(x)
        else:
            groups.append(g); g=[x]
        last=x['T']
    if g: groups.append(g)
    sweeps=[]
    for i,g in enumerate(groups,1):
        t0=min(x['T'] for x in g)
        sweeps.append({'name':f'sweep{i}','t0':t0,'t0_utc':datetime.fromtimestamp(t0/1000,tz=timezone.utc).isoformat(),'binance_low':low,
                       'low_qty':sum(x['q'] for x in g),'seller_aggressor_low_qty':sum(x['q'] for x in g if x['m']),'low_trade_count':len(g)})
    return {'minute_trade_count':len(tr),'minute_low':low,'low_trade_count':len(lows),'sweeps':sweeps}

def okx_file():
    begin=ms(DATE+' 00:00:00'); end=begin+86400000
    p={'module':'6','instType':'SPOT','dateAggrType':'daily','begin':str(begin),'end':str(end),'instIdList':'BTC-USDT'}
    r=requests.get(OKX_API,params=p,timeout=30); r.raise_for_status(); js=r.json()
    groups=js['data'][0]['details'][0]['groupDetails']
    candidates=[x for x in groups if x.get('filename')=='BTC-USDT.OK.csv.gz']
    exact=[x for x in candidates if x.get('dateTs')==str(begin)]
    return (exact or candidates)[0]

def book_vwap(row,qty):
    if qty<=0 or row is None: return None,0
    rem=qty; val=0.0
    for i in range(1,51):
        ps=row.get(f'bid_{i}_px'); qs=row.get(f'bid_{i}_qty')
        if not ps or not qs: continue
        p=float(ps); q=float(qs)
        take=min(rem,q)
        if take>0: val+=take*p; rem-=take
        if rem<=1e-12: break
    return ((val/qty) if rem<=1e-10 else None), qty-rem

b=load_binance_sweeps(); sweeps=b['sweeps']; print('BINANCE',json.dumps(b,ensure_ascii=False,indent=2))
item=okx_file(); print('OKX FILE',item['sizeMB'],'MB')
arrivals=[]
for s in sweeps:
    for d in DELAYS: arrivals.append((s['t0']+d,s['name'],d))
arrivals.sort(); snaps={}; prev=None; prev_ts=None; idx=0
resp=requests.get(item['url'],stream=True,timeout=120); resp.raise_for_status(); resp.raw.decode_content=False
with gzip.GzipFile(fileobj=resp.raw,mode='rb') as gz:
    rd=csv.DictReader(io.TextIOWrapper(gz,encoding='utf-8',errors='strict',newline=''))
    for row in rd:
        ts=int(row['exchTimeMs'])
        while idx<len(arrivals) and arrivals[idx][0] < ts:
            arr,name,d=arrivals[idx]; snaps[(name,d)]=(prev_ts,prev.copy() if prev else None); idx+=1
        prev=row; prev_ts=ts
        if idx>=len(arrivals) and ts>max(x[0] for x in arrivals)+50: break
while idx<len(arrivals):
    arr,name,d=arrivals[idx]; snaps[(name,d)]=(prev_ts,prev.copy() if prev else None); idx+=1

results=[]
for s in sweeps:
    pre_ts,pre=snaps[(s['name'],0)]
    fair=float(pre['bid_1_px'])
    ladder=[]
    for off in LADDER_OFFSETS_BP:
        px=fair*(1-off/10000)
        ladder.append({'offset_bp':off,'limit_px':px,'filled_price_through':s['binance_low']<px})
    filled=[x for x in ladder if x['filled_price_through']]
    capital=LAYER_USD*len(filled); qty=sum(LAYER_USD/x['limit_px'] for x in filled); entry=capital/qty if qty else None
    for d in DELAYS:
        bts,row=snaps[(s['name'],d)]
        vwap,covered=book_vwap(row,qty)
        gross=(vwap/entry-1)*10000 if vwap and entry else None
        net=gross-COST_BP if gross is not None else None
        results.append({'event':DATE,'sweep':s['name'],'t0_utc':s['t0_utc'],'t0_ms':s['t0'],'binance_low':s['binance_low'],
                        'low_qty':s['low_qty'],'seller_aggressor_low_qty':s['seller_aggressor_low_qty'],'okx_pre_book_ms':pre_ts,'okx_pre_best_bid':fair,
                        'filled_layers':len(filled),'capital_usd':capital,'qty_btc':qty,'binance_ladder_entry':entry,'delay_ms':d,
                        'okx_book_ms':bts,'book_age_ms':(s['t0']+d-bts) if bts else None,'sell_vwap_usdt':vwap,'covered_qty':covered,
                        'gross_edge_bp':gross,'net_after_32_5bp':net,'net_pnl_usd':capital*net/10000 if net is not None else None,
                        'ladder_json':json.dumps(ladder,separators=(',',':'))})

fields=list(results[0].keys())
with (OUT/'standardized_matrix.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(results)
summary={'binance_detection':b,'okx_file':{'filename':item['filename'],'sizeMB':item['sizeMB']},'cost_bp':COST_BP,'delays':DELAYS,
         'results_100ms':[r for r in results if r['delay_ms']==100]}
summary['cluster_net_pnl_100ms']=sum((r['net_pnl_usd'] or 0) for r in results if r['delay_ms']==100)
summary['cluster_binance_capital_required_no_rebalance']=sum(r['capital_usd'] for r in results if r['delay_ms']==100)
summary['cluster_total_prepositioned_capital_estimate']=2*summary['cluster_binance_capital_required_no_rebalance']
(OUT/'standardized_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
