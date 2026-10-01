# XAUUSD 24/5 Autonomous Agent: Build Plan

**Status:** core code built and tested (11 tests pass). Jev and Agenkit are *not* integrated: I could not verify either one's API or docs, so both sit behind interfaces. Nothing here is backtested. Every number marked ASSUMPTION or ESTIMATE needs testing.

## 1. Architecture (BRAIN / REFLEX, never blurred)

| Layer | Owner | Does |
|---|---|---|
| BRAIN | Claude Opus 5.5 | Research, strategy derivation, code, overnight review, deep re-read on escalation |
| REFLEX | Jev (interface `JevClient`) | Per-candle typed decision from a <400-token snapshot |
| CODE | `xau_agent/` | Every threshold, size, side effect, kill switch |

Per-tick flow (`loop.py`): broker equity and position -> `guard.mark` -> state snapshot -> Jev decision -> escalate to Opus if confidence < 0.60 or regime is crisis (Opus is re-gated by the same code) -> `gate` -> `size_lots` (quarter Kelly, 2% hard cap) -> `check_order` -> broker -> G.R.O.W alert.

## 2. Files built (`/xau_agent`)

- `state_engine.py`: mid, spread, imbalance, realized vol, inventory, drawdown. Strictly causal: future-dated and out-of-order ticks raise. Token budget enforced.
- `risk.py`: max drawdown 15%, max daily loss, max position, spread and stale-data limits. The kill switch is a latch; while tripped, orders are reduce-only and only a human can reset.
- `policy.py`: gate (quality >= 2, confidence > 0.80, risk_state safe, not toxic, not crisis), Kelly, vol-based stop, lot sizing. Sizing never rounds *up* to the minimum lot.
- `jev_schema.py`: typed schema, strict parser, `JevClient` and `Brain` interfaces.
- `loop.py`: live loop, one position at a time, journals every decision to JSONL.
- `review.py`: Brier score, calibration bins, gated-vs-missed split, proposals only.
- `tests/test_core.py`: gate edges, Kelly, causality, kill-switch latch, flatten, escalation, schema rejection, Brier.

**Spec fields I interpreted (the original text was garbled):** `toxic_flow` is a boolean; `setup_quality` is 0 to 3; the escalation floor is 0.60. Correct me if any are wrong.

## 3. Six Agenkit phases (approval gate at each)

Phase names follow your prompt. I have no Agenkit docs, so commands are not shown.

1. **Spec**: freeze this document plus the garbled-field interpretations. *Gate:* you confirm them.
2. **Architecture**: confirm the data source. Spot XAUUSD has no central order book, so `imbalance` will be 0 unless your broker gives depth. *Gate:* data feed and broker chosen.
3. **Plan**: `adapters/broker_<name>.py`, `adapters/jev_client.py`, `adapters/brain_opus.py`, `adapters/grow_alert.py` (webhook into the G.R.O.W panel), `jobs/forward_returns.py` (fills `fwd_ret_bps`), `jobs/nightly.py`, `config/limits.yaml`. *Gate:* limits reviewed.
4. **Test-first build**: add tests for each adapter before code, plus replay tests on recorded ticks. Add level-proximity fields to the snapshot (distance to 4H support and resistance, DXY and US10Y trend flags) while staying under 400 tokens.
5. **Review**: a deep backtest *with costs*: spread, slippage, swap, news-spike gaps. Walk-forward validation, not a single in-sample run. *Gate:* net-of-cost expectancy and the Brier score of Jev's confidence.
6. **Ship**: paper trade for at least several weeks, then the smallest live size. The kill switch is tested live before capital is added.

## 4. Live research read (4H, TradingView data via G.R.O.W, Oct 1 2026)

Automated structure readings, not primary-source news. I did **not** pull news, ETF flows, positioning, or central-bank data; that is still open.

