# 2026 Annual EV — Verified Lower Bound (work in progress)

## Standardized annual replay rule

Fair-price anchor: OKX BTC-USDT historical best bid immediately before the Binance sweep.

Binance resting-bid ladder, fixed ex ante for all events:
- -40 bp: $2,000
- -60 bp: $2,000
- -80 bp: $2,000
- -100 bp: $2,000
- -120 bp: $2,000

Only strict price-through levels count as high-confidence fills. A mere touch is excluded.

Primary cost scenario used here:
- Binance maker with BNB discount: 7.5 bp
- OKX SG regular taker: 20 bp
- non-fee execution/rebalance/leg-risk allowance: 5 bp
- total hurdle: 32.5 bp
- hedge latency scenario: 100 ms

This is a research scenario, not a claim about the user's actual fee tier.

## Level-A verified events so far

### 2026-02-07
- OKX pre-event best bid: 68,276.8
- Binance low: 67,300.0
- Fixed ladder price-through layers: 5/5
- Binance notional: $10,000
- weighted ladder entry: 67,730.03498
- OKX 100ms executable sell VWAP: 68,193.42627
- gross executable edge: 68.4174 bp
- net after 32.5bp hurdle: 35.9174 bp
- net PnL: $35.92

### 2026-07-10
- OKX pre-event best bid: 64,557.6
- Binance low: 63,999.0
- Fixed ladder price-through layers: 3/5
- Binance notional: $6,000
- weighted ladder entry: 64,170.08121
- OKX 100ms executable sell VWAP: 64,506.9
- gross executable edge: 52.4884 bp
- net after 32.5bp hurdle: 19.9884 bp
- net PnL: $11.99

### 2026-08-22 — rejected false positive
- Spot–Index 1m screen: approximately -68.24 bp
- OKX pre-event best bid: 76,626.0
- Binance low: 76,618.7
- Fixed ladder price-through layers: 0/5
- Reason: cross-exchange spot dislocation was < 40bp; the large Spot–Index signal did not translate into a tradable Binance-vs-OKX gap.
- PnL: $0

## Candidate #005 (2026-02-15) restated under the same annual ladder

Earlier Candidate #005 analysis used an event-specific round-number ladder. For annual EV this is replaced with the standardized -40/-60/-80/-100/-120bp ladder to avoid hindsight bias.

### Sweep 1
- OKX pre-event best bid: 70,058.1
- Binance low: 69,459.35
- standardized price-through layers: 3/5
- notional: $6,000
- weighted entry: 69,637.56345
- OKX 100ms sell VWAP: 69,981.40928
- gross edge: 49.3765 bp
- net after 32.5bp hurdle: 16.8765 bp
- net PnL: $10.13

### Sweep 2
- OKX pre-event best bid: 70,135.3
- Binance low: 69,573.45
- standardized price-through layers: 3/5
- notional: $6,000
- weighted entry: 69,714.30004
- OKX 100ms sell VWAP: 70,075.9
- gross edge: 51.8688 bp
- net after 32.5bp hurdle: 19.3688 bp
- net PnL: $11.62

### Sweep 3
- OKX pre-event best bid: 70,005.4
- Binance low: 69,746.48
- standardized price-through layers: 0/5
- result: no resting bid should have filled under the annual rule; no hedge trade; PnL $0.

Candidate #005 standardized cluster net PnL: $21.75.

## Verified lower bound to date

Positive standardized Level-A validated PnL at 100ms and 32.5bp hurdle:
- 2026-02-07: $35.92
- 2026-02-15 cluster: $21.75
- 2026-07-10: $11.99
- 2026-08-22: $0 (rejected)

Cumulative verified PnL so far: $69.66.

Known peak pre-positioned capital requirement among these standardized replays is at least $24,000 for the two-sweep 2026-02-15 cluster ($12k USDT on Binance plus roughly $12k BTC inventory on the hedge venue before rebalancing).

On $24,000 locked capital, the currently verified subset contributes about 29.0 bp cumulatively through 2026-09-05. Annualizing this incomplete lower bound mechanically over 248 observed days gives about 0.43% per year, but this is NOT the final strategy return: most annual candidates remain unvalidated and the strongest 2026-07-20 event has not yet been replayed under the standardized Level-A rule.

## Interpretation

The current $69.66 is a verified lower bound from the subset already replayed, not an estimate of final annual profit. It is useful only as a floor.

The next task is to validate the remaining L2-priority events under the identical fixed ladder and cost model. Only then can annual EV and capital efficiency be estimated without cherry-picking.
