# Candidate #005 — Net EV & Capital Efficiency

## Fixed evidence inputs

Primary hedge venue: OKX Spot BTC-USDT, historical module=6 50-level tick-by-tick order book.

Binance fixed ladder results:
- Sweep 1: $10,000, 0.14347261 BTC, weighted entry 69,699.71
- Sweep 2: $8,000, 0.11469564 BTC, weighted entry 69,749.82
- Sweep 3: $4,000, 0.05726560 BTC, weighted entry 69,849.96

OKX gross executable edge (bp):

| Sweep | 0ms | 50ms | 100ms | 250ms | 500ms |
|---|---:|---:|---:|---:|---:|
| 1 | 51.42 | 43.91 | 40.42 | 40.21 | 44.34 |
| 2 | 55.27 | 46.12 | 46.75 | 49.73 | 48.94 |
| 3 | 22.25 | 17.85 | 19.23 | 20.36 | 19.33 |

## Current regular-fee scenarios

Current published regular-user fee references used for sensitivity analysis:
- Binance Spot maker: 10 bp standard, 7.5 bp with BNB 25% discount.
- OKX SG Reg Spot taker: 20 bp.

Thus fee-only hurdle is 30 bp standard or 27.5 bp with Binance BNB discount.

### At 100ms

| Sweep | Gross bp | Net after 30bp fees | Net after 27.5bp fees |
|---|---:|---:|---:|
| 1 | 40.42 | 10.42 | 12.92 |
| 2 | 46.75 | 16.75 | 19.25 |
| 3 | 19.23 | -10.77 | -8.27 |

### At 250ms

| Sweep | Gross bp | Net after 30bp fees | Net after 27.5bp fees |
|---|---:|---:|---:|
| 1 | 40.21 | 10.21 | 12.71 |
| 2 | 49.73 | 19.73 | 22.23 |
| 3 | 20.36 | -9.64 | -7.14 |

## Non-fee cost sensitivity (Binance BNB + OKX Reg)

This treats rebalance + leg-risk + operational slippage as an additional all-in bp cost per traded notional.

### 100ms

| Extra non-fee cost | Sweep 1 net | Sweep 2 net | Cluster $ PnL (10k + 8k) |
|---:|---:|---:|---:|
| 0 bp | 12.92 bp | 19.25 bp | $28.32 |
| 2 bp | 10.92 bp | 17.25 bp | $24.72 |
| 5 bp | 7.92 bp | 14.25 bp | $19.32 |
| 10 bp | 2.92 bp | 9.25 bp | $10.32 |

### 250ms

| Extra non-fee cost | Sweep 1 net | Sweep 2 net | Cluster $ PnL (10k + 8k) |
|---:|---:|---:|---:|
| 0 bp | 12.71 bp | 22.23 bp | $30.50 |
| 2 bp | 10.71 bp | 20.23 bp | $26.90 |
| 5 bp | 7.71 bp | 17.23 bp | $21.50 |
| 10 bp | 2.71 bp | 12.23 bp | $12.50 |

## Capital lock

To capture Sweep 1 + Sweep 2 without relying on a 21-second rebalance:
- Binance must have at least ~$18,000 USDT available.
- Hedge venue must have at least 0.25816825 BTC available (roughly ~$18,000 around the event price).
- Total pre-positioned strategy capital is therefore roughly $36,000 before any perp margin used to neutralize inventory exposure.

At 100ms, using Binance BNB discount + OKX Reg + 5bp non-fee cost, the two-sweep cluster earns about $19.32. That is only about 5.37 bp on ~$36,000 of locked capital for this cluster.

Therefore final strategy attractiveness cannot be judged from per-trade edge alone; annual event frequency and capital reuse rate are required.

## Critical execution rule correction

Once a Binance passive buy has filled, the strategy already owns BTC. It is unsafe to say “do not hedge if current edge is below threshold” unless the strategy intentionally accepts directional BTC exposure.

For a market-neutral arbitrage system:
- post-fill hedge should normally be mandatory (subject to emergency kill-switch logic);
- the edge threshold must control whether/where Binance bids are resting or reloaded *before* the next fill;
- Candidate #005 Sweep 3 shows why blindly reloading all shallow bid layers is dangerous.

A robust reload rule should include expected adverse-selection during the hedge latency, not just current spread.

## Bitget status

The Bitget data-download frontend exposes:
- `/statistics/public/download/getPublicDataV2`
- `/statistics/public/download/getSymbolList`

The frontend parameter mapping for Spot Depth is:
- businessLine = 1
- businessType = 3
- dateType = 1 for day mode
- displaySymbol
- beginTimeStr
- endTimeStr

GitHub Runner requests to api.bitget.com are currently blocked by Cloudflare (HTTP 403), so historical Bitget L2 for 2026-02-15 is not yet included in the executable matrix. The official Bitget download page confirms Spot Depth archives exist.

## Next research gate

1. Resolve/download Bitget 2026-02-15 Spot Depth, if accessible through an allowed client/path.
2. Do not let Bitget block the main line: use OKX as the Level-A primary hedge baseline.
3. Apply the same cost/capital-efficiency model to all validated yearly events.
4. Estimate annual EV on total locked capital, not only per-trade notional.
