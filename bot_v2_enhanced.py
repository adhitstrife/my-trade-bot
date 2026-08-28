#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Indodax Trading Bot - Enhanced Version v2
Improvements based on critical audit feedback:
1. Volume filtering (higher minimum threshold)
2. SMA comparison (20/50 vs 10/30)
3. RSI condition filtering
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
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path.cwd()
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
STATE_FILE = DATA / "state.json"
TAPI_URL = "https://indodax.com/tapi"
TAPI_V2_URL = "https://api.indodax.com"
PUBLIC_TICKER = "https://indodax.com/api/ticker/{pair}"
LEGACY_PUBLIC_TICKER = "https://indodax.com/api/{pair}/ticker"
PUBLIC_SUMMARIES = "https://indodax.com/api/summaries"
HISTORY_URL = "https://indodax.com/tradingview/history_v2"

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_json(url: str, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = urlencode(data).encode() if data else None
    request_headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "IndodaxTradingBot/2.0 (enhanced; API client)",
    }
    request_headers.update(headers or {})
    req = Request(url, data=body, headers=request_headers, method="POST" if body else "GET")
    try:
        with urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"API request failed: HTTP {exc.code} {exc.reason}; {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"API request failed: {exc}") from exc


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
    if config["mode"] not in {"paper", "live"}:
        raise ValueError("mode must be 'paper' or 'live'")
    if config["strategy"]["fast_sma"] >= config["strategy"]["slow_sma"]:
        raise ValueError("fast_sma must be smaller than slow_sma")
    for key in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "trailing_activation_pct"):
        if key not in config["risk"] or not 0 < float(config["risk"][key]) < 1:
            raise ValueError(f"risk.{key} must be a decimal between 0 and 1")
    screener = config.get("screener", {})
    if screener.get("enabled") and not screener.get("allowlist"):
        raise ValueError("screener.allowlist must contain at least one pair")
    return config


