#!/usr/bin/env python3
import argparse,csv,gzip,heapq,io,json,math,requests,zipfile
from pathlib import Path
from datetime import datetime,timezone,timedelta

LADDER_BP=[40,60,80,100,120]
LAYER_USD=2000.0
HEDGE_DELAY_MS=100
COST_BP=32.5
TICK=0.01
TRIGGER_CSV=Path('output/continuous_dynamic/guaranteed_trigger_minutes.csv')
OKX_API='https://www.okx.com/api/v5/public/market-data-history'

def norm_ms(v):
    x=int(float(v)); return x//1000 if x>10**14 else x

def iso(ms): return datetime.fromtimestamp(ms/1000,tz=timezone.utc).isoformat()
def floor_tick(x): return math.floor((x+1e-12)/TICK)*TICK

def load_trigger_minutes(date):
    rows=[]
    with TRIGGER_CSV.open(encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if r['minute_utc'].startswith(date):
                rows.append({'minute_utc':r['minute_utc'],'start_ms':int(r['minute_ms'])})
    return rows

def load_binance_trades(date,mins):
    targets=[(m['start_ms'],m['start_ms']+60000) for m in mins]
    url=f'https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-{date}.zip'
    r=requests.get(url,timeout=120);r.raise_for_status()
    z=zipfile.ZipFile(io.BytesIO(r.content)); name=next(n for n in z.namelist() if n.endswith('.csv'))
    rd=csv.reader(io.TextIOWrapper(z.open(name),encoding='utf-8-sig',newline=''))
    out=[]
    for x in rd:
        if not x or not x[0].replace('.','',1).isdigit():continue
        t=norm_ms(x[5])
        if any(a<=t<b for a,b in targets):
            out.append({'a':int(x[0]),'p':float(x[1]),'q':float(x[2]),'T':t,'m':str(x[6]).lower()=='true'})
    out.sort(key=lambda x:(x['T'],x['a']))
    return out,url

def okx_item(date):
    d=datetime.strptime(date,'%Y-%m-%d').replace(tzinfo=timezone.utc)
    begin=int(d.timestamp()*1000);end=int((d+timedelta(days=1)).timestamp()*1000)
    p={'module':'6','instType':'SPOT','dateAggrType':'daily','begin':str(begin),'end':str(end),'instIdList':'BTC-USDT'}
    r=requests.get(OKX_API,params=p,timeout=30);r.raise_for_status();js=r.json()
    groups=js['data'][0]['details'][0]['groupDetails']
    cand=[x for x in groups if x.get('filename')=='BTC-USDT.OK.csv.gz']
    exact=[x for x in cand if x.get('dateTs')==str(begin)]
    if not (exact or cand):raise RuntimeError(f'no OKX file {date}')
    return (exact or cand)[0]

def book_vwap(row,qty):
    if not row or qty<=0:return None,0
    rem=qty;val=0.0
    for i in range(1,51):
        ps=row.get(f'bid_{i}_px');qs=row.get(f'bid_{i}_qty')
        if not ps or not qs:continue
        p=float(ps);q=float(qs);take=min(rem,q);val+=take*p;rem-=take
        if rem<=1e-12:break
    return ((val/qty) if rem<=1e-10 else None),qty-rem

def minute_start(t): return (t//60000)*60000

def main(date):
    mins=load_trigger_minutes(date)
    if not mins:raise RuntimeError(f'no trigger minutes for {date}')
    target_starts={m['start_ms'] for m in mins}
    trades,burl=load_binance_trades(date,mins)
    seller=[x for x in trades if x['m']]
    item=okx_item(date)
    print('date',date,'minutes',len(mins),'trades',len(trades),'seller',len(seller),'okxMB',item.get('sizeMB'))

    # Ideal continuously repriced peg: each tier may fill once per minute, no reload until next minute.
    armed={(m,off):True for m in target_starts for off in LADDER_BP}
    fills=[]
    pending={}  # arrival_ms -> list of fills
    hedge_groups=[]
    ti=0; prev=None; prev_ts=None
    last_needed=max(m+60000 for m in target_starts)+HEDGE_DELAY_MS+1000

    def schedule_fill(tr,book,book_ts):
        if book is None:return
        m=minute_start(tr['T'])
        if m not in target_starts:return
        fair=float(book['bid_1_px'])
        for off in LADDER_BP:
            key=(m,off)
            if not armed.get(key):continue
            limit=floor_tick(fair*(1-off/10000))
            if tr['p'] < limit:  # strict price-through by a seller aggressor
                armed[key]=False
                qty=LAYER_USD/limit
                rec={'date':date,'minute_utc':datetime.fromtimestamp(m/1000,tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                     'tier_bp':off,'fill_t_ms':tr['T'],'fill_t_utc':iso(tr['T']),'binance_trade_px':tr['p'],'binance_trade_qty':tr['q'],
                     'okx_anchor_book_ms':book_ts,'anchor_age_ms':tr['T']-book_ts,'okx_best_bid_anchor':fair,
                     'entry_limit_px':limit,'qty_btc':qty,'capital_usd':LAYER_USD,'hedge_arrival_ms':tr['T']+HEDGE_DELAY_MS}
                fills.append(rec);pending.setdefault(rec['hedge_arrival_ms'],[]).append(rec)

    def settle_due(now_ts,book,book_ts,strict_less=True):
        keys=sorted([k for k in pending if (k<now_ts if strict_less else k<=now_ts)])
        for arr in keys:
            group=pending.pop(arr)
            tq=sum(x['qty_btc'] for x in group);cap=sum(x['capital_usd'] for x in group)
            sell,cov=book_vwap(book,tq)
            entry_value=sum(x['entry_limit_px']*x['qty_btc'] for x in group)
            gross_bp=((sell*tq/entry_value)-1)*10000 if sell else None
            net_bp=gross_bp-COST_BP if gross_bp is not None else None
            pnl=cap*net_bp/10000 if net_bp is not None else None
            hedge_groups.append({'arrival_ms':arr,'arrival_utc':iso(arr),'okx_book_ms':book_ts,'book_age_ms':arr-book_ts if book_ts else None,
                                 'fills':len(group),'tiers_bp':[x['tier_bp'] for x in group],'qty_btc':tq,'capital_usd':cap,
                                 'sell_vwap_usdt':sell,'covered_qty':cov,'gross_edge_bp':gross_bp,'net_after_32_5bp':net_bp,'net_pnl_usd':pnl,
                                 'fill_refs':[{'minute_utc':x['minute_utc'],'tier_bp':x['tier_bp'],'fill_t_ms':x['fill_t_ms'],'entry_limit_px':x['entry_limit_px']} for x in group]})

    resp=requests.get(item['url'],stream=True,timeout=180);resp.raise_for_status();resp.raw.decode_content=False
    with gzip.GzipFile(fileobj=resp.raw,mode='rb') as gz:
        rd=csv.DictReader(io.TextIOWrapper(gz,encoding='utf-8',errors='strict',newline=''))
        for row in rd:
            ts=int(row['exchTimeMs'])
            # Hedges/trades strictly before this book update use the previous known book.
            if prev is not None:
                settle_due(ts,prev,prev_ts,strict_less=True)
                while ti<len(seller) and seller[ti]['T']<ts:
                    schedule_fill(seller[ti],prev,prev_ts);ti+=1
                    settle_due(ts,prev,prev_ts,strict_less=True)
            prev=row;prev_ts=ts
            # Trades at the exact same timestamp may use this book update (optimistic tie convention, explicitly recorded).
            while ti<len(seller) and seller[ti]['T']==ts:
                schedule_fill(seller[ti],prev,prev_ts);ti+=1
            settle_due(ts,prev,prev_ts,strict_less=False)
            if ts>last_needed and ti>=len(seller) and not pending:break
    # Finish any remaining trades with last book, then hedges if possible.
    while ti<len(seller):schedule_fill(seller[ti],prev,prev_ts);ti+=1
    settle_due(10**18,prev,prev_ts,strict_less=False)

    # QA: no tier fills more than once per minute; anchor/hedge books should be reasonably fresh.
    keys=[(x['minute_utc'],x['tier_bp']) for x in fills]
    duplicates=len(keys)-len(set(keys))
    stale_anchor=max([x['anchor_age_ms'] for x in fills],default=None)
    stale_hedge=max([x['book_age_ms'] for x in hedge_groups if x['book_age_ms'] is not None],default=None)
    total_pnl=sum(x['net_pnl_usd'] or 0 for x in hedge_groups)
    total_cap=sum(x['capital_usd'] for x in hedge_groups)
    summary={'date':date,'policy':'ideal continuous peg to latest OKX bid; five tiers -40/-60/-80/-100/-120bp; $2k each; strict seller-aggressor price-through; each tier max once per minute; +100ms OKX hedge; 32.5bp all-in cost',
             'trigger_minutes_screened':[m['minute_utc'] for m in mins],'binance_archive':burl,'okx_file':{'filename':item.get('filename'),'sizeMB':item.get('sizeMB')},
             'seller_aggressor_trades_in_minutes':len(seller),'fills':fills,'hedge_groups':hedge_groups,
             'fill_count':len(fills),'hedge_group_count':len(hedge_groups),'total_capital_traded_usd':total_cap,'total_net_pnl_usd':total_pnl,
             'qa':{'duplicate_minute_tier_fills':duplicates,'max_anchor_book_age_ms':stale_anchor,'max_hedge_book_age_ms':stale_hedge,
                   'tie_convention':'If OKX book update and Binance trade share exact ms, use the same-ms OKX book; otherwise latest strictly prior book.'}}
    out=Path('output/continuous_dynamic_exact')/date;out.mkdir(parents=True,exist_ok=True)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:summary[k] for k in ['date','fill_count','hedge_group_count','total_capital_traded_usd','total_net_pnl_usd','qa']},ensure_ascii=False,indent=2))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);args=ap.parse_args();main(args.date)
