#!/usr/bin/env python3
import argparse,csv,gzip,io,json,math,requests,zipfile
from pathlib import Path
from datetime import datetime,timezone,timedelta

LADDER_BP=[40,60,80,100,120]
LAYER_USD=2000.0
HEDGE_DELAY_MS=100
COST_BP=32.5
TICK=0.01
TRIGGER_CSV=Path('output/continuous_exante/minute_triggers.csv')
OUTROOT=Path('output/continuous_exante_exact')
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
                out.append({'minute_utc':r['minute_utc'],'start_ms':int(r['minute_ms']), 'anchor':float(r['anchor_price'])})
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
            out.append({'a':int(x[0]),'p':float(x[1]),'q':float(x[2]),'T':t,'m':str(x[6]).lower()=='true'})
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
    if not row or qty<=0:return None,0
    rem=qty; val=0.0
    for i in range(1,51):
        ps=row.get(f'bid_{i}_px'); qs=row.get(f'bid_{i}_qty')
        if not ps or not qs: continue
        p=float(ps); q=float(qs); take=min(rem,q); val+=take*p; rem-=take
        if rem<=1e-12: break
    return ((val/qty) if rem<=1e-10 else None),qty-rem

def minute_start(t): return (t//60000)*60000

def replay_day(date):
    mins=load_minutes(date)
    if not mins: return None
    minute_map={m['start_ms']:m for m in mins}; targets=set(minute_map)
    trades,burl=load_binance_trades(date,mins); seller=[x for x in trades if x['m']]
    item=okx_item(date)
    armed={(m,off):True for m in targets for off in LADDER_BP}
    fills=[]; pending={}; hedges=[]; ti=0; prev=None; prev_ts=None
    last_needed=max(m+60000 for m in targets)+HEDGE_DELAY_MS+1000

    def schedule(tr):
        m=minute_start(tr['T'])
        if m not in targets:return
        anchor=minute_map[m]['anchor']
        for off in LADDER_BP:
            key=(m,off)
            if not armed.get(key):continue
            limit=floor_tick(anchor*(1-off/10000))
            if tr['p'] < limit:
                armed[key]=False; qty=LAYER_USD/limit
                rec={'date':date,'minute_utc':minute_map[m]['minute_utc'],'tier_bp':off,'fill_t_ms':tr['T'],'fill_t_utc':iso(tr['T']),
                     'binance_trade_px':tr['p'],'binance_trade_qty':tr['q'],'static_anchor_prev_index_close':anchor,
                     'entry_limit_px':limit,'qty_btc':qty,'capital_usd':LAYER_USD,'hedge_arrival_ms':tr['T']+HEDGE_DELAY_MS}
                fills.append(rec); pending.setdefault(rec['hedge_arrival_ms'],[]).append(rec)

    def settle(now_ts,book,book_ts,strict_less=True):
        keys=sorted(k for k in pending if (k<now_ts if strict_less else k<=now_ts))
        for arr in keys:
            group=pending.pop(arr); tq=sum(x['qty_btc'] for x in group); cap=sum(x['capital_usd'] for x in group)
            sell,cov=book_vwap(book,tq)
            entry_value=sum(x['entry_limit_px']*x['qty_btc'] for x in group)
            gross=((sell*tq/entry_value)-1)*10000 if sell else None
            net=gross-COST_BP if gross is not None else None; pnl=cap*net/10000 if net is not None else None
            hedges.append({'arrival_ms':arr,'arrival_utc':iso(arr),'okx_book_ms':book_ts,'book_age_ms':arr-book_ts if book_ts else None,
                           'fills':len(group),'tiers_bp':[x['tier_bp'] for x in group],'qty_btc':tq,'capital_usd':cap,
                           'sell_vwap_usdt':sell,'covered_qty':cov,'gross_edge_bp':gross,'net_after_32_5bp':net,'net_pnl_usd':pnl,
                           'fill_refs':[{'minute_utc':x['minute_utc'],'tier_bp':x['tier_bp'],'fill_t_ms':x['fill_t_ms'],'entry_limit_px':x['entry_limit_px']} for x in group]})

    resp=requests.get(item['url'],stream=True,timeout=240); resp.raise_for_status(); resp.raw.decode_content=False
    with gzip.GzipFile(fileobj=resp.raw,mode='rb') as gz:
        rd=csv.DictReader(io.TextIOWrapper(gz,encoding='utf-8',errors='strict',newline=''))
        for row in rd:
            ts=int(row['exchTimeMs'])
            if prev is not None:
                settle(ts,prev,prev_ts,True)
                while ti<len(seller) and seller[ti]['T']<ts:
                    schedule(seller[ti]); ti+=1; settle(ts,prev,prev_ts,True)
            prev=row; prev_ts=ts
            while ti<len(seller) and seller[ti]['T']==ts:
                schedule(seller[ti]); ti+=1
            settle(ts,prev,prev_ts,False)
            if ts>last_needed and ti>=len(seller) and not pending: break
    while ti<len(seller): schedule(seller[ti]); ti+=1
    settle(10**18,prev,prev_ts,False)

    keys=[(x['minute_utc'],x['tier_bp']) for x in fills]
    total_pnl=sum((x['net_pnl_usd'] or 0) for x in hedges); total_cap=sum(x['capital_usd'] for x in hedges)
    uncovered=sum(1 for x in hedges if x['sell_vwap_usdt'] is None)
    summary={'date':date,
      'policy':'STRICT EX-ANTE stale-minute peg: every minute ladder is armed from previous completed Binance index close only; static for 60s; strict Binance seller-aggressor price-through; each tier max once/minute; +100ms OKX 50-level hedge; 32.5bp all-in cost.',
      'trigger_minutes_prefiltered_no_oracle_for_arming':[m['minute_utc'] for m in mins],
      'prefilter_note':'Current-minute 1m low is used only offline to skip minutes where an already-armed static order mathematically could not fill; it does not choose anchor, eligibility, or tier prices.',
      'binance_archive':burl,'okx_file':{'filename':item.get('filename'),'sizeMB':item.get('sizeMB')},
      'seller_aggressor_trades_in_trigger_minutes':len(seller),'fills':fills,'hedge_groups':hedges,
      'fill_count':len(fills),'hedge_group_count':len(hedges),'total_capital_traded_usd':total_cap,'total_net_pnl_usd':total_pnl,
      'positive_hedges':sum(1 for x in hedges if (x['net_pnl_usd'] or 0)>0),'negative_hedges':sum(1 for x in hedges if (x['net_pnl_usd'] or 0)<0),
      'uncovered_hedges':uncovered,
      'qa':{'duplicate_minute_tier_fills':len(keys)-len(set(keys)),
            'max_hedge_book_age_ms':max([x['book_age_ms'] for x in hedges if x['book_age_ms'] is not None],default=None),
            'tie_convention':'same-ms OKX update is usable; otherwise latest strictly prior book'}}
    out=OUTROOT/date; out.mkdir(parents=True,exist_ok=True); (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'date':date,'fill_count':len(fills),'hedges':len(hedges),'cap':total_cap,'pnl':total_pnl,'negative':summary['negative_hedges']},ensure_ascii=False))
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
    (OUTROOT/f'shard_{a.shard}_errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding='utf-8')
    if errors: raise RuntimeError(f'{len(errors)} day errors')
if __name__=='__main__': main()
