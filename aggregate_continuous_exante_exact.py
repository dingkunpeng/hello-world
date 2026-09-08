#!/usr/bin/env python3
import json
from pathlib import Path
ROOT=Path('output/continuous_exante_exact')
rows=[]
for p in sorted(ROOT.glob('*/summary.json')):
    j=json.loads(p.read_text(encoding='utf-8'))
    rows.append({'date':j['date'],'trigger_minutes':len(j['trigger_minutes_prefiltered_no_oracle_for_arming']),
                 'fills':j['fill_count'],'hedges':j['hedge_group_count'],'capital_usd':j['total_capital_traded_usd'],
                 'net_pnl_usd':j['total_net_pnl_usd'],'positive_hedges':j['positive_hedges'],'negative_hedges':j['negative_hedges'],
                 'uncovered_hedges':j['uncovered_hedges'],'max_hedge_age_ms':j['qa']['max_hedge_book_age_ms']})
summary={
 'policy':'STRICT EX-ANTE stale-minute peg. Every minute armed from previous completed Binance index close only; five static tiers -40/-60/-80/-100/-120bp, $2k each; strict Binance seller-aggressor price-through; +100ms OKX 50-level hedge; 32.5bp cost.',
 'days_replayed':len(rows),'trigger_minutes':sum(r['trigger_minutes'] for r in rows),'fill_count':sum(r['fills'] for r in rows),
 'hedge_groups':sum(r['hedges'] for r in rows),'total_traded_capital_usd':sum(r['capital_usd'] for r in rows),
 'total_net_pnl_usd':sum(r['net_pnl_usd'] for r in rows),'positive_days':sum(r['net_pnl_usd']>0 for r in rows),
 'negative_days':sum(r['net_pnl_usd']<0 for r in rows),'zero_days':sum(abs(r['net_pnl_usd'])<1e-12 for r in rows),
 'positive_hedges':sum(r['positive_hedges'] for r in rows),'negative_hedges':sum(r['negative_hedges'] for r in rows),
 'uncovered_hedges':sum(r['uncovered_hedges'] for r in rows),
 'best_day':max(rows,key=lambda r:r['net_pnl_usd']) if rows else None,'worst_day':min(rows,key=lambda r:r['net_pnl_usd']) if rows else None,
 'rows':rows,
 'decision_rule':'If this always-armed static previous-minute anchor is materially negative after exact replay, reject it and move to a continuously repriced or gated arming rule; do not rescue it with hindsight event selection.'
}
(ROOT/'aggregate_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k!='rows'},ensure_ascii=False,indent=2))
