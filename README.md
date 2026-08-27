# Indodax Trading Bot

This is a deliberately conservative spot-trading bot for Indodax. It has:

- **Paper mode** (default): simulated cash, fills, positions, and reports.
- **Live mode**: sends signed orders only after two explicit opt-ins.
- **Backtesting**: runs the same SMA crossover strategy on a CSV of historical candles.
- **Reports**: equity, P/L, current position, recent trades, and risk status on demand.
- **Paper-mode screener**: ranks only the configured allowlist at startup, then re-screens while flat.

It trades one IDR spot pair at a time. The included strategy is a simple fast/slow moving-average crossover, intended as a starting point—not investment advice or a claim of profitability.

## Quick start

1. Copy `config.example.json` to `config.json` and adjust the pair/risk settings.
2. For live trading only, copy `.env.example` to `.env` and add an Indodax Trade API key that has **trading only** enabled. Do not enable withdrawal permission.
3. Run paper mode:

```powershell
python bot.py run --config config.json
```

4. Ask for a report whenever needed:

```powershell
python bot.py report --config config.json
```

Stop the bot with `Ctrl+C`. Its state is saved in `data/state.json` after every cycle.

## Coin screener

The supplied `config.json` screens `BTCIDR`, `ETHIDR`, and `SOLIDR` on startup. It selects only a pair that meets the configured 24-hour IDR volume and bid/ask-spread filters, then ranks eligible pairs by a modest liquidity/spread-adjusted 24-hour momentum score. The selection is recorded in the report.

It is deliberately **paper-only** by default. It re-screens every four hours only when no asset is held, so the bot cannot switch away from a position that still needs to be sold. Adjust `screener.allowlist`, `min_volume_idr`, `max_spread_pct`, and `rescreen_hours` in `config.json` to suit your research. This is a filter, not a prediction model.

## Backtesting

Provide a CSV with `timestamp,open,high,low,close,volume` columns (timestamp can be ISO-8601 or Unix seconds):

```powershell
python bot.py backtest --config config.json --candles data/btcidr_1h.csv
```

The bot prints and stores a report in `reports/`. Backtests include configured fees and execute the signal at the candle close; they do not model spread, slippage, partial fills, or API latency. Treat their result as research, not a forecast.

## Live-mode guardrail

Set `"mode": "live"` in `config.json`, then use the explicit confirmation flag:

```powershell
python bot.py run --config config.json --confirm-live
```

Without the flag, no live order can be sent. Live mode also halts new orders when the configured daily loss limit is reached. Check Indodax’s current API documentation and fee schedule before enabling it.

## Report details

Reports are generated from the local bot ledger. In live mode, the report additionally asks Indodax for current balances, so it can show exchange balances alongside the bot ledger.
