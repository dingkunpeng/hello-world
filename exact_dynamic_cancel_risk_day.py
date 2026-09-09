#!/usr/bin/env python3
import argparse,csv,gzip,io,json,math,requests,zipfile
from collections import deque
from pathlib import Path
from datetime import datetime,timezone,timedelta

LADDER_BP=[40,60,80,100,120]
LAYER_USD=2000.0
CANCEL_LATENCIES_MS=[0,10,20,50,100]
HEDGE_DELAY_MS=100
COST_BP=32.5
TICK=0.01
TRIGGER_CSV=Path('output/continuous_exante/minute_triggers.csv')
OUTROOT=Path('output/dynamic_cancel_risk_exact')
OKX_API='https://www.okx.com/api/v5/public/market-data-history'


def norm_ms(v):
    x=int(float(v)); return x//1000 if x>10**14 else x

def iso(ms): return datetime.fromtimestamp(ms/1000,tz=timezone.utc).isoformat()
def floor_tick(x): return math.floor((x+1e-12)/TICK)*TICK

def all_trigger_days():
    with TRIGGER_CSV.open(encoding='utf-8-sig') as f:
        return sorted({r['minute_utc'][:10] for r in csv.DictReader(f)})

def load_minutes(date):
    out=[]
    with TRIGGER_CSV.open(encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if r['minute_utc'].startswith(date):
                out.append({'minute_utc':r['minute_utc'],'start_ms':int(r['minute_ms'])})
    return out

def load_binance_trades(date,mins):
    targets=[(m['start_ms'],m['start_ms']+60000) for m in mins]
    url=f'https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-{date}.zip'
    r=requests.get(url,timeout=180); r.raise_for_status()
    z=zipfile.ZipFile(io.BytesIO(r.content)); name=next(n for n in z.namelist() if n.endswith('.csv'))
    rd=csv.reader(io.TextIOWrapper(z.open(name),encoding='utf-8-sig',newline=''))
    out=[]
    for x in rd:
        if not x or not x[0].replace('.','',1).isdigit(): continue
        t=norm_ms(x[5])
        if any(a<=t<b for a,b in targets):
            if str(x[6]).lower()=='true':
                out.append({'a':int(x[0]),'p':float(x[1]),'q':float(x[2]),'T':t})
    out.sort(key=lambda x:(x['T'],x['a']))
    return out,url

def okx_item(date):
    d=datetime.strptime(date,'%Y-%m-%d').replace(tzinfo=timezone.utc)
    begin=int(d.timestamp()*1000); end=int((d+timedelta(days=1)).timestamp()*1000)
    p={'module':'6','instType':'SPOT','dateAggrType':'daily','begin':str(begin),'end':str(end),'instIdList':'BTC-USDT'}
    r=requests.get(OKX_API,params=p,timeout=45); r.raise_for_status(); js=r.json()
    groups=js['data'][0]['details'][0]['groupDetails']
    cand=[x for x in groups if x.get('filename')=='BTC-USDT.OK.csv.gz']
    exact=[x for x in cand if x.get('dateTs')==str(begin)]
    if not (exact or cand): raise RuntimeError(f'no OKX file {date}')
    return (exact or cand)[0]

def book_vwap(row,qty):
    if not row or qty<=0:return None,0.0
    rem=qty; val=0.0
    for i in range(1,51):
        ps=row.get(f'bid_{i}_px'); qs=row.get(f'bid_{i}_qty')
        if not ps or not qs: continue
        p=float(ps); q=float(qs); take=min(rem,q); val+=take*p; rem-=take
        if rem<=1e-12: break
    return ((val/qty) if rem<=1e-10 else None),qty-rem

def minute_start(t): return (t//60000)*60000

def latest_book_le(history,target_ms):
    for ts,row in reversed(history):
        if ts<=target_ms:return ts,row
    return (None,None)

def replay_day(date):
    mins=load_minutes(date)
    if not mins:return None
    targets={m['start_ms'] for m in mins}
    trades,burl=load_binance_trades(date,mins)
    item=okx_item(date)
    print('date',date,'trigger_minutes',len(mins),'seller_trades',len(trades),'okxMB',item.get('sizeMB'))

    armed={L:{(m,off):True for m in targets for off in LADDER_BP} for L in CANCEL_LATENCIES_MS}
    fills={L:[] for L in CANCEL_LATENCIES_MS}
    pending={L:{} for L in CANCEL_LATENCIES_MS}
    hedges={L:[] for L in CANCEL_LATENCIES_MS}
    ti=0; history=deque(); prev=None; prev_ts=None
    last_needed=max(m+60000 for m in targets)+HEDGE_DELAY_MS+1000

    def schedule_trade(tr,current_book,current_ts):
        m=minute_start(tr['T'])
        if m not in targets or current_book is None:return
        ideal_fair=float(current_book['bid_1_px'])
        for L in CANCEL_LATENCIES_MS:
            anchor_target=tr['T']-L
            ats,abook=latest_book_le(history,anchor_target)
            if abook is None:continue
            lagged_fair=float(abook['bid_1_px'])
            for off in LADDER_BP:
                key=(m,off)
                if not armed[L].get(key):continue
                limit=floor_tick(lagged_fair*(1-off/10000))
                if tr['p'] < limit:
                    armed[L][key]=False
                    ideal_limit=floor_tick(ideal_fair*(1-off/10000))
                    qty=LAYER_USD/limit
                    rec={'date':date,'minute_utc':datetime.fromtimestamp(m/1000,tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                         'cancel_latency_ms':L,'tier_bp':off,'fill_t_ms':tr['T'],'fill_t_utc':iso(tr['T']),
                         'binance_trade_px':tr['p'],'binance_trade_qty':tr['q'],
                         'lagged_anchor_book_ms':ats,'lagged_anchor_age_ms':tr['T']-ats,
                         'lagged_okx_best_bid':lagged_fair,'current_okx_book_ms':current_ts,'current_okx_best_bid':ideal_fair,
                         'entry_limit_px':limit,'ideal_zero_latency_limit_px':ideal_limit,
                         'cancel_race_only': not (tr['p'] < ideal_limit),
                         'qty_btc':qty,'capital_usd':LAYER_USD,'hedge_arrival_ms':tr['T']+HEDGE_DELAY_MS}
                    fills[L].append(rec); pending[L].setdefault(rec['hedge_arrival_ms'],[]).append(rec)

    def settle_due(now_ts,book,book_ts,strict_less=True):
        if book is None:return
        for L in CANCEL_LATENCIES_MS:
            ks=sorted(k for k in pending[L] if (k<now_ts if strict_less else k<=now_ts))
            for arr in ks:
                group=pending[L].pop(arr); tq=sum(x['qty_btc'] for x in group); cap=sum(x['capital_usd'] for x in group)
                sell,cov=book_vwap(book,tq)
                entry_value=sum(x['entry_limit_px']*x['qty_btc'] for x in group)
                gross=((sell*tq/entry_value)-1)*10000 if sell else None
                net=gross-COST_BP if gross is not None else None
                pnl=cap*net/10000 if net is not None else None
                hedges[L].append({'cancel_latency_ms':L,'arrival_ms':arr,'arrival_utc':iso(arr),'okx_book_ms':book_ts,
                                  'book_age_ms':arr-book_ts if book_ts is not None else None,'fills':len(group),
                                  'cancel_race_only_fills':sum(1 for x in group if x['cancel_race_only']),
                                  'tiers_bp':[x['tier_bp'] for x in group],'qty_btc':tq,'capital_usd':cap,
                                  'sell_vwap_usdt':sell,'covered_qty':cov,'gross_edge_bp':gross,
                                  'net_after_32_5bp':net,'net_pnl_usd':pnl})

    resp=requests.get(item['url'],stream=True,timeout=240); resp.raise_for_status(); resp.raw.decode_content=False
    with gzip.GzipFile(fileobj=resp.raw,mode='rb') as gz:
        rd=csv.DictReader(io.TextIOWrapper(gz,encoding='utf-8',errors='strict',newline=''))
        for row in rd:
            ts=int(row['exchTimeMs'])
            if prev is not None:
                settle_due(ts,prev,prev_ts,True)
                while ti<len(trades) and trades[ti]['T']<ts:
                    schedule_trade(trades[ti],prev,prev_ts); ti+=1
                    settle_due(ts,prev,prev_ts,True)
            prev=row; prev_ts=ts; history.append((ts,row))
            # Keep at least one book older than a 5s rolling window.
            cutoff=ts-5000
            while len(history)>=2 and history[1][0] <= cutoff: history.popleft()
            while ti<len(trades) and trades[ti]['T']==ts:
                schedule_trade(trades[ti],prev,prev_ts); ti+=1
            settle_due(ts,prev,prev_ts,False)
            if ts>last_needed and ti>=len(trades) and all(not pending[L] for L in CANCEL_LATENCIES_MS):break
    while ti<len(trades):
        schedule_trade(trades[ti],prev,prev_ts); ti+=1
    settle_due(10**18,prev,prev_ts,False)

    by_latency={}
    for L in CANCEL_LATENCIES_MS:
        hs=hedges[L]; fs=fills[L]
        by_latency[str(L)]={
            'fill_count':len(fs),'hedge_groups':len(hs),'capital_usd':sum(x['capital_usd'] for x in hs),
            'net_pnl_usd':sum((x['net_pnl_usd'] or 0) for x in hs),
            'positive_hedges':sum(1 for x in hs if (x['net_pnl_usd'] or 0)>0),
            'negative_hedges':sum(1 for x in hs if (x['net_pnl_usd'] or 0)<0),
            'uncovered_hedges':sum(1 for x in hs if x['sell_vwap_usdt'] is None),
            'cancel_race_only_fills':sum(1 for x in fs if x['cancel_race_only']),
            'ideal_zero_latency_fills':sum(1 for x in fs if not x['cancel_race_only']),
            'max_lagged_anchor_age_ms':max([x['lagged_anchor_age_ms'] for x in fs],default=None),
            'max_hedge_book_age_ms':max([x['book_age_ms'] for x in hs if x['book_age_ms'] is not None],default=None)
        }
    summary={'date':date,
             'experiment':'CAUSAL STALE-QUOTE RESCUE TEST on the exact same static-risk minutes that produced the strict ex-ante baseline. Dynamic order price is latest OKX best bid observed at t-cancel_latency, approximating a continuously repriced peg with 0/10/20/50/100ms cancel/replace lag. One fill per tier per minute. +100ms OKX 50-level hedge. 32.5bp all-in cost.',
             'important_scope':'This isolates whether real-time repricing rescues the 564 known static-risk minutes. It is NOT yet the exhaustive full-year dynamic-strategy EV because dynamic fills outside this static-risk universe are not included.',
             'trigger_minutes':[m['minute_utc'] for m in mins],'binance_archive':burl,
             'okx_file':{'filename':item.get('filename'),'sizeMB':item.get('sizeMB')},
             'by_latency':by_latency}
    out=OUTROOT/date; out.mkdir(parents=True,exist_ok=True)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'date':date,'by_latency':by_latency},ensure_ascii=False))
    return summary

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date'); ap.add_argument('--shard',type=int); ap.add_argument('--shards',type=int); a=ap.parse_args()
    if a.date: replay_day(a.date); return
    days=all_trigger_days()
    if a.shard is None or a.shards is None: raise SystemExit('use --date or --shard/--shards')
    chosen=[d for i,d in enumerate(days) if i%a.shards==a.shard]
    print('shard',a.shard,'of',a.shards,'days',len(chosen),chosen)
    errors=[]
    for d in chosen:
        try: replay_day(d)
        except Exception as e:
            print('ERROR',d,repr(e)); errors.append({'date':d,'error':repr(e)})
    OUTROOT.mkdir(parents=True,exist_ok=True)
    (OUTROOT/f'shard_{a.shard}_errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding='utf-8')
    if errors: raise RuntimeError(f'{len(errors)} day errors')
if __name__=='__main__': main()
