#!/usr/bin/env python3
"""Conservative single-pair Indodax spot bot: paper, live, backtest, report."""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import hmac
import json
import math
import os
import signal
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# The bot is intended to be run from its project folder.  Using the process
# working directory avoids path conversion bugs in Windows compatibility
# Python builds that can turn __file__ into an invalid C:\\... path.
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
_persistence_error: str | None = None


@dataclass(frozen=True)
class Candle:
    """A completed OHLC candle used for signals and backtests."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_json(url: str, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = urlencode(data).encode() if data else None
    request_headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "IndodaxTradingBot/1.0 (personal-use; API client)",
    }
    request_headers.update(headers or {})
    req = Request(url, data=body, headers=request_headers, method="POST" if body else "GET")
    try:
        with urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        # The response body is valuable when an exchange/WAF rejects a request.
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
    if float(config.get("execution_slippage_pct", 0)) < 0 or float(config.get("execution_slippage_pct", 0)) >= 1:
        raise ValueError("execution_slippage_pct must be between 0 and 1")
    strategy = config["strategy"]
    if int(strategy.get("trend_sma", 0)) and int(strategy["slow_sma"]) >= int(strategy["trend_sma"]):
        raise ValueError("trend_sma must be larger than slow_sma")
    if float(strategy.get("min_adx", 0)) < 0 or float(strategy.get("min_sma_separation_pct", 0)) < 0:
        raise ValueError("strategy filters cannot be negative")
    if not 0 < config["risk"]["max_position_pct"] <= 1:
        raise ValueError("max_position_pct must be between 0 and 1")
    for key in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "trailing_activation_pct", "max_risk_per_trade_pct"):
        if key not in config["risk"] or not 0 < float(config["risk"][key]) < 1:
            raise ValueError(f"risk.{key} must be a decimal between 0 and 1")
    for key in ("max_entries_per_day", "cooldown_candles"):
        if int(config["risk"].get(key, 0)) < 0:
            raise ValueError(f"risk.{key} cannot be negative")
    screener = config.get("screener", {})
    if screener.get("enabled") and not screener.get("allowlist"):
        raise ValueError("screener.allowlist must contain at least one pair")
    return config


class Indodax:
    def __init__(self) -> None:
        load_dotenv()
        self.key = os.getenv("INDODAX_API_KEY", "")
        self.secret = os.getenv("INDODAX_API_SECRET", "")

    def ticker_data(self, pair: str) -> dict[str, float]:
        pair = pair.lower()
        try:
            payload = request_json(PUBLIC_TICKER.format(pair=pair))
        except RuntimeError as primary_error:
            # Some Indodax edge locations still serve the older pair-path route.
            legacy_pair = f"{pair[:-3]}_{pair[-3:]}" if pair.endswith("idr") else pair
            try:
                payload = request_json(LEGACY_PUBLIC_TICKER.format(pair=legacy_pair))
            except RuntimeError as fallback_error:
                raise RuntimeError(f"Ticker unavailable. Primary: {primary_error}; fallback: {fallback_error}") from fallback_error
        try:
            ticker = payload["ticker"]
            return {
                "last": float(ticker["last"]),
                "bid": float(ticker.get("buy") or ticker["last"]),
                "ask": float(ticker.get("sell") or ticker["last"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Unexpected ticker response: {payload}") from exc

    def ticker(self, pair: str) -> float:
        return self.ticker_data(pair)["last"]

    def summaries(self) -> dict[str, Any]:
        return request_json(PUBLIC_SUMMARIES)

    def history(self, pair: str, timeframe: str, bars: int) -> list[Candle]:
        minutes = int(timeframe) if timeframe.isdigit() else 60
        end = int(time.time())
        start = end - (minutes * 60 * (bars + 5))
        url = f"{HISTORY_URL}?{urlencode({'from': start, 'to': end, 'symbol': pair.upper(), 'tf': timeframe})}"
        payload = request_json(url)
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected history response: {payload}")
        candles: list[Candle] = []
        for candle in payload:
            try:
                timestamp = int(float(candle.get("Time", candle.get("time", candle.get("timestamp")))))
                candles.append(Candle(
                    timestamp=timestamp,
                    open=float(candle.get("Open", candle["Close"])),
                    high=float(candle.get("High", candle["Close"])),
                    low=float(candle.get("Low", candle["Close"])),
                    close=float(candle["Close"]),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        # TradingView responses commonly include the in-progress candle.  It
        # must never be used for a crossover signal because it can repaint.
        return candles[:-1]

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
    last_candle_time: int = 0
    last_entry_candle_time: int = 0
    entries_today: int = 0

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
        # Preserve a partial state file from an interrupted first run, rather
        # than preventing the bot from starting forever.
        backup = STATE_FILE.with_name(f"state-corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
        try:
            STATE_FILE.replace(backup)
            print(f"Invalid state file moved to {backup.name}: {exc}", file=sys.stderr)
        except OSError:
            print(f"Invalid state file ignored: {exc}", file=sys.stderr)
        return Ledger(cash_idr=starting_idr, peak_equity=starting_idr, day_start_equity=starting_idr, day=datetime.now(timezone.utc).date().isoformat())


def save_ledger(ledger: Ledger) -> bool:
    # Create the runtime folder tree on the first run; the bot must not require
    # a manually created data/ directory.
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


def roll_day(ledger: Ledger, price: float, day: str | None = None) -> None:
    day = day or datetime.now(timezone.utc).date().isoformat()
    if ledger.day != day:
        ledger.day, ledger.day_start_equity, ledger.entries_today = day, equity(ledger, price), 0


def signal_from_prices(prices: list[float], fast: int, slow: int, min_separation_pct: float = 0.0) -> str | None:
    if len(prices) < slow + 1:
        return None
    previous_fast, previous_slow = fmean(prices[-fast-1:-1]), fmean(prices[-slow-1:-1])
    current_fast, current_slow = fmean(prices[-fast:]), fmean(prices[-slow:])
    separation = abs(current_fast - current_slow) / current_slow if current_slow else 0.0
    if previous_fast <= previous_slow and current_fast > current_slow and separation >= min_separation_pct:
        return "buy"
    if previous_fast >= previous_slow and current_fast < current_slow:
        return "sell"
    return None


def calculate_adx(candles: list[Candle], period: int = 14) -> float:
    """Return a simple, deterministic ADX approximation for a closed-candle window."""
    if len(candles) < period * 2 + 1:
        return 0.0
    tr_values, plus_dm, minus_dm = [], [], []
    for previous, current in zip(candles[:-1], candles[1:]):
        tr_values.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
        up_move, down_move = current.high - previous.high, previous.low - current.low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    dx_values: list[float] = []
    for index in range(period - 1, len(tr_values)):
        tr = sum(tr_values[index - period + 1:index + 1])
        if tr <= 0:
            dx_values.append(0.0)
            continue
        plus_di = 100 * sum(plus_dm[index - period + 1:index + 1]) / tr
        minus_di = 100 * sum(minus_dm[index - period + 1:index + 1]) / tr
        denominator = plus_di + minus_di
        dx_values.append(100 * abs(plus_di - minus_di) / denominator if denominator else 0.0)
    return fmean(dx_values[-period:]) if len(dx_values) >= period else 0.0


def entry_filters(candles: list[Candle], config: dict[str, Any]) -> tuple[bool, str]:
    """Require an established BTC trend before accepting a crossover entry."""
    strategy = config["strategy"]
    closes = [candle.close for candle in candles]
    trend_period = int(strategy.get("trend_sma", 0))
    if trend_period and len(closes) < trend_period + 1:
        return False, "insufficient candles for trend filter"
    if trend_period:
        trend_now = fmean(closes[-trend_period:])
        trend_previous = fmean(closes[-trend_period-1:-1])
        if closes[-1] <= trend_now or trend_now <= trend_previous:
            return False, "higher trend filter not met"
    min_adx = float(strategy.get("min_adx", 0))
    if min_adx:
        adx = calculate_adx(candles, int(strategy.get("adx_period", 14)))
        if adx < min_adx:
            return False, f"ADX {adx:.1f} below {min_adx:.1f}"
    return True, ""


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
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if volume < minimum_volume or spread_pct > maximum_spread or not math.isfinite(momentum_pct):
            continue
        # Momentum is only a ranking input; liquidity rewards are capped so a
        # large volume cannot mask a poor spread or extreme one-day move.
        score = momentum_pct + min(5.0, math.log10(max(volume / minimum_volume, 1.0))) - spread_pct * 100
        candidates.append({"pair": pair, "score": score, "volume_idr": volume, "spread_pct": spread_pct * 100, "momentum_24h_pct": momentum_pct})
    if not candidates:
        raise RuntimeError("Screener found no allowed pair meeting the volume/spread filters")
    winner = max(candidates, key=lambda item: item["score"])
    return winner["pair"], winner


def warm_candles(api: Indodax, pair: str, config: dict[str, Any]) -> list[Candle]:
    strategy = config["strategy"]
    required = max(int(strategy["slow_sma"]), int(strategy.get("trend_sma", 0)), int(strategy.get("adx_period", 14)) * 2 + 1)
    timeframe = str(config.get("candle_timeframe", "60"))
    candles = api.history(pair, timeframe, required + 3)
    if len(candles) < required + 1:
        raise RuntimeError(f"Only received {len(candles)} closed candles; need {required + 1}")
    return candles[-(required + 2):]


def fill_paper(ledger: Ledger, side: str, price: float, config: dict[str, Any], reason: str = "sma_crossover", trade_time: str | None = None) -> dict[str, Any] | None:
    fee = float(config["fee_rate"])
    risk = config["risk"]
    slippage = float(config.get("execution_slippage_pct", 0.001))
    fill_price = price * (1 + slippage if side == "buy" else 1 - slippage)
    if side == "buy":
        stop_distance = float(risk["stop_loss_pct"])
        risk_budget = equity(ledger, price) * float(risk["max_risk_per_trade_pct"])
        risk_limited_spend = risk_budget / stop_distance if stop_distance else ledger.cash_idr
        spend = min(ledger.cash_idr * float(risk["max_position_pct"]), risk_limited_spend, ledger.cash_idr)
        if spend < float(risk["min_order_idr"]):
            return None
        quantity = spend * (1 - fee) / fill_price
        old_value = ledger.asset * ledger.average_cost
        ledger.cash_idr -= spend
        ledger.asset += quantity
        ledger.average_cost = (old_value + spend) / ledger.asset
        ledger.highest_price = fill_price
        ledger.last_entry_candle_time = ledger.last_candle_time
        ledger.entries_today += 1
        amount = spend
        net_pnl = None
    else:
        quantity = ledger.asset
        if quantity <= 0:
            return None
        proceeds = quantity * fill_price * (1 - fee)
        net_pnl = proceeds - quantity * ledger.average_cost
        ledger.cash_idr += proceeds
        ledger.realized_pnl += net_pnl
        ledger.asset, ledger.average_cost, ledger.highest_price, amount = 0.0, 0.0, 0.0, quantity
    trade = {"time": trade_time or now(), "mode": "paper", "side": side, "reason": reason, "signal_price": price, "price": fill_price, "amount": amount, "fee_rate": fee, "slippage_pct": slippage, "net_pnl": net_pnl, "asset_after": ledger.asset, "cash_after": ledger.cash_idr}
    ledger.trades.append(trade)
    return trade


def can_trade(ledger: Ledger, price: float, config: dict[str, Any], day: str | None = None) -> tuple[bool, str]:
    roll_day(ledger, price, day)
    loss_limit = float(config["risk"]["max_daily_loss_pct"])
    if equity(ledger, price) < ledger.day_start_equity * (1 - loss_limit):
        return False, "daily loss limit reached"
    max_entries = int(config["risk"].get("max_entries_per_day", 0))
    if max_entries and ledger.entries_today >= max_entries:
        return False, "maximum daily entries reached"
    cooldown = int(config["risk"].get("cooldown_candles", 0))
    if cooldown and ledger.last_entry_candle_time:
        minutes = int(str(config.get("candle_timeframe", "60")))
        elapsed = ledger.last_candle_time - ledger.last_entry_candle_time
        if elapsed < cooldown * minutes * 60:
            return False, "entry cooldown active"
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
    if config["mode"] == "live" and os.getenv("ALLOW_LIVE_TRADING") != "YES":
        raise RuntimeError("Live mode is locked. Set ALLOW_LIVE_TRADING=YES only after exchange reconciliation has been verified.")
    api, ledger, candles = Indodax(), load_ledger(float(config["starting_idr"])), []
    persistence_available = save_ledger(ledger)
    if config["mode"] == "live" and not persistence_available:
        raise RuntimeError("Live mode requires working local state storage. Fix the Python/file permission issue first.")
    if not persistence_available:
        print("Warning: paper mode will run in memory only; state and reports cannot be saved until Python file access is fixed.", file=sys.stderr)
    keep_running = True
    signal.signal(signal.SIGINT, lambda *_: setattr(sys.modules[__name__], "_stop", True))
    globals()["_stop"] = False
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
        candles = warm_candles(api, active_pair, config)
        ledger.last_candle_time = candles[-1].timestamp
        print(f"Loaded {len(candles)} closed historical candles for {active_pair.upper()}.")
    except Exception as exc:
        print(f"Candle warm-up unavailable; entries are disabled until closed candles are available: {exc}", file=sys.stderr)
    print(f"Started {config['mode']} bot for {active_pair.upper()}. Press Ctrl+C to stop.")
    while not globals()["_stop"]:
        try:
            # Re-screen only when flat: changing a pair while holding an asset
            # would orphan the existing position from its sell logic.
            interval = float(screener.get("rescreen_hours", 4)) * 3600
            if screener.get("enabled") and config["mode"] == "paper" and ledger.asset <= 0 and time.time() - ledger.last_screen_time >= interval:
                candidate, screen = screen_pair(api, config)
                ledger.last_screen_time = time.time()
                ledger.screen_events.append({"time": now(), **screen})
                if candidate != active_pair:
                    active_pair, ledger.active_pair = candidate, candidate
                    candles = warm_candles(api, active_pair, config)
                    ledger.last_candle_time = candles[-1].timestamp
                    print(f"Re-screen selected {active_pair.upper()}: score={screen['score']:.2f}")
            quote = api.ticker_data(active_pair)
            price = quote["last"]
            protective_exit = exit_reason(ledger, price, config)
            decision = "sell" if protective_exit else None
            latest_candles = warm_candles(api, active_pair, config)
            if latest_candles and latest_candles[-1].timestamp > ledger.last_candle_time:
                candles = latest_candles
                ledger.last_candle_time = candles[-1].timestamp
                closes = [candle.close for candle in candles]
                signal = signal_from_prices(
                    closes,
                    int(config["strategy"]["fast_sma"]),
                    int(config["strategy"]["slow_sma"]),
                    float(config["strategy"].get("min_sma_separation_pct", 0)),
                )
                if signal == "buy":
                    eligible, filter_reason = entry_filters(candles, config)
                    decision = "buy" if eligible else None
                    if not eligible:
                        print(f"{now()} BUY blocked: {filter_reason}")
                else:
                    decision = signal
                print(f"Closed candle {candles[-1].timestamp} processed for {active_pair.upper()}.")
            allowed, reason = can_trade(ledger, price, config)
            # An exit reduces exposure, so it remains available even if the
            # daily-loss guard has blocked new entries.
            if decision == "sell":
                allowed = True
            if decision and allowed:
                if config["mode"] == "paper":
                    # Use bid for sells and ask for buys; fill_paper adds a
                    # conservative configurable slippage assumption.
                    execution_price = quote["ask"] if decision == "buy" else quote["bid"]
                    result = fill_paper(ledger, decision, execution_price, config, protective_exit or "sma_crossover")
                else:
                    result = execute_live(api, ledger, decision, price, {**config, "pair": active_pair}, protective_exit or "sma_crossover")
                if result: print(f"{now()} {decision.upper()} executed ({protective_exit or 'sma_crossover'}): {result}")
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
    # The order is recorded as a submission.  No ledger position is changed
    # until a future exchange-reconciliation implementation confirms fills.
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
    ledger.trades.append({"time": now(), "mode": "live_submission", "side": side, "reason": reason, "price": price, "exchange_response": result})
    return result


def parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except ValueError:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))


def performance_metrics(ledger: Ledger, final_price: float, from_timestamp: int | None = None) -> dict[str, float | int]:
    closed = [trade for trade in ledger.trades if trade["side"] == "sell" and trade.get("net_pnl") is not None]
    if from_timestamp is not None:
        closed = [trade for trade in closed if parse_timestamp(str(trade["time"])).timestamp() >= from_timestamp]
    wins = [float(trade["net_pnl"]) for trade in closed if float(trade["net_pnl"]) > 0]
    losses = [float(trade["net_pnl"]) for trade in closed if float(trade["net_pnl"]) <= 0]
    gross_wins, gross_losses = sum(wins), abs(sum(losses))
    return {
        "closed_positions": len(closed),
        "wins": len(wins),
        "win_rate_pct": len(wins) / len(closed) * 100 if closed else 0.0,
        "profit_factor": gross_wins / gross_losses if gross_losses else (float("inf") if gross_wins else 0.0),
        "expectancy_idr": sum(wins + losses) / len(closed) if closed else 0.0,
        "return_pct": sum(wins + losses) / ledger.day_start_equity * 100 if ledger.day_start_equity else 0.0,
        "net_pnl": sum(wins + losses),
    }


def backtest(config: dict[str, Any], candles_path: Path, emit: bool = True, metric_from_timestamp: int | None = None) -> dict[str, float | int]:
    with candles_path.open(newline="", encoding="utf-8") as fh:
        candles = list(csv.DictReader(fh))
    required = {"timestamp", "open", "high", "low", "close"}
    if not candles or not required.issubset(candles[0]):
        raise ValueError("CSV needs timestamp, open, high, low, and close columns")
    ledger, prices, candle_history = Ledger(cash_idr=float(config["starting_idr"]), peak_equity=float(config["starting_idr"]), day_start_equity=float(config["starting_idr"]), day="backtest"), [], []
    pending_side: str | None = None
    for row in candles:
        candle = Candle(int(float(row["timestamp"])), float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
        ledger.last_candle_time = candle.timestamp
        candle_time = datetime.fromtimestamp(candle.timestamp, tz=timezone.utc).isoformat(timespec="seconds")
        # Signals from the prior close fill no earlier than this candle open.
        if pending_side:
            fill_paper(ledger, pending_side, candle.open, config, "sma_crossover", candle_time)
            pending_side = None
        # Stops are evaluated within the candle. If both targets could be hit,
        # use the adverse result instead of choosing the favorable path.
        if ledger.asset > 0:
            entry = ledger.average_cost
            ledger.highest_price = max(ledger.highest_price, candle.high)
            stop = entry * (1 - float(config["risk"]["stop_loss_pct"]))
            target = entry * (1 + float(config["risk"]["take_profit_pct"]))
            trail = ledger.highest_price * (1 - float(config["risk"]["trailing_stop_pct"]))
            trigger_price, reason = None, None
            if candle.low <= stop:
                trigger_price, reason = stop, "stop_loss"
            elif candle.high >= target:
                trigger_price, reason = target, "take_profit"
            elif ledger.highest_price >= entry * (1 + float(config["risk"]["trailing_activation_pct"])) and candle.low <= trail:
                trigger_price, reason = trail, "trailing_stop"
            if trigger_price is not None:
                fill_paper(ledger, "sell", trigger_price, config, reason, candle_time)
        prices.append(candle.close)
        prices = prices[-(int(config["strategy"]["slow_sma"]) + 2):]
        candle_history.append(candle)
        required_history = max(int(config["strategy"].get("trend_sma", 0)), int(config["strategy"].get("adx_period", 14)) * 2 + 1) + 1
        filter_window = candle_history[-required_history:]
        if ledger.asset <= 0:
            allowed, _ = can_trade(ledger, candle.close, config, candle_time[:10])
            signal = signal_from_prices(prices, int(config["strategy"]["fast_sma"]), int(config["strategy"]["slow_sma"]), float(config["strategy"].get("min_sma_separation_pct", 0)))
            if allowed and signal == "buy":
                eligible, _ = entry_filters(filter_window, config)
                if eligible:
                    pending_side = "buy"
        elif signal_from_prices(prices, int(config["strategy"]["fast_sma"]), int(config["strategy"]["slow_sma"]), float(config["strategy"].get("min_sma_separation_pct", 0))) == "sell":
            pending_side = "sell"
        ledger.peak_equity = max(ledger.peak_equity, equity(ledger, candle.close))
    final_price = float(candles[-1]["close"])
    metrics = performance_metrics(ledger, final_price, metric_from_timestamp)
    if emit:
        text = report(ledger, final_price, config, f"Backtest report ({len(candles)} candles)")
        factor = f"{metrics['profit_factor']:.2f}" if math.isfinite(float(metrics["profit_factor"])) else "∞"
        text += (f"\nClosed positions: {metrics['closed_positions']} | Win rate: {metrics['win_rate_pct']:.1f}%"
                 f" | Profit factor: {factor} | Expectancy: Rp {metrics['expectancy_idr']:,.2f}")
        path = write_report(text)
        print(text)
        if path: print(f"Saved: {path}")
    return metrics


def _write_candle_rows(rows: list[dict[str, str]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=("timestamp", "open", "high", "low", "close"))
        writer.writeheader()
        writer.writerows(rows)


def walk_forward_optimize(config: dict[str, Any], candles_path: Path) -> Path:
    """Select a BTC-only configuration without using the final holdout period."""
    with candles_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if len(rows) < 500:
        raise ValueError("Walk-forward optimisation needs at least 500 candles")
    cutoff = int(len(rows) * 0.8)
    validation_windows = [(int(len(rows) * 0.4), int(len(rows) * 0.6)), (int(len(rows) * 0.6), cutoff)]
    candidates: list[dict[str, Any]] = []
    for fast, slow in ((10, 30), (15, 40), (20, 50)):
        for min_adx in (0, 18, 22):
            for separation in (0.0, 0.0005):
                for cooldown in (6, 12):
                    candidate = copy.deepcopy(config)
                    candidate["pair"] = "btcidr"
                    candidate["screener"]["enabled"] = False
                    candidate["strategy"].update({"fast_sma": fast, "slow_sma": slow, "trend_sma": slow * 2, "min_adx": min_adx, "min_sma_separation_pct": separation})
                    candidate["risk"].update({"cooldown_candles": cooldown, "max_entries_per_day": 2})
                    candidates.append(candidate)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir) / "candles.csv"
        for candidate in candidates:
            validation: list[dict[str, float | int]] = []
            for start, end in validation_windows:
                # Each validation begins with all preceding candles so that the
                # indicators warm up, but score only the new window via an
                # independent run over that window plus its required history.
                history_start = max(0, start - 250)
                _write_candle_rows(rows[history_start:end], temp_path)
                validation.append(backtest(candidate, temp_path, emit=False, metric_from_timestamp=int(float(rows[start]["timestamp"]))))
            closed = sum(int(metric["closed_positions"]) for metric in validation)
            average_return = fmean(float(metric["return_pct"]) for metric in validation)
            average_pf = fmean(float(metric["profit_factor"]) for metric in validation if math.isfinite(float(metric["profit_factor"]))) if any(math.isfinite(float(metric["profit_factor"])) for metric in validation) else 0.0
            # Reject fragile candidates with too little evidence; remaining
            # candidates are ranked by return after costs, then profit factor.
            score = average_return + min(2.0, average_pf) * 0.1 if closed >= 3 else -999.0
            results.append({"config": candidate, "validation": validation, "closed_positions": closed, "average_return_pct": average_return, "average_profit_factor": average_pf, "score": score})
        ranked = sorted(results, key=lambda item: float(item["score"]), reverse=True)
        selected = ranked[0]
        _write_candle_rows(rows[cutoff - 250:], temp_path)
        holdout = backtest(selected["config"], temp_path, emit=False, metric_from_timestamp=int(float(rows[cutoff]["timestamp"])))
    REPORTS.mkdir(parents=True, exist_ok=True)
    output = REPORTS / f"walk-forward-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    viable = float(selected["average_return_pct"]) > 0 and float(selected["average_profit_factor"]) > 1 and int(selected["closed_positions"]) >= 3
    output.write_text(json.dumps({
        "candles": len(rows), "validation_windows": validation_windows,
        "selected": selected, "holdout": holdout, "viable": viable,
        "top_candidates": ranked[:5],
        "warning": "Selected by validation only. Holdout must be positive before adopting parameters."
    }, indent=2), encoding="utf-8")
    print(f"Walk-forward report saved: {output}")
    print(f"Selected: SMA {selected['config']['strategy']['fast_sma']}/{selected['config']['strategy']['slow_sma']}, ADX >= {selected['config']['strategy']['min_adx']}, cooldown {selected['config']['risk']['cooldown_candles']} candles")
    print(f"Holdout: {holdout['closed_positions']} positions | WR {holdout['win_rate_pct']:.1f}% | PF {holdout['profit_factor']:.2f} | return {holdout['return_pct']:+.2f}%")
    print("Candidate is viable for paper testing." if viable else "No candidate passed validation; keep the existing configuration and collect more data.")
    return output


def download_candles(config: dict[str, Any], days: int, output: Path) -> Path:
    """Download public completed candles in the CSV format used by backtest."""
    if days <= 0:
        raise ValueError("days must be positive")
    timeframe = str(config.get("candle_timeframe", "60"))
    minutes = int(timeframe)
    bars = math.ceil(days * 24 * 60 / minutes)
    api = Indodax()
    candles = api.history(config["pair"], timeframe, bars)
    if len(candles) < int(config["strategy"]["slow_sma"]) + 2:
        raise RuntimeError(f"Only received {len(candles)} closed candles; not enough for a backtest")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=("timestamp", "open", "high", "low", "close"))
        writer.writeheader()
        writer.writerows(asdict(candle) for candle in candles)
    print(f"Downloaded {len(candles)} completed {timeframe}m candles to {output}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "report", "backtest", "download-candles", "optimize"))
    parser.add_argument("--config", default="config.json", type=Path)
    parser.add_argument("--candles", type=Path, help="CSV for backtest")
    parser.add_argument("--days", type=int, default=180, help="history length for download-candles")
    parser.add_argument("--output", type=Path, default=Path("data/btcidr-candles.csv"), help="output CSV for download-candles")
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
    elif args.command == "backtest":
        if not args.candles: parser.error("backtest requires --candles FILE")
        backtest(config, args.candles)
    elif args.command == "optimize":
        if not args.candles: parser.error("optimize requires --candles FILE")
        walk_forward_optimize(config, args.candles)
    else:
        download_candles(config, args.days, args.output)


if __name__ == "__main__":
    main()
