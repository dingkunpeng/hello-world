#!/usr/bin/env python3
import json
from pathlib import Path
ROOT=Path('output/dynamic_cancel_risk_exact')
latencies=['0','10','20','50','100']
rows=[]
for p in sorted(ROOT.glob('*/summary.json')):
    j=json.loads(p.read_text(encoding='utf-8'))
    rec={'date':j['date'],'by_latency':j['by_latency']}
    rows.append(rec)
summary={'experiment':'Causal stale-quote rescue test on the same 135 days / 564 static-risk minutes as the strict ex-ante baseline.',
         'baseline_static_net_pnl_usd':-4597.6972956022,
         'scope_note':'Not yet exhaustive dynamic-strategy EV; this isolates the effect of replacing the stale previous-minute anchor with an OKX best-bid peg lagged by cancel/replace latency.',
         'days_replayed':len(rows),'latencies_ms':{},'rows':rows}
for L in latencies:
    vals=[r['by_latency'][L] for r in rows if L in r['by_latency']]
    summary['latencies_ms'][L]={
        'fill_count':sum(v['fill_count'] for v in vals),
        'hedge_groups':sum(v['hedge_groups'] for v in vals),
        'capital_usd':sum(v['capital_usd'] for v in vals),
        'net_pnl_usd':sum(v['net_pnl_usd'] for v in vals),
        'positive_hedges':sum(v['positive_hedges'] for v in vals),
        'negative_hedges':sum(v['negative_hedges'] for v in vals),
        'uncovered_hedges':sum(v['uncovered_hedges'] for v in vals),
        'cancel_race_only_fills':sum(v['cancel_race_only_fills'] for v in vals),
        'ideal_zero_latency_fills':sum(v['ideal_zero_latency_fills'] for v in vals),
        'pnl_improvement_vs_static_usd':sum(v['net_pnl_usd'] for v in vals)-summary['baseline_static_net_pnl_usd']
    }
(ROOT/'aggregate_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k!='rows'},ensure_ascii=False,indent=2))
