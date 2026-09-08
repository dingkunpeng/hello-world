#!/usr/bin/env python3
import csv, io, json, zipfile, requests, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

START = datetime(2026,1,1,tzinfo=timezone.utc)
END_EXCL = datetime(2026,9,6,tzinfo=timezone.utc)
LADDER_BPS = [40,60,80,100,120]
LAYER_USD = 2000
OUT = Path('output/continuous_exante')
OUT.mkdir(parents=True, exist_ok=True)

SPOT_MONTH = 'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-{ym}.zip'
SPOT_DAY = 'https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{ymd}.zip'
INDEX_MONTH = 'https://data.binance.vision/data/futures/um/monthly/indexPriceKlines/BTCUSDT/1m/BTCUSDT-1m-{ym}.zip'
INDEX_DAY = 'https://data.binance.vision/data/futures/um/daily/indexPriceKlines/BTCUSDT/1m/BTCUSDT-1m-{ymd}.zip'

def get(url, tries=5):
    last=None
    for i in range(tries):
        try:
            r=requests.get(url, timeout=90)
            if r.status_code == 200:
                return r
            last=RuntimeError(f'{r.status_code} {url}')
        except Exception as e:
            last=e
        time.sleep(min(2**i, 10))
    raise last

def rows_from_zip(url):
    r=get(url)
    z=zipfile.ZipFile(io.BytesIO(r.content))
    name=next(n for n in z.namelist() if n.endswith('.csv'))
    rows=list(csv.reader(z.read(name).decode('utf-8-sig').splitlines()))
    if rows and rows[0] and not rows[0][0].replace('.','',1).isdigit():
        rows=rows[1:]
    return rows

def norm_ms(v):
    x=int(float(v))
    return x//1000 if x>10**14 else x

def add_rows(dst, rows):
    for r in rows:
        if r:
            dst[norm_ms(r[0])] = r

def load_all(month_tmpl, day_tmpl):
    d={}
    for m in range(1,9):
        ym=f'2026-{m:02d}'
        url=month_tmpl.format(ym=ym)
        print('download',url)
        add_rows(d, rows_from_zip(url))
    for day in range(1,6):
        ymd=f'2026-09-{day:02d}'
        url=day_tmpl.format(ymd=ymd)
        print('download',url)
        add_rows(d, rows_from_zip(url))
    return d

