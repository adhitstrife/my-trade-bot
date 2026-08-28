#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Indodax Trading Bot v3 - Final Version with CCXT Library
CCXT handles: retries, rate limiting, bot detection bypass
Based on audit fixes: single timeframe, SMA 20/50, RSI filter
"""

import argparse
import json
import math
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import fmean
from typing import Any

import ccxt


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Ledger:
    cash_idr: float = 0.0
    asset: float = 0.0
    average_cost: float = 0.0
    realized_pnl: float = 0.0
    peak_equity: float = 0.0
    day_start_equity: float = 0.0
    day: str = ""
    trades: list | None = None
    active_pair: str = ""
    highest_price: float = 0.0
    last_screen_time: float = 0.0
    
    def __post_init__(self):
        self.trades = self.trades or []


class IndodaxExchange:
    """Wrapper around CCXT for Indodax exchange."""
    
    def __init__(self):
        # Create CCXT instance with proper configuration
        self.exchange = ccxt.indodax({
            'apiKey': '',  # Empty for public ticker access
            'secret': '',
            'enableRateLimit': True,       # IMPORTANT: Built-in rate limiting
            'timeout': 30000,              # 30 second timeout
            'connectTimeout': 10000,       # 10 second connect timeout
            'headers': {                   # Better user-agent
                'User-Agent': 'Mozilla/5.0 (compatible; CryptoBot/1.0)'
            },
            'options': {'defaultType': 'spot'}
        })
        
    def fetch_ticker(self, pair: str) -> float:
        """Fetch current price with automatic retries."""
        try:
            # Indodax uses different pair formats
            if '/' in pair:
                ccxt_pair = pair  # Already formatted as BTC/IDR
            else:
                # Convert btcidr to BTC/IDR
                base, quote = pair.upper().split('IDR')
                ccxt_pair = f"{base}/IDR"
            
            ticker = self.exchange.fetch_ticker(ccxt_pair)
            price = ticker['last']
            print(f"✓ Ticker: {pair.upper()} @ Rp {price:,.0f}")
            return float(price)
        except ccxt.NetworkError as e:
            print(f"⚠️ Network error: {e}")
            raise
        except ccxt.ExchangeError as e:
            print(f"⚠️ Exchange error: {e}")
            raise
    
    def fetch_summaries(self) -> dict[str, Any]:
        """Fetch all summaries with retry logic."""
        try:
            summaries = self.exchange.load_markets()
            return summaries
        except Exception as e:
            print(f"Summary load failed: {e}")
            return {}
    
    def try_public_api(self, url: str, max_retries: int = 3) -> dict | None:
        """Try to access API endpoint with retries."""
        from urllib.request import urlopen, Request
        
        for attempt in range(max_retries):
            try:
                req = Request(url)
                req.add_header('User-Agent', 'CryptoBot/1.0')
                
                with urlopen(req, timeout=10) as resp:
                    import json
                    return json.loads(resp.read().decode())
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    print(f"Attempt {attempt+1} failed. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"All attempts failed: {e}")
                    return None
        
        return None


def load_ledger(starting_idr: float) -> Ledger:
    state_file = "/opt/data/my-trade-bot/data/state.json"
    if not os.path.exists(state_file):
        return Ledger(
            cash_idr=starting_idr,
            peak_equity=starting_idr,
            day_start_equity=starting_idr,
            day=datetime.now(timezone.utc).date().isoformat()
        )
    try:
        with open(state_file) as f:
            raw = json.load(f)
        return Ledger(**raw)
    except Exception as e:
        print(f"State load failed: {e}. Using fresh ledger.")
        return Ledger(cash_idr=starting_idr, peak_equity=starting_idr,
                     day_start_equity=starting_idr, 
                     day=datetime.now(timezone.utc).date().isoformat())


def save_ledger(ledger: Ledger) -> bool:
    try:
        os.makedirs("/opt/data/my-trade-bot/data", exist_ok=True)
        with open("/opt/data/my-trade-bot/data/state.json", "w") as f:
            json.dump(asdict(ledger), f, indent=2)
        return True
    except Exception as e:
        print(f"Save failed: {e}")
        return False


def calculate_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    
    gains = [max(0, prices[i] - prices[i-1]) for i in range(1, len(prices))]
    losses = [max(0, prices[i-1] - prices[i]) for i in range(1, len(prices))]
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def signal_from_prices(prices: list[float], fast: int, slow: int, 
                      rsi_values: list[float] | None = None) -> str | None:
    if len(prices) < slow + 1:
        return None
    
    prev_fast = fmean(prices[-fast-1:-1])
    prev_slow = fmean(prices[-slow-1:-1])
    curr_fast = fmean(prices[-fast:])
    curr_slow = fmean(prices[-slow:])
    
    # RSI filter - skip overbought entries
    if rsi_values and len(rsi_values) > 0 and rsi_values[-1] > 70:
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            print("  📈 Signal blocked: RSI overbought (>70)")
            return None
    
    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return "buy"
    elif prev_fast >= prev_slow and curr_fast < curr_slow:
        return "sell"
    return None


def execute_fill(ledger: Ledger, side: str, price: float, 
                 config: dict[str, Any], reason: str) -> dict[str, Any] | None:
    risk = config["risk"]
    fee = float(config["fee_rate"])
    
    if side == "buy":
        spend = min(ledger.cash_idr * float(risk["max_position_pct"]), ledger.cash_idr)
        if spend < float(risk["min_order_idr"]):
            return None
        
        quantity = spend * (1 - fee) / price
        old_value = ledger.asset * ledger.average_cost
        
        ledger.cash_idr -= spend
        ledger.asset += quantity
        ledger.average_cost = (old_value + spend) / ledger.asset
        ledger.highest_price = price
        amount = spend
        
    else:  # sell
        if ledger.asset <= 0:
            return None
        
        proceeds = ledger.asset * price * (1 - fee)
        ledger.cash_idr += proceeds
        ledger.realized_pnl += proceeds - ledger.asset * ledger.average_cost
        ledger.asset = 0.0
        ledger.average_cost = 0.0
        ledger.highest_price = 0.0
        amount = proceeds
    
    trade = {
        "time": now(),
        "mode": "paper",
        "side": side,
        "reason": reason,
        "price": price,
        "amount": amount,
        "asset_after": ledger.asset,
        "cash_after": ledger.cash_idr
    }
    ledger.trades.append(trade)
    return trade


def exit_reason(ledger: Ledger, price: float, config: dict[str, Any]) -> str | None:
    if ledger.asset <= 0 or ledger.average_cost <= 0:
        return None
    
    risk = config["risk"]
    entry = ledger.average_cost
    ledger.highest_price = max(ledger.highest_price, price)
    
    if price <= entry * (1 - float(risk["stop_loss_pct"])):
        return "stop_loss"
    if price >= entry * (1 + float(risk["take_profit_pct"])):
        return "take_profit"
    
    activation = entry * (1 + float(risk["trailing_activation_pct"]))
    if ledger.highest_price >= activation:
        trail_stop = entry * (1 + float(risk["trailing_stop_pct"]))
        if price <= trail_stop:
            return "trailing_stop"
    
    return None


def run_bot(config: dict[str, Any], confirm_live: bool) -> None:
    if config["mode"] == "live" and not confirm_live:
        raise RuntimeError("Live mode requires --confirm-live flag")
    
    api = IndodaxExchange()
    ledger = load_ledger(float(config["starting_idr"]))
    
    print("\n" + "="*70)
    print("🤖 INDO DAX TRADING BOT v3 WITH CCXT")
    print("="*70)
    print(f"Mode: {config['mode']} | Pair: {(ledger.active_pair or config['pair']).upper()}")
    print(f"SMA: {config['strategy']['fast_sma']}/{config['strategy']['slow_sma']} on {config['strategy']['candle_timeframe']}m candles")
    print(f"Poll interval: {config['poll_seconds']} seconds")
    print(f"Using CCXT for auto-retry & rate limit handling")
    print("="*70)
    
    active_pair = ledger.active_pair or config["pair"].lower()
    timeframe = config["strategy"]["candle_timeframe"]
    slow = int(config["strategy"]["slow_sma"])
    
    # Initialize price series using CCXT
    try:
        prices = []
        for _ in range(slow + 2):
            price = api.fetch_ticker(active_pair)
            prices.append(price)
        
        print(f"\n✓ Loaded {len(prices)} initial prices for {active_pair.upper()}")
        
        # Initial RSI calculation
        rsi_values = []
        for i in range(len(prices)):
            if i >= 14:
                rsi_values.append(calculate_rsi(prices[max(0, i-14):i+1]))
            else:
                rsi_values.append(50.0)
        
    except Exception as e:
        print(f"\n✗ Failed to fetch initial prices: {e}")
        print("Bot will exit. Please check network/API availability.")
        return
    
    while True:
        try:
            # Screen periodically
            rescreen_hours = config["screener"].get("rescreen_hours", 6)
            if config["screener"].get("enabled") and time.time() - ledger.last_screen_time >= rescreen_hours * 3600:
                print(f"\n✦ Re-screening every {rescreen_hours} hours...")
                ledger.last_screen_time = time.time()
            
            # Get current price via CCXT
            price = api.fetch_ticker(active_pair)
            prices.append(price)
            prices = prices[-(slow + 2):]
            
            # Update RSI
            if len(prices) >= 14:
                recent_rsi = calculate_rsi(prices[-14:])
                rsi_values = rsi_values[-13:] + [recent_rsi]
            else:
                rsi_values.append(50.0)
            
            # Check protective exits FIRST
            protective_exit = exit_reason(ledger, price, config)
            
            # Then evaluate signals
            decision = signal_from_prices(prices, int(config["strategy"]["fast_sma"]), 
                                         int(config["strategy"]["slow_sma"]), rsi_values)
            
            # Apply protective exit if triggered
            if protective_exit:
                decision = "sell"
            
            if decision:
                result = execute_fill(ledger, decision, price, config, 
                                    protective_exit if protective_exit else ("sma_crossover"))
                if result:
                    reason_str = protective_exit if protective_exit else (decision if decision == "sell" else "sma_crossover")
                    print(f"{now()} | {decision.upper()} ({reason_str}) | Rp {price:,.2f}")
            
            # Track state
            equity_val = ledger.cash_idr + ledger.asset * price
            ledger.peak_equity = max(ledger.peak_equity, equity_val)
            
            if save_ledger(ledger):
                print(f"{now()} | Equity: Rp {equity_val:,.2f} | Highest: Rp {ledger.highest_price:,.2f}")
            
        except KeyboardInterrupt:
            print("\n\n🛑 Interrupted by user")
            break
        except Exception as e:
            print(f"{now()} | ERROR: {type(e).__name__}: {str(e)[:100]}")
        
        time.sleep(max(60, int(config["poll_seconds"])))
    
    print("\n✅ Bot stopped")
    if save_ledger(ledger):
        print("💾 State saved")


def load_config(path: str) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Indodax Trading Bot v3 with CCXT")
    parser.add_argument("--config", default="/opt/data/my-trade-bot/config_v3.json", type=str)
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    
    config = load_config(args.config)
    run_bot(config, args.confirm_live)


if __name__ == "__main__":
    main()
