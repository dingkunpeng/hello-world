#!/usr/bin/env python3
import json
from pathlib import Path

ROOT=Path('output/continuous_dynamic_exact')
files=sorted(ROOT.glob('*/summary.json'))
rows=[]
for p in files:
    j=json.loads(p.read_text(encoding='utf-8'))
    rows.append({'date':j['date'],'screened_minutes':len(j['trigger_minutes_screened']),'fills':j['fill_count'],
                 'hedge_groups':j['hedge_group_count'],'traded_capital_usd':j['total_capital_traded_usd'],
                 'net_pnl_usd':j['total_net_pnl_usd'],'max_anchor_age_ms':j['qa']['max_anchor_book_age_ms'],
                 'max_hedge_age_ms':j['qa']['max_hedge_book_age_ms']})
summary={
 'policy':'Conservative lower-bound continuous test: exhaustive full-year 1m index-low sufficient-trigger screen, then exact Binance seller-aggressor trades vs latest OKX Level-A bid; dynamic tiers -40/-60/-80/-100/-120bp, $2k each, max one fill per tier per minute, +100ms OKX hedge, 32.5bp cost.',
 'dates_exact_replayed':len(rows),
 'screened_trigger_minutes':sum(r['screened_minutes'] for r in rows),
 'exact_fill_count':sum(r['fills'] for r in rows),
 'exact_hedge_groups':sum(r['hedge_groups'] for r in rows),
 'total_traded_capital_usd':sum(r['traded_capital_usd'] for r in rows),
 'total_net_pnl_usd':sum(r['net_pnl_usd'] for r in rows),
 'positive_dates':sum(r['net_pnl_usd']>0 for r in rows),
 'negative_dates':sum(r['net_pnl_usd']<0 for r in rows),
 'zero_dates':sum(abs(r['net_pnl_usd'])<1e-12 for r in rows),
 'max_single_day_pnl_usd':max([r['net_pnl_usd'] for r in rows],default=None),
 'min_single_day_pnl_usd':min([r['net_pnl_usd'] for r in rows],default=None),
 'rows':rows,
 'interpretation_limits':[
   'This is a LOWER BOUND on ideal dynamic opportunities because the 1m index-low screen is sufficient but not necessary; it can miss true intraminute dislocations.',
   'This is simultaneously an UPPER-QUALITY execution assumption within screened minutes because the peg uses the latest observed OKX book with no cancel/replace or network lag.',
   'One tier can fill at most once per minute; no intra-minute reload is allowed.',
   'The +100ms hedge and 32.5bp all-in cost are fixed research assumptions, not user-specific realized fees.',
   'A full stale-peg test is required before live deployment because lagged bids can be filled during broad-market declines.'
 ]
}
ROOT.mkdir(parents=True,exist_ok=True)
(ROOT/'aggregate_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
