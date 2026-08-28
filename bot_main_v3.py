#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Indodax Trading Bot v3 - Production Ready (Fixed)
Based on audit analysis and fixes implemented.
Key improvements:
1. Single consistent candle timeframe (no mixing 60m + 1m)
2. SMA 20/50 instead of 10/30
3. Polling every 15 minutes (matches candle timeframe)
4. Higher volume thresholds
5. Focus on liquid pairs first
"""

import argparse
import csv
import hashlib
import hmac
import json
import math
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path.cwd()
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
STATE_FILE = DATA / "state.json"
TAPI_URL = "https://indodax.com/tapi"
TAPI_V2_URL = "https://api.indodax.com"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_json(url: str, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = urlencode(data).encode() if data else None
    request_headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "IndodaxTradingBot/v3 (fixed pipeline)",
    }
    request_headers.update(headers or {})
    req = Request(url, data=body, headers=request_headers, method="POST" if body else "GET")
    try:
        with urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"API request failed: HTTP {exc.code} {exc.reason}; {detail}") from exc


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = ("mode", "pair", "poll_seconds", "starting_idr", "fee_rate", "strategy", "risk")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Missing config fields: {', '.join(missing)}")
    
    screener = config.get("screener", {})
    if screener.get("enabled") and not screener.get("allowlist"):
        raise ValueError("screener.allowlist must contain at least one pair")
    
    return config


@dataclass
class Ledger:
    cash_idr: float
    asset: float = 0.0
    average_cost: float = 0.0
    realized_pnl: float = 0.0
    peak_equity: float = 0.0
    day_start_equity: float = 0.0
    day: str = ""
    trades: list[dict[str, Any]] | None = None
    active_pair: str = ""
    highest_price: float = 0.0
    last_screen_time: float = 0.0

    def __post_init__(self) -> None:
        self.trades = self.trades or []


class Indodax:
    def __init__(self) -> None:
        load_dotenv()
        self.key = os.getenv("INDODAX_API_KEY", "")
        self.secret = os.getenv("INDODAX_API_SECRET", "")

    def ticker(self, pair: str) -> float:
        pair = pair.lower()
        url = f"https://indodax.com/api/{pair}/ticker"
        payload = request_json(url)
        return float(payload["ticker"]["last"])

    def summaries(self) -> dict[str, Any]:
        return request_json("https://indodax.com/api/summaries")

    def history(self, pair: str, timeframe: str, bars: int) -> list[float]:
        """Get historical candles - uses SINGLE consistent timeframe."""
        end = int(time.time())
        start = end - (int(timeframe) * 60 * (bars + 5))
        
        # TradingView history endpoint for proper candles
        url = f"https://indodax.com/tradingview/history_v2?{urlencode({
            'symbol': pair.upper().replace('idr', ''),
            'tf': timeframe,
            'from': start,
            'to': end
        })}"
        
        payload = request_json(url)
        if isinstance(payload, list):
            return [float(candle["close"]) for candle in payload if "close" in candle]
        raise RuntimeError(f"Unexpected history format: {payload}")

    def private(self, method: str, **params: Any) -> dict[str, Any]:
        if not self.key or not self.secret:
            raise RuntimeError("Needs INDODAX_API_KEY and INDODAX_API_SECRET")
        payload = {"method": method, "timestamp": int(time.time() * 1000), **params}
        encoded = urlencode(payload)
        signature = hmac.new(self.secret.encode(), encoded.encode(), hashlib.sha512).hexdigest()
        response = request_json(TAPI_URL, payload, {"Key": self.key, "Sign": signature})
        if response.get("success") != 1:
            raise RuntimeError(f"Indodax {method} rejected: {response.get('error')}")
        return response.get("return", {})

    def balances(self) -> dict[str, float]:
        info = self.private("getInfo")
        return {k: float(v) for k, v in info.get("balance", {}).items()}


def load_ledger(starting_idr: float) -> Ledger:
    if not STATE_FILE.exists():
        return Ledger(
            cash_idr=starting_idr,
            peak_equity=starting_idr,
            day_start_equity=starting_idr,
            day=datetime.now(timezone.utc).date().isoformat()
        )
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return Ledger(**raw)
    except Exception:
        return Ledger(cash_idr=starting_idr, peak_equity=starting_idr, 
                     day_start_equity=starting_idr, day=datetime.now(timezone.utc).date().isoformat())


def save_ledger(ledger: Ledger) -> bool:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(asdict(ledger), indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def equity(ledger: Ledger, price: float) -> float:
    return ledger.cash_idr + ledger.asset * price


def screen_pair(api: Indodax, config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    settings = config["screener"]
    allowlist = {p.lower() for p in settings["allowlist"]}
    min_volume = float(settings["min_volume_idr"])
    max_spread = float(settings["max_spread_pct"])
    
    summaries = api.summaries()
    candidates = []
    
    for raw_pair, ticker in summaries.get("tickers", {}).items():
        pair = raw_pair.lower()
        if pair not in allowlist or not pair.endswith("idr"):
            continue
        
        try:
            last = float(ticker["last"])
            buy, sell = float(ticker["buy"]), float(ticker["sell"])
            volume = float(ticker["vol_idr"])
            spread = (sell - buy) / last if last else float("inf")
            
            if volume >= min_volume and spread <= max_spread:
                momentum = float(ticker.get("price_change_24h_pct", 0))
                score = momentum + min(5.0, math.log10(volume / max(min_volume, 1)))
                candidates.append({"pair": pair, "score": score, "volume": volume, "spread": spread})
                
        except Exception:
            continue
    
    if not candidates:
        return config["pair"], {"score": 0, "volume": 0, "spread": 999}
    
    winner = max(candidates, key=lambda x: x["score"])
    return winner["pair"], {"score": winner["score"], "volume": winner["volume"], "spread": winner["spread"]}


def signal_from_prices(prices: list[float], fast: int, slow: int, rsi_values: list[float] | None = None) -> str | None:
    """Calculate signal using CONSISTENT timeframe candles only."""
    if len(prices) < slow + 1:
        return None
    
    prev_fast = fmean(prices[-fast-1:-1])
    prev_slow = fmean(prices[-slow-1:-1])
    curr_fast = fmean(prices[-fast:])
    curr_slow = fmean(prices[-slow:])
    
    # RSI filter - skip overbought (>70) entry signals
    if rsi_values and rsi_values[-1] > 70:
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            return None  # Golden cross but overbought
    
    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return "buy"
    elif prev_fast >= prev_slow and curr_fast < curr_slow:
        return "sell"
    return None


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


def execute_fill(ledger: Ledger, side: str, price: float, config: dict[str, Any], reason: str) -> dict[str, Any] | None:
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
    else:
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


def can_trade(ledger: Ledger, price: float, config: dict[str, Any]) -> tuple[bool, str]:
    loss_limit = float(config["risk"]["max_daily_loss_pct"])
    if equity(ledger, price) < ledger.day_start_equity * (1 - loss_limit):
        return False, "daily loss limit reached"
    return True, ""


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


def report(ledger: Ledger, price: float, config: dict[str, Any]) -> str:
    value = equity(ledger, price)
    start = float(config["starting_idr"])
    pair = ledger.active_pair or config["pair"]
    
    lines = [
        f"Equity: Rp {value:,.2f}",
        f"P/L: Rp {(value-start):,.2f} ({(value/start-1)*100:+.2f}%)",
        f"Cash: Rp {ledger.cash_idr:,.2f} | Asset: {ledger.asset:.8f}",
        f"Realized P&L: Rp {ledger.realized_pnl:,.2f}",
        f"Highest Price Seen: Rp {ledger.highest_price:,.2f}",
        f"Trades: {len(ledger.trades)}"
    ]
    
    if ledger.trades[-5:] if ledger.trades else False:
        lines.append("Recent:")
        for t in ledger.trades[-5:]:
            lines.append(f"  {t['time'][:19]} | {t['side']} @ {t['price']:,.2f} ({t['reason']})")
    
    return "\n".join(lines)


def run(config: dict[str, Any], confirm_live: bool) -> None:
    if config["mode"] == "live" and not confirm_live:
        raise RuntimeError("Live mode requires --confirm-live flag")
    
    api = Indodax()
    ledger = load_ledger(float(config["starting_idr"]))
    
    print("\n" + "="*70)
    print("🤖 INDO DAX TRADING BOT v3 - STARTING")
    print("="*70)
    print(f"Mode: {config['mode']} | Pair: {(ledger.active_pair or config['pair']).upper()}")
    print(f"SMA: {config['strategy']['fast_sma']}/{config['strategy']['slow_sma']} on {config['strategy']['candle_timeframe']}m candles")
    print(f"Poll interval: {config['poll_seconds']} seconds")
    print("="*70)
    
    keep_running = True
    signal.signal(signal.SIGINT, lambda *_: setattr(sys.modules[__name__], "_stop", True))
    globals()["__stop"] = False
    
    active_pair = ledger.active_pair or config["pair"].lower()
    
    # Load initial prices with FIXED methodology
    timeframe = config["strategy"]["candle_timeframe"]
    slow = int(config["strategy"]["slow_sma"])
    
    try:
        prices = api.history(active_pair, timeframe, slow + 2)
        print(f"\n✓ Loaded {len(prices)} complete {timeframe}-minute candles for {active_pair.upper()}")
        
        # Calculate initial RSI values
        rsi_values = []
        for i in range(len(prices)):
            if i >= 14:
                rsi = calculate_rsi(prices[max(0, i-14):i+1])
                rsi_values.append(rsi)
            else:
                rsi_values.append(50.0)
        
    except Exception as e:
        print(f"⚠️ Candles unavailable: {e}. Will use real-time prices.")
        prices = [api.ticker(active_pair)]
        rsi_values = [50.0]
    
    while not globals()["__stop"]:
        try:
            # Screen periodically (not every poll!)
            rescreen_hours = config["screener"].get("rescreen_hours", 6)
            if config["screener"].get("enabled") and time.time() - ledger.last_screen_time >= rescreen_hours * 3600:
                try:
                    candidate, screen_info = screen_pair(api, config)
                    if candidate != active_pair:
                        active_pair = candidate
                        ledger.active_pair = active_pair
                        
                        # Reload candles for new pair
                        prices = api.history(active_pair, timeframe, slow + 2)
                        
                        # Recalculate RSI
                        rsi_values = []
                        for i in range(len(prices)):
                            if i >= 14:
                                rsi_values.append(calculate_rsi(prices[max(0, i-14):i+1]))
                            else:
                                rsi_values.append(50.0)
                        
                        ledger.last_screen_time = time.time()
                        print(f"\n✦ Re-screened: Switched to {active_pair.upper()} (score: {screen_info['score']:.2f})")
                        
                except Exception as e:
                    print(f"Screen error: {e}")
            
            # Get current price
            price = api.ticker(active_pair)
            
            # Update price series and recalculate RSI
            prices.append(price)
            prices = prices[-(slow + 2):]  # Keep limited window
            
            if len(prices) >= 14:
                recent_rsi = calculate_rsi(prices[-14:])
                rsi_values = rsi_values[-13:] + [recent_rsi]
            else:
                rsi_values.append(50.0)
            
            # Check protective exits FIRST (these never open positions)
            protective_exit = exit_reason(ledger, price, config)
            
            # Then evaluate entry signals
            decision = signal_from_prices(prices, int(config["strategy"]["fast_sma"]), 
                                         int(config["strategy"]["slow_sma"]), rsi_values)
            
            allowed, reason = can_trade(ledger, price, config)
            
            # Execute if both exit is free and entry is allowed
            if protective_exit:
                decision = "sell"
            
            if decision and allowed:
                result = execute_fill(ledger, decision, price, config, decision if decision == "sell" else "sma_crossover")
                if result:
                    reason_str = protective_exit if protective_exit else (decision if decision == "sell" else "sma_crossover")
                    print(f"{now()} | {decision.upper()} ({reason_str}) | Price: Rp {price:,.2f}")
            
            elif decision:
                print(f"{now()} | {decision.upper()} BLOCKED: {reason}")
            
            # Track and save state
            ledger.peak_equity = max(ledger.peak_equity, equity(ledger, price))
            
            if save_ledger(ledger):
                print(f"{now()} | Equity: Rp {equity(ledger, price):,.2f} | Highest: Rp {ledger.highest_price:,.2f}")
            
        except KeyboardInterrupt:
            print("\n\n🛑 Interrupted by user")
            break
        except Exception as e:
            print(f"{now()} | ERROR: {e}")
        
        # Sleep matching candle timeframe
        time.sleep(max(60, int(config["poll_seconds"])))
    
    print("\n✅ Bot stopped")
    if save_ledger(ledger):
        print("💾 State saved to state.json")
    else:
        print("❌ Could not save state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json", type=Path)
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    
    config = load_config(args.config)
    run(config, args.confirm_live)


if __name__ == "__main__":
    main()
