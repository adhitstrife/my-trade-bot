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
- Limits screening to the configured allowlist and liquidity/spread requirements.

## Backtesting

```bash
python bot.py backtest --config config.json --candles candles.csv
```

The CSV must contain `timestamp,open,high,low,close`. A signal at one candle's close is filled no earlier than the following candle's open. Fees, slippage, protective exits, and the daily-loss guard are simulated. Treat results as research output, not a performance guarantee.

## Safety of live mode

Live order submission is locked behind both `--confirm-live` and `ALLOW_LIVE_TRADING=YES`. This is intentional: current code records an order submission but does not yet reconcile partial fills, open orders, or exchange position history. Do not unlock it until that reconciliation is implemented and tested.

## Configuration

Important controls in `config.json`:

- `fee_rate` and `execution_slippage_pct`: conservative paper-execution assumptions.
- `max_position_pct`: maximum exposure in one position.
- `max_risk_per_trade_pct`: maximum equity at risk if the stop is reached.
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

## Security

Store credentials only in `.env`, never commit them, and use an exchange API key without withdrawal permission. Rotate any key that was ever exposed.

## Disclaimer

Cryptocurrency trading carries substantial risk. Past simulation results do not predict future performance.