| | Price | 4H bias | Key levels |
|---|---|---|---|
| **XAU (TVC:GOLD)** | 4178.74 | Bearish (moderate); bearish BOS at 4234.68 | S: 4163.62, 4158.96, 4110.5 (swing low). R: 4188.93, 4213.16, 4234.68, 4237.39 |
| **DXY** | 101.63 | Bullish (weak); premium zone | S: 101.40, 101.29, 101.20. R: 101.63 (unconfirmed high) |
| **US10Y** | 5.277 | Bullish (strong); premium zone | S: 5.12, 5.04. R: 5.31 (unconfirmed high) |

**Read:** a rising dollar and rising yields are consistent with the bearish structure in gold. Overhead supply is in the bearish order blocks at 4372.9 to 4399.6 and 4411.8 to 4449.0. Below price sits an unfilled bullish FVG at 4102.7 to 4152.8, a likely reaction zone. The tool's own swing setup (short at 4178.74, stop 4329.17) scores R:R 0.45:1, below its 2:1 minimum. Don't act on it.

**Hypothesis finalists (untested, ESTIMATES):**

1. **Continuation short on a retest of 4188.9 to 4237.4** (fib 0.382 to 0.618). *Invalidates:* 4H close above 4237 with DXY failing below 101.4. *Bear case:* gold is already in the discount zone, so chasing shorts is late.
2. **Sweep of 4110.5 and reclaim into the 4102.7 to 4152.8 FVG** (counter-trend long). *Invalidates:* 4H acceptance below 4102. *Bear case:* it fights a strong yield trend.
3. **Range or consolidation between 4110 and 4189**: fade the edges while DXY holds 101.4 to 101.63. *Invalidates:* a DXY break of 101.63 with a 10Y close above 5.31.

AMD/PO3 phase (accumulation, manipulation, distribution) needs 15m data against this 4H bias; I have not computed it. Your G.R.O.W panel is the right place for it.

## 5. Nightly loop

`nightly.py` -> `forward_returns.py` fills outcomes -> `review.nightly_report` -> I draft schema or threshold changes -> **you approve at the Agenkit gate** before the next open. Below 30 scored decisions the report refuses to propose changes.

## WHAT COULD I BE WRONG ABOUT?

Highest risk first.

1. **No edge exists yet.** Nothing is backtested. A well-built system around a zero-edge signal still loses money to costs.
2. **Jev is unverified.** Its calibration, latency, and API are unknown. If its 0.80+ confidence is overconfident, Kelly sizing amplifies the error. The 5% haircut is a guess; the Brier score decides.
3. **The edge may be incentive-driven.** Structure-based reads (BOS, order blocks, FVGs) are popular and subjective, and I have no evidence they are persistent after costs.
4. **Spot gold has no real order book.** Imbalance may be empty or noisy; your tick source decides this.
5. **Costs and gaps.** News spikes (CPI, FOMC) can jump through any stop. The stop model is volatility-based and untested, and the spread filter is a rough guard.
6. **Small account math.** With a small balance, the 2% risk cap often sizes below the minimum lot, so the agent skips trades rather than over-risking. Contract size per lot must be verified with your broker (default assumes 100 oz).
7. **Catalysts may be priced in.** I did not check news or positioning, so I can't say what is priced in. Treat the finalists as hypotheses.
8. **Hard limits are in code, not the model.** I checked: drawdown, daily loss, position, spread, staleness, and the kill switch are all enforced in `risk.py`. Escalated Opus decisions pass the same gate. Remaining gap: the broker adapter must refuse orders even if the agent crashes, so add broker-side stops and daily limits as a second layer.
9. **Tick-based volatility** treats irregular ticks as equal time steps. Switch to time-bucketed bars if tick rates vary widely.
10. **Data timestamps.** If broker and local clocks drift, the causality check could wrongly reject or wrongly pass ticks. Sync clocks before going live.

Priority: a system that survives beats one that looks profitable. Run it paper-only until a net-of-cost backtest and the Brier score both justify capital.
