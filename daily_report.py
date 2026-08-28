#!/usr/bin/env python3
"""
Indodax Daily Report Generator - output goes to Discord via cron delivery.
Generates a daily summary: equity, P&L, trades, positions, balances.
Prints the report to stdout (cron delivers stdout verbatim to Discord).
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure we can import the bot module
BOT_DIR = Path("/opt/data/my-trade-bot")
os.chdir(BOT_DIR)
sys.path.insert(0, str(BOT_DIR))

import bot as botmod


def load_dotenv_manual():
    """Load .env manually."""
    env_path = BOT_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def fmt_rp(value: float) -> str:
    return f"Rp {value:,.0f}"


def main():
    load_dotenv_manual()
    config_path = BOT_DIR / "config.json"
    config = botmod.load_config(config_path)
    ledger = botmod.load_ledger(float(config["starting_idr"]))
    api = botmod.Indodax()

    pair = ledger.active_pair or config["pair"]

    lines = []
    lines.append("📊 **INDO DAX BOT - DAILY REPORT**")
    lines.append(f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC | {pair.upper()}")
    lines.append("")

    # --- Bot status ---
    import subprocess
    running = subprocess.run(["pgrep", "-f", "bot.py run --config config.json"],
                             capture_output=True).returncode == 0
    lines.append(f"🤖 Bot status: {'🟢 RUNNING' if running else '🔴 DOWN'}")

    # --- Equity / P&L ---
    try:
        price = api.ticker(pair)
        value = botmod.equity(ledger, price)
        start = float(config["starting_idr"])
        pnl = value - start
        pnl_pct = (value / start - 1) * 100 if start else 0

        lines.append("")
        lines.append("💰 **PORTFOLIO**")
        lines.append(f"• Last price: {fmt_rp(price)}")
        lines.append(f"• Equity: {fmt_rp(value)}")
        lines.append(f"• P/L: **{fmt_rp(pnl)} ({pnl_pct:+.2f}%)**")
        lines.append(f"• Cash: {fmt_rp(ledger.cash_idr)} | Asset: {ledger.asset:.8f}")
        lines.append(f"• Realized P/L: {fmt_rp(ledger.realized_pnl)}")
    except Exception as exc:
        lines.append(f"⚠️ Price/equity unavailable: {exc}")

    # --- Open position ---
    if ledger.asset > 0 and ledger.average_cost > 0:
        try:
            cur_price = api.ticker(pair)
            pos_pnl_pct = (cur_price - ledger.average_cost) / ledger.average_cost * 100
            lines.append("")
            lines.append(f"🎯 **OPEN POSITION: {pair.upper()}**")
            lines.append(f"• Entry: {fmt_rp(ledger.average_cost)}")
            lines.append(f"• Current: {fmt_rp(cur_price)}")
            lines.append(f"• Position P/L: {pos_pnl_pct:+.2f}%")
            lines.append(f"• Highest seen: {fmt_rp(ledger.highest_price)}")
        except Exception:
            pass
    else:
        lines.append("")
        lines.append("🎯 Open position: **None (flat)**")

    # --- Recent trades ---
    if ledger.trades:
        lines.append("")
        lines.append("📈 **RECENT TRADES**")
        for t in ledger.trades[-5:]:
            reason = t.get("reason", "unknown")
            side = t.get("side", "?").upper()
            ts = t.get("time", "")[11:19] if t.get("time") else ""
            lines.append(f"• {ts} | {side} ({reason}) @ {fmt_rp(t['price'])}")
    else:
        lines.append("")
        lines.append("📈 Trades: **none yet**")

    # --- Risk state ---
    lines.append("")
    lines.append("🛡️ **RISK SETTINGS**")
    r = config["risk"]
    lines.append(f"• Stop loss: {float(r['stop_loss_pct'])*100:.0f}% | Take profit: {float(r['take_profit_pct'])*100:.0f}%")
    lines.append(f"• Trailing: {float(r['trailing_stop_pct'])*100:.1f}% (activation +{float(r['trailing_activation_pct'])*100:.0f}%)")

    # --- Exchange balances (TAPI v2) ---
    try:
        balances = api.balances_v2()
        non_zero = {k: v for k, v in balances.items() if v > 0}
        if non_zero:
            lines.append("")
            lines.append("🏦 **EXCHANGE BALANCES**")
            for asset, amt in sorted(non_zero.items()):
                lines.append(f"• {asset.upper()}: {amt:.8f}")
    except Exception as exc:
        lines.append(f"\n⚠️ Balances unavailable: {exc}")

    lines.append("")
    lines.append("— Generated automatically by Indodax trading bot 📉")

    print("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        print(f"❌ Daily report failed: {exc}")
        traceback.print_exc()
        sys.exit(1)
