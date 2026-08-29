# Indodax Trading Bot — Paper First

This is an experimental spot-trading bot for research and paper trading. It does not claim a profitable edge and is not ready for unattended live trading.

## Run

```bash
python bot.py run --config config.json
```

`config.json` defaults to paper mode. The canonical implementation is `bot.py`; files named `bot_v3*` are historical references only.

## What the bot does

- Reads only completed OHLC candles for SMA crossover signals.
- Uses the current bid/ask for paper-fill pricing, then adds configurable slippage and fees.
- Caps each entry by both maximum exposure and risk at the stop loss.
- Applies stop loss, take profit, trailing stop, and a daily-loss guard.
- Defaults to BTCIDR only; the screener is disabled to prevent pair switching.
- Requires an established trend (`trend_sma`), sufficient trend strength (ADX), a minimum SMA separation, cooldown between entries, and at most one entry per day.

## Backtesting

```bash
python bot.py backtest --config config.json --candles candles.csv
```

The CSV must contain `timestamp,open,high,low,close`. A signal at one candle's close is filled no earlier than the following candle's open. Fees, slippage, protective exits, and the daily-loss guard are simulated. Treat results as research output, not a performance guarantee.

To download BTCIDR candles directly from Indodax before backtesting:

```bash
python bot.py download-candles --config config.json --days 180
python bot.py backtest --config config.json --candles data/btcidr-candles.csv
```

To compare a small, predeclared set of conservative BTCIDR parameters without selecting on the final 20% of the data:

```bash
python bot.py optimize --config config.json --candles data/btcidr-candles.csv
```

Read the generated walk-forward report before changing `config.json`. Do not adopt a candidate unless its holdout return, expectancy, and profit factor are positive with enough closed positions.

## Safety of live mode

Live order submission is locked behind both `--confirm-live` and `ALLOW_LIVE_TRADING=YES`. This is intentional: current code records an order submission but does not yet reconcile partial fills, open orders, or exchange position history. Do not unlock it until that reconciliation is implemented and tested.

## Configuration

Important controls in `config.json`:

- `fee_rate` and `execution_slippage_pct`: conservative paper-execution assumptions.
- `max_position_pct`: maximum exposure in one position.
- `max_risk_per_trade_pct`: maximum equity at risk if the stop is reached.
- `trend_sma`, `min_adx`, and `min_sma_separation_pct`: filters that reject weak or choppy crossover entries.
- `max_entries_per_day` and `cooldown_candles`: explicit turnover limits.
- `stop_loss_pct`, `take_profit_pct`, `trailing_stop_pct`, `trailing_activation_pct`: exit policy.
- `screener`: allowlisted pair selection and liquidity/spread gates.

Start with paper mode, inspect every closed position, and validate over multiple market regimes before considering any live rollout.

## Reporting

```bash
python daily_report.py
python weekly_review.py
python audit_report.py
```

Reports read the local `data/state.json`. They are useful for monitoring but do not independently validate a trading edge.

## Deploy to Hermes VPS

Use this sequence when updating the existing bot on Hermes. It preserves the current paper ledger and avoids unintentionally enabling live orders.

```bash
cd /opt/data/my-trade-bot
git status --short
mkdir -p backup
cp config.json "backup/config-$(date +%F-%H%M%S).json"
test -f data/state.json && cp data/state.json "backup/state-$(date +%F-%H%M%S).json"
git pull --ff-only origin main
python3 -m compileall -q .
python3 -m unittest -v
```

Before restarting, confirm `config.json` contains `"mode": "paper"`. Also ensure `ALLOW_LIVE_TRADING` is not set in the shell, service, or scheduler environment.

```bash
unset ALLOW_LIVE_TRADING
./start_bot.sh
tail -n 50 logs/bot.log
cat data/state.json
```

The expected startup message states that closed historical candles were loaded. If the pull has a configuration conflict, tests fail, candle data is unavailable, or the bot reports an API error, stop there and restore the saved configuration/state if needed. Do not enable live mode to work around an error.

The watchdog script can continue to run after a successful paper-mode verification:

```bash
./watchdog.sh
```

## Security

Store credentials only in `.env`, never commit them, and use an exchange API key without withdrawal permission. Rotate any key that was ever exposed.

## Disclaimer

Cryptocurrency trading carries substantial risk. Past simulation results do not predict future performance.
