#!/usr/bin/env python3
import csv, json, math, time
from datetime import datetime, timezone
from pathlib import Path
import requests

EVENTS=[
    {'name':'sweep1','t0_ms':1771128171025,'qty':0.14347261,'binance_entry':69699.71,'capital':10000},
    {'name':'sweep2','t0_ms':1771128192274,'qty':0.11469564,'binance_entry':69749.82,'capital':8000},
    {'name':'sweep3','t0_ms':1771128275429,'qty':0.05726560,'binance_entry':69849.96,'capital':4000},
]
DELAYS=[0,50,100,250,500]
OUT=Path('output/candidate005_crossvenue'); OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({'User-Agent':'candidate005-research/1.0'})

def iso_ms(ms):
    return datetime.fromtimestamp(ms/1000,tz=timezone.utc).isoformat()

def edge(price, entry):
    return (price/entry-1)*10000

def fill_from_trade_tape(trades, arrival_ms, qty, side_pred):
    rem=qty; value=0.0; used=[]; first=None; last=None
    for tr in trades:
        if tr['ts_ms'] < arrival_ms or not side_pred(tr):
            continue
        take=min(rem,tr['qty'])
        if take<=0: continue
        if first is None: first=tr['ts_ms']
        last=tr['ts_ms']; value += take*tr['price']; rem -= take
        used.append({'ts_ms':tr['ts_ms'],'price':tr['price'],'available_qty':tr['qty'],'used_qty':take,'side':tr.get('side')})
        if rem<=1e-12: break
    return {'covered':rem<=1e-10,'vwap':None if rem>1e-10 else value/qty,
            'covered_qty':qty-rem,'first_trade_ms':first,'cover_ms':last,'used':used}

# ---------- Kraken historical time & sales ----------
def fetch_kraken():
    start_sec=int(min(e['t0_ms'] for e in EVENTS)/1000)-3
    end_sec=max(e['t0_ms'] for e in EVENTS)/1000+5
    since=str(start_sec-1)+'999999999'
    alltr=[]; loops=0
    while True:
        r=S.get('https://api.kraken.com/0/public/Trades',params={'pair':'XBTUSD','since':since},timeout=30)
        r.raise_for_status(); js=r.json()
        if js.get('error'): raise RuntimeError(js['error'])
        key=next(k for k in js['result'] if k!='last')
        batch=js['result'][key]
        for x in batch:
            ts=float(x[2]);
            if ts <= end_sec:
                alltr.append({'price':float(x[0]),'qty':float(x[1]),'ts_ms':int(round(ts*1000)),'side':x[3],'ordertype':x[4]})
        loops+=1
        if not batch or float(batch[-1][2])>end_sec or loops>50: break
        nxt=js['result']['last']
        if nxt==since: break
        since=nxt; time.sleep(0.15)
    alltr.sort(key=lambda x:x['ts_ms'])
    # Kraken public time-and-sales uses b/s side flag. For this research, s is sell-side trade tape.
    return alltr

# ---------- Coinbase Exchange historical product trades ----------
CB='https://api.exchange.coinbase.com/products/BTC-USD/trades'

def cb_get(params=None):
    for i in range(7):
        r=S.get(CB,params=params or {},timeout=30)
        if r.status_code==429:
            time.sleep(1.0+0.5*i); continue
        r.raise_for_status(); return r.json()
    raise RuntimeError('Coinbase repeated 429')

def parse_iso_ms(s):
    return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()*1000)

def cb_probe_id(cursor_id):
    # Default ordering is newest -> older. after sets the end cursor; first result is near cursor on the older side.
    data=cb_get({'after':str(int(cursor_id)+1),'limit':1})
    if not data: return None
    x=data[0]
    return {'id':int(x['trade_id']),'ts_ms':parse_iso_ms(x['time'])}