class Indodax:
    def __init__(self) -> None:
        load_dotenv()
        self.key = os.getenv("INDODAX_API_KEY", "")
        self.secret = os.getenv("INDODAX_API_SECRET", "")

    def ticker(self, pair: str) -> float:
        pair = pair.lower()
        try:
            payload = request_json(PUBLIC_TICKER.format(pair=pair))
        except RuntimeError as primary_error:
            legacy_pair = f"{pair[:-3]}_{pair[-3:]}" if pair.endswith("idr") else pair
            try:
                payload = request_json(LEGACY_PUBLIC_TICKER.format(pair=legacy_pair))
            except RuntimeError as fallback_error:
                raise RuntimeError(f"Ticker unavailable. Primary: {primary_error}; fallback: {fallback_error}") from fallback_error
        try:
            return float(payload["ticker"]["last"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Unexpected ticker response: {payload}") from exc

    def summaries(self) -> dict[str, Any]:
        return request_json(PUBLIC_SUMMARIES)

    def history(self, pair: str, timeframe: str, bars: int) -> list[float]:
        minutes = int(timeframe) if timeframe.isdigit() else 60
        end = int(time.time())
        start = end - (minutes * 60 * (bars + 5))
        url = f"{HISTORY_URL}?{urlencode({'from': start, 'to': end, 'symbol': pair.upper(), 'tf': timeframe})}"
        payload = request_json(url)
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected history response: {payload}")
        return [float(candle["Close"]) for candle in payload if "Close" in candle]

    def private(self, method: str, **params: Any) -> dict[str, Any]:
        if not self.key or not self.secret:
            raise RuntimeError("Live mode needs INDODAX_API_KEY and INDODAX_API_SECRET in .env")
        payload = {"method": method, "timestamp": int(time.time() * 1000), "recvWindow": 5000, **params}
        encoded = urlencode(payload)
        signature = hmac.new(self.secret.encode(), encoded.encode(), hashlib.sha512).hexdigest()
        response = request_json(TAPI_URL, payload, {"Key": self.key, "Sign": signature, "Content-Type": "application/x-www-form-urlencoded"})
        if response.get("success") != 1:
            raise RuntimeError(f"Indodax {method} rejected: {response.get('error', response)}")
        return response["return"]

    def private_v2(self, method: str, path: str = "/api/v2/account", query_params: dict[str, Any] | None = None) -> dict[str, Any]:
        """TAPI v2 signed request: X-APIKEY header + HMAC-SHA256 on sorted query string."""
        if not self.key or not self.secret:
            raise RuntimeError("Live mode needs INDODAX_API_KEY and INDODAX_API_SECRET in .env")
        query_params = dict(query_params or {})
        query_params.setdefault("timestamp", int(time.time() * 1000))
        query_params.setdefault("recvWindow", 5000)
        sorted_items = sorted(query_params.items())
        query_string = urlencode(sorted_items)
        signature = hmac.new(self.secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
        headers = {
            "Accept": "application/json",
            "X-APIKEY": self.key,
            "Sign": signature,
        }
        url = f"{TAPI_V2_URL}{path}?{query_string}"
        response = request_json(url, headers=headers)
        return response

    def balances_v2(self) -> dict[str, float]:
        """Fetch balances via TAPI v2 (api.indodax.com /api/v2/account)."""
        info = self.private_v2("getInfo", path="/api/v2/account")
        balances: dict[str, float] = {}
        for bal in info.get("balances", []):
            try:
                balances[bal["asset"].lower()] = float(bal.get("free", 0) or 0)
            except (KeyError, TypeError, ValueError):
                continue
        return balances

    def balances(self) -> dict[str, float]:
        # Prefer TAPI v2; fall back to v1 only if v2 is unavailable.
        try:
            return self.balances_v2()
        except Exception:
            info = self.private("getInfo")
            return {k: float(v) for k, v in info.get("balance", {}).items()}

    def trade(self, pair: str, side: str, price: float, idr_amount: float = 0, asset_amount: float = 0) -> dict[str, Any]:
        params: dict[str, Any] = {"pair": pair, "type": side, "price": f"{price:.8f}"}
        if side == "buy":
            params["idr"] = f"{idr_amount:.0f}"
        else:
            params[pair.replace("idr", "")] = f"{asset_amount:.8f}"
        return self.private("trade", **params)


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
    last_screen_time: float = 0.0
    screen_events: list[dict[str, Any]] | None = None
    highest_price: float = 0.0

    def __post_init__(self) -> None:
        self.trades = self.trades or []
        self.screen_events = self.screen_events or []


def load_ledger(starting_idr: float) -> Ledger:
    if not STATE_FILE.exists():
        return Ledger(cash_idr=starting_idr, peak_equity=starting_idr, day_start_equity=starting_idr, day=datetime.now(timezone.utc).date().isoformat())
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return Ledger(**raw)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        backup = STATE_FILE.with_name(f"state-corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
        try:
            STATE_FILE.replace(backup)
            print(f"Invalid state file moved to {backup.name}: {exc}", file=sys.stderr)
        except OSError:
            print(f"Invalid state file ignored: {exc}", file=sys.stderr)
        return Ledger(cash_idr=starting_idr, peak_equity=starting_idr, day_start_equity=starting_idr, day=datetime.now(timezone.utc).date().isoformat())


def save_ledger(ledger: Ledger) -> bool:
    global _persistence_error
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(asdict(ledger), indent=2), encoding="utf-8")
        _persistence_error = None
        return True
    except OSError as exc:
        _persistence_error = str(exc)
        return False


def equity(ledger: Ledger, price: float) -> float:
    return ledger.cash_idr + ledger.asset * price


def roll_day(ledger: Ledger, price: float) -> None:
    day = datetime.now(timezone.utc).date().isoformat()
    if ledger.day != day:
        ledger.day, ledger.day_start_equity = day, equity(ledger, price)


def calculate_rsi(prices: list[float], period: int = 14) -> float:
    """Calculate RSI indicator."""
    if len(prices) < period + 1:
        return 50.0
    
    gains = []
    losses = []
    
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(change if change > 0 else 0)
        losses.append(abs(change) if change < 0 else 0)
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def signal_from_prices(prices: list[float], fast: int, slow: int, rsi_values: list[float] = None) -> str | None:
    if len(prices) < slow + 1:
        return None
    
    previous_fast, previous_slow = fmean(prices[-fast-1:-1]), fmean(prices[-slow-1:-1])
    current_fast, current_slow = fmean(prices[-fast:]), fmean(prices[-slow:])
    
    # RSI FILTER (new enhancement #4): Use RSI to filter entries
    # Don't buy when RSI is already very high (>70 = overbought)
    rsi_oversold = 0
    if rsi_values and len(rsi_values) >= 14:
        rsi_oversold = calculate_rsi(rsi_values)
    
    # Avoid buying if already overbought (>70)
    if rsi_oversold > 70:
        # Check if this is a trend-following buy (golden cross)
        # Only allow golden cross if momentum is still building
        if previous_fast <= previous_slow and current_fast > current_slow:
            # Golden cross but RSI high = risky, skip
            return None
        elif current_fast > current_slow and rsi_oversold < 65:  # Relaxed threshold
            return "buy"
        return None
    
    if previous_fast <= previous_slow and current_fast > current_slow:
        return "buy"
    if previous_fast >= previous_slow and current_fast < current_slow:
        return "sell"
    return None


def screen_pair(api: Indodax, config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Rank only explicit, liquid IDR pairs; never discover arbitrary tokens."""
    settings = config["screener"]
    allowlist = {pair.lower().replace("_", "") for pair in settings["allowlist"]}
    minimum_volume = float(settings["min_volume_idr"])
    maximum_spread = float(settings["max_spread_pct"])
    summaries = api.summaries()
    candidates: list[dict[str, Any]] = []
    
    for raw_pair, ticker in summaries.get("tickers", {}).items():
        pair = raw_pair.lower().replace("_", "")
        if pair not in allowlist or not pair.endswith("idr"):
            continue
        try:
            last, buy, sell = float(ticker["last"]), float(ticker["buy"]), float(ticker["sell"])
            volume = float(ticker["vol_idr"])
            previous = float(summaries.get("prices_24h", {}).get(pair, last))
            spread_pct = (sell - buy) / last if last else float("inf")
            momentum_pct = (last / previous - 1) * 100 if previous else 0.0
            
            # VOLUME FILTER (enhancement #2): Double minimum threshold
            min_required_volume = minimum_volume * 2.0
            if volume < min_required_volume:
                continue
                
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if volume < minimum_volume or spread_pct > maximum_spread or not math.isfinite(momentum_pct):
            continue
        score = momentum_pct + min(5.0, math.log10(max(volume / minimum_volume, 1.0))) - spread_pct * 100
        candidates.append({"pair": pair, "score": score, "volume_idr": volume, "spread_pct": spread_pct * 100, "momentum_24h_pct": momentum_pct})
    
    if not candidates:
        raise RuntimeError("Screener found no allowed pair meeting the volume/spread filters")
    winner = max(candidates, key=lambda item: item["score"])
    return winner["pair"], winner


def warm_prices(api: Indodax, pair: str, config: dict[str, Any]) -> tuple[list[float], list[float]]:
    slow = int(config["strategy"]["slow_sma"])
    timeframe = str(config.get("candle_timeframe", "60"))
    prices = api.history(pair, timeframe, slow + 2)
    if len(prices) < slow + 1:
        raise RuntimeError(f"Only received {len(prices)} candles; need {slow + 1}")
    
    # Calculate RSI values for all candles
    rsi_values = []
    for i in range(len(prices)):
        if i >= 14:
            rsi = calculate_rsi(prices[max(0, i-14):i+1])
            rsi_values.append(rsi)
        else:
            rsi_values.append(50.0)  # Neutral for insufficient data
    
    return prices, rsi_values


def fill_paper(ledger: Ledger, side: str, price: float, config: dict[str, Any], reason: str = "sma_crossover") -> dict[str, Any] | None:
    fee = float(config["fee_rate"])
    risk = config["risk"]
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
        quantity = ledger.asset
        if quantity <= 0:
            return None
        proceeds = quantity * price * (1 - fee)
        ledger.cash_idr += proceeds
        ledger.realized_pnl += proceeds - quantity * ledger.average_cost
        ledger.asset, ledger.average_cost, ledger.highest_price, amount = 0.0, 0.0, 0.0, quantity
    trade = {"time": now(), "mode": "paper", "side": side, "reason": reason, "price": price, "amount": amount, "asset_after": ledger.asset, "cash_after": ledger.cash_idr}
    ledger.trades.append(trade)
    return trade


def can_trade(ledger: Ledger, price: float, config: dict[str, Any]) -> tuple[bool, str]:
    roll_day(ledger, price)
    loss_limit = float(config["risk"]["max_daily_loss_pct"])
    if equity(ledger, price) < ledger.day_start_equity * (1 - loss_limit):
        return False, "daily loss limit reached"
    return True, ""


def exit_reason(ledger: Ledger, price: float, config: dict[str, Any]) -> str | None:
    """Return a protective sell reason; these checks never open a new position."""
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
    if ledger.highest_price >= activation and price <= ledger.highest_price * (1 - float(risk["trailing_stop_pct"])):
        return "trailing_stop"
    return None


def report(ledger: Ledger, price: float, config: dict[str, Any], title: str = "Bot report") -> str:
    value = equity(ledger, price)
    start = float(config["starting_idr"])
    pair = ledger.active_pair or config["pair"]
    lines = [title, f"Generated: {now()}", f"Mode: {config['mode']} | Pair: {pair.upper()}", f"Last price: Rp {price:,.2f}", f"Equity: Rp {value:,.2f}", f"P/L: Rp {value - start:,.2f} ({(value / start - 1) * 100:.2f}%)", f"Cash: Rp {ledger.cash_idr:,.2f} | Asset: {ledger.asset:.8f}", f"Realized P/L: Rp {ledger.realized_pnl:,.2f}", f"Trades: {len(ledger.trades)}"]
    if ledger.screen_events:
        last_screen = ledger.screen_events[-1]
        lines.append(f"Last screen: {last_screen['pair'].upper()} | score {last_screen['score']:.2f} | 24h momentum {last_screen['momentum_24h_pct']:.2f}% | spread {last_screen['spread_pct']:.3f}%")
    if ledger.trades:
        lines.append("Recent trades:")
        lines += [f"  {x['time']} | {x['mode']} {x['side']} ({x.get('reason', 'unknown')}) @ {x['price']:,.2f}" for x in ledger.trades[-5:]]
    return "\n".join(lines)


def write_report(text: str) -> Path | None:
    try:
        REPORTS.mkdir(parents=True, exist_ok=True)
        path = REPORTS / f"report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        path.write_text(text + "\n", encoding="utf-8")
        return path
    except OSError as exc:
        print(f"Report could not be saved: {exc}", file=sys.stderr)
        return None


def run(config: dict[str, Any], confirm_live: bool) -> None:
    if config["mode"] == "live" and not confirm_live:
        raise RuntimeError("Live mode requires --confirm-live. No order was sent.")
    api, ledger, prices, rsi_values = Indodax(), load_ledger(float(config["starting_idr"])), [], []
    persistence_available = save_ledger(ledger)
    if config["mode"] == "live" and not persistence_available:
        raise RuntimeError("Live mode requires working local state storage. Fix the Python/file permission issue first.")
    if not persistence_available:
        print("Warning: paper mode will run in memory only; state and reports cannot be saved until Python file access is fixed.", file=sys.stderr)
    keep_running = True
    signal.signal(signal.SIGINT, lambda *_: setattr(sys.modules[__name__], "_stop", True))
    globals()["__stop"] = False
    screener = config.get("screener", {})
    active_pair = ledger.active_pair or config["pair"].lower()
    if screener.get("enabled") and (config["mode"] == "paper" or not screener.get("paper_only", True)):
        try:
            active_pair, screen = screen_pair(api, config)
            ledger.active_pair, ledger.last_screen_time = active_pair, time.time()
            ledger.screen_events.append({"time": now(), **screen})
            print(f"Screen selected {active_pair.upper()}: score={screen['score']:.2f}, 24h momentum={screen['momentum_24h_pct']:.2f}%, spread={screen['spread_pct']:.3f}%")
        except Exception as exc:
            print(f"Startup screen failed; using {active_pair.upper()}: {exc}", file=sys.stderr)
    elif screener.get("enabled"):
        print("Screener is paper-only; live mode is using the configured pair.", file=sys.stderr)
    ledger.active_pair = active_pair
    try:
        prices, rsi_values = warm_prices(api, active_pair, config)
        print(f"Loaded {len(prices)} historical candles for {active_pair.upper()}.")
    except Exception as exc:
        print(f"Candle warm-up unavailable; accumulating live prices instead: {exc}", file=sys.stderr)
        # Start with neutral RSI values
        rsi_values = [50.0] * len(prices) if prices else []
    print(f"Started {config['mode']} bot for {active_pair.upper()}. Press Ctrl+C to stop.")
    while not globals()["__stop"]:
        try:
            interval = float(screener.get("rescreen_hours", 4)) * 3600
            if screener.get("enabled") and config["mode"] == "paper" and ledger.asset <= 0 and time.time() - ledger.last_screen_time >= interval:
                candidate, screen = screen_pair(api, config)
                ledger.last_screen_time = time.time()
                ledger.screen_events.append({"time": now(), **screen})
                if candidate != active_pair:
                    active_pair, ledger.active_pair = candidate, candidate
                    prices, rsi_values = warm_prices(api, active_pair, config)
                    print(f"Re-screen selected {active_pair.upper()}: score={screen['score']:.2f}")
            price = api.ticker(active_pair)
            prices.append(price)
            prices = prices[-(int(config["strategy"]["slow_sma"]) + 2):]
            
            # Update RSI values for recent prices
            if len(prices) >= 14:
                recent_rsi = calculate_rsi(prices[-14:])
                rsi_values = rsi_values[-13:] + [recent_rsi]  # Keep history
            else:
                rsi_values.append(50.0)
            
            decision = signal_from_prices(prices, int(config["strategy"]["fast_sma"]), int(config["strategy"]["slow_sma"]), rsi_values)
            allowed, reason = can_trade(ledger, price, config)
            if decision and allowed:
                if config["mode"] == "paper":
                    result = fill_paper(ledger, decision, price, config, decision if decision == "sell" else "sma_crossover")
                else:
                    result = execute_live(api, ledger, decision, price, {**config, "pair": active_pair}, decision if decision == "sell" else "sma_crossover")
                if result: 
                    print(f"{now()} {decision.upper()} executed ({decision}): {result}")
            elif decision:
                print(f"{now()} {decision.upper()} blocked: {reason}")
            ledger.peak_equity = max(ledger.peak_equity, equity(ledger, price))
            save_ledger(ledger)
            print(f"{now()} price=Rp {price:,.2f} equity=Rp {equity(ledger, price):,.2f}")
        except Exception as exc:
            print(f"{now()} cycle error: {exc}", file=sys.stderr)
        time.sleep(max(5, int(config["poll_seconds"])))
    if save_ledger(ledger):
        print("Stopped; state saved.")
    else:
        print("Stopped; paper state was not saved.")


def execute_live(api: Indodax, ledger: Ledger, side: str, price: float, config: dict[str, Any], reason: str = "sma_crossover") -> dict[str, Any] | None:
    balances = api.balances()
    base = config["pair"].lower().replace("idr", "")
    if side == "buy":
        spend = balances.get("idr", 0.0) * float(config["risk"]["max_position_pct"])
        if spend < float(config["risk"]["min_order_idr"]): return None
        result = api.trade(config["pair"], "buy", price, idr_amount=spend)
    else:
        amount = balances.get(base, 0.0)
        if amount <= 0: return None
        result = api.trade(config["pair"], "sell", price, asset_amount=amount)
    ledger.trades.append({"time": now(), "mode": "live", "side": side, "reason": reason, "price": price, "exchange_response": result})
    return result


def parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except ValueError:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))


def backtest(config: dict[str, Any], candles_path: Path) -> None:
    with candles_path.open(newline="", encoding="utf-8") as fh:
        candles = list(csv.DictReader(fh))
    if not candles or not {"timestamp", "close"}.issubset(candles[0]):
        raise ValueError("CSV needs at least timestamp and close columns")
    ledger, prices = Ledger(cash_idr=float(config["starting_idr"]), peak_equity=float(config["starting_idr"]), day_start_equity=float(config["starting_idr"]), day="backtest"), []
    rsi_values = []
    for candle in candles:
        price = float(candle["close"]); prices.append(price)
        if len(prices) >= 14:
            rsi = calculate_rsi(prices[-14:])
            rsi_values.append(rsi)
        else:
            rsi_values.append(50.0)
        
        decision = signal_from_prices(prices, int(config["strategy"]["fast_sma"]), int(config["strategy"]["slow_sma"]), rsi_values)
        if decision: 
            fill_paper(ledger, decision, price, config, decision if decision == "sell" else "sma_crossover")
        ledger.peak_equity = max(ledger.peak_equity, equity(ledger, price))
    final_price = float(candles[-1]["close"])
    text = report(ledger, final_price, config, f"Backtest report ({len(candles)} candles)")
    path = write_report(text)
    print(text)
    if path: print(f"Saved: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "report", "backtest"))
    parser.add_argument("--config", default="config.json", type=Path)
    parser.add_argument("--candles", type=Path, help="CSV for backtest")
    parser.add_argument("--confirm-live", action="store_true", help="required to submit live orders")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == "run": run(config, args.confirm_live)
    elif args.command == "report":
        api, ledger = Indodax(), load_ledger(float(config["starting_idr"]))
        price = api.ticker(ledger.active_pair or config["pair"])
        text, path = report(ledger, price, config), None
        if config["mode"] == "live":
            try: text += "\nExchange balances: " + json.dumps(api.balances())
            except Exception as exc: text += f"\nExchange balance unavailable: {exc}"
        path = write_report(text); print(text)
        if path: print(f"Saved: {path}")
    else:
        if not args.candles: parser.error("backtest requires --candles FILE")
        backtest(config, args.candles)


if __name__ == "__main__":
    main()