def iso(ms):
    return datetime.fromtimestamp(ms/1000,tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

spot = load_all(SPOT_MONTH, SPOT_DAY)
idx = load_all(INDEX_MONTH, INDEX_DAY)
start_ms=int(START.timestamp()*1000); end_ms=int(END_EXCL.timestamp()*1000)
minute_ms=60_000
expected=(end_ms-start_ms)//minute_ms
keys=[]; t=start_ms
while t<end_ms:
    if t not in spot or t not in idx:
        raise RuntimeError(f'missing synchronized 1m row at {iso(t)} spot={t in spot} idx={t in idx}')
    keys.append(t); t+=minute_ms
print('synchronized minutes',len(keys),'expected',expected)

triggers=[]
all_minutes=0
for i,t in enumerate(keys):
    if i==0: continue
    prev=keys[i-1]
    # strictly ex-ante anchor: previous completed Binance Index close only.
    anchor=float(idx[prev][4])
    prev_spot_close=float(spot[prev][4])
    prev_index_close=anchor
    prev_gap_bp=(prev_spot_close/prev_index_close-1)*10000
    cur_spot_low=float(spot[t][3])
    cur_spot_open=float(spot[t][1]); cur_spot_close=float(spot[t][4])
    cur_idx_low=float(idx[t][3]); cur_idx_close=float(idx[t][4])
    levels=[]
    for bp in LADDER_BPS:
        px=anchor*(1-bp/10000)
        levels.append({'offset_bp':bp,'limit_px':px,'price_through':cur_spot_low < px})
    filled=[x for x in levels if x['price_through']]
    all_minutes += 1
    if not filled: continue
    cap=LAYER_USD*len(filled)
    qty=sum(LAYER_USD/x['limit_px'] for x in filled)
    entry=cap/qty
    # proxy diagnostics only; never used for order eligibility.
    proxy_low_edge=(cur_idx_low/entry-1)*10000
    proxy_close_edge=(cur_idx_close/entry-1)*10000
    triggers.append({
        'minute_utc':iso(t),'minute_ms':t,
        'anchor_source':'prev_completed_binance_index_close',
        'anchor_price':anchor,
        'prev_spot_close':prev_spot_close,'prev_index_close':prev_index_close,'prev_gap_bp':prev_gap_bp,
        'spot_open':cur_spot_open,'spot_low':cur_spot_low,'spot_close':cur_spot_close,
        'index_low':cur_idx_low,'index_close':cur_idx_close,
        'filled_layers_proxy':len(filled),'capital_proxy_usd':cap,'qty_proxy_btc':qty,'entry_proxy':entry,
        'deepest_filled_bp':max(x['offset_bp'] for x in filled),
        'index_low_edge_proxy_bp':proxy_low_edge,'index_close_edge_proxy_bp':proxy_close_edge,
        'levels_json':json.dumps(levels,separators=(',',':'))
    })

fields=list(triggers[0].keys()) if triggers else []
with (OUT/'minute_triggers.csv').open('w',newline='',encoding='utf-8-sig') as f:
    if fields:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(triggers)

# group consecutive trigger minutes as clusters, for workload and event-frequency diagnostics only.
clusters=[]
if triggers:
    cur=[triggers[0]]
    for r in triggers[1:]:
        if r['minute_ms']-cur[-1]['minute_ms']<=60_000:
            cur.append(r)
        else:
            clusters.append(cur); cur=[r]
    clusters.append(cur)

cluster_rows=[]
for c in clusters:
    cluster_rows.append({
        'start_utc':c[0]['minute_utc'],'end_utc':c[-1]['minute_utc'],'minutes':len(c),
        'max_layers':max(x['filled_layers_proxy'] for x in c),
        'max_capital_proxy_usd':max(x['capital_proxy_usd'] for x in c),
        'worst_spot_low':min(x['spot_low'] for x in c),
        'min_index_low_edge_proxy_bp':min(x['index_low_edge_proxy_bp'] for x in c),
        'max_index_close_edge_proxy_bp':max(x['index_close_edge_proxy_bp'] for x in c),
    })
if cluster_rows:
    with (OUT/'trigger_clusters.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(cluster_rows[0].keys())); w.writeheader(); w.writerows(cluster_rows)

# descriptive buckets based solely on pre-known state; useful for later train/test filters, not applied to baseline.
def cnt(pred): return sum(1 for r in triggers if pred(r))
summary={
    'period_utc':'2026-01-01 through 2026-09-05 inclusive',
    'expected_minutes':expected,
    'evaluated_minutes':all_minutes,
    'lookahead_policy':'Anchor and arming use only previous completed minute Binance Index close. Candidate labels and current/future index/OKX values are forbidden for eligibility.',
    'baseline_policy':'Always armed; refresh ladder every minute; -40/-60/-80/-100/-120bp, $2k each; strict current-minute price-through is only a fill-candidate detector.',
    'trigger_minutes':len(triggers),
    'trigger_clusters':len(cluster_rows),
    'trigger_days':len(set(r['minute_utc'][:10] for r in triggers)),
    'layers_histogram':{str(k):sum(1 for r in triggers if r['filled_layers_proxy']==k) for k in range(1,6)},
    'pre_gap_buckets':{
        'abs_prev_gap_le_5bp':cnt(lambda r:abs(r['prev_gap_bp'])<=5),
        'abs_prev_gap_le_10bp':cnt(lambda r:abs(r['prev_gap_bp'])<=10),
        'prev_gap_between_-5_and_0bp':cnt(lambda r:-5<=r['prev_gap_bp']<=0),
    },
    'proxy_only_not_pnl':{
        'index_low_edge_positive_after_32_5bp':cnt(lambda r:r['index_low_edge_proxy_bp']>32.5),
        'index_close_edge_positive_after_32_5bp':cnt(lambda r:r['index_close_edge_proxy_bp']>32.5),
        'index_low_edge_negative_gross':cnt(lambda r:r['index_low_edge_proxy_bp']<0),
    },
    'max_consecutive_cluster_minutes':max([x['minutes'] for x in cluster_rows], default=0),
}
(OUT/'baseline_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