def cb_find_id_for_time(target_ms):
    latest=cb_get({'limit':1})[0]
    hi=int(latest['trade_id']); lo=1
    # Binary search trade id because trade_id is monotonic with time.
    for _ in range(35):
        if lo>=hi: break
        mid=(lo+hi)//2
        p=cb_probe_id(mid)
        if p is None:
            hi=mid; continue
        if p['ts_ms'] < target_ms:
            lo=mid+1
        else:
            hi=mid
        time.sleep(0.03)
    return lo

def fetch_coinbase():
    start_ms=min(e['t0_ms'] for e in EVENTS)-3000
    end_ms=max(e['t0_ms'] for e in EVENTS)+5000
    start_id=cb_find_id_for_time(start_ms)
    end_id=cb_find_id_for_time(end_ms)
    print('coinbase id range',start_id,end_id)
    cursor=end_id+1500; floor=max(1,start_id-1500); rows=[]; seen=set(); loops=0
    while cursor>floor and loops<100:
        data=cb_get({'after':str(cursor),'limit':1000})
        if not data: break
        ids=[]
        for x in data:
            tid=int(x['trade_id']); ids.append(tid)
            if tid in seen: continue
            seen.add(tid)
            ts=parse_iso_ms(x['time'])
            if start_ms-2000 <= ts <= end_ms+2000:
                rows.append({'id':tid,'price':float(x['price']),'qty':float(x['size']),'ts_ms':ts,'side':x['side']})
        new_cursor=min(ids)
        if new_cursor>=cursor: break
        cursor=new_cursor; loops+=1; time.sleep(0.12)
        if cursor<=floor: break
    rows.sort(key=lambda x:(x['ts_ms'],x['id']))
    return rows, {'start_id':start_id,'end_id':end_id,'loops':loops}

kraken=fetch_kraken()
print('kraken trades',len(kraken), iso_ms(kraken[0]['ts_ms']) if kraken else None, iso_ms(kraken[-1]['ts_ms']) if kraken else None)
coinbase, cbmeta=fetch_coinbase()
print('coinbase trades',len(coinbase), iso_ms(coinbase[0]['ts_ms']) if coinbase else None, iso_ms(coinbase[-1]['ts_ms']) if coinbase else None)

results=[]
for venue,trades,pred,evidence in [
    ('Kraken',kraken,lambda tr:tr.get('side')=='s','Level B trade-tape sell executions'),
    ('Coinbase',coinbase,lambda tr:tr.get('side')=='buy','Level B maker-buy removed / aggressive sell into bid'),
]:
    for e in EVENTS:
        for d in DELAYS:
            arr=e['t0_ms']+d
            f=fill_from_trade_tape(trades,arr,e['qty'],pred)
            v=f['vwap']; ed=None if v is None else edge(v,e['binance_entry'])
            results.append({
                'venue':venue,'event':e['name'],'t0_ms':e['t0_ms'],'delay_ms':d,'arrival_ms':arr,
                'qty_btc':e['qty'],'capital_usd':e['capital'],'binance_entry':e['binance_entry'],
                'covered':f['covered'],'sell_vwap_native':v,'native_quote':'USD','gross_edge_bp_raw_usd_vs_usdt':ed,
                'gross_pnl_raw':None if ed is None else e['capital']*ed/10000,
                'residual_after_20bp_raw':None if ed is None else ed-20,
                'first_qualifying_trade_ms':f['first_trade_ms'],'cover_ms':f['cover_ms'],
                'time_to_first_trade_ms':None if f['first_trade_ms'] is None else f['first_trade_ms']-arr,
                'time_to_cover_ms':None if f['cover_ms'] is None else f['cover_ms']-arr,
                'evidence':evidence,'used_trade_count':len(f['used'])
            })

fields=list(results[0].keys())
with open(OUT/'kraken_coinbase_matrix.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(results)
(OUT/'kraken_coinbase_matrix.json').write_text(json.dumps({'events':EVENTS,'delays':DELAYS,'coinbase_meta':cbmeta,'kraken_trade_count':len(kraken),'coinbase_trade_count':len(coinbase),'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
