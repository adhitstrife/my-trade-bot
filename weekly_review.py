#!/usr/bin/env python3
"""
Indodax Trading Bot - Weekly Performance Report Generator
Generates comprehensive report every Monday morning for review.
"""

import json
import os
from pathlib import Path
from datetime import datetime, timezone

BOT_DIR = Path(__file__).resolve().parent
os.chdir(BOT_DIR)

with open(BOT_DIR / "data/state.json") as f:
    state = json.load(f)

with open(BOT_DIR / "config.json") as f:
    config = json.load(f)

trades = state.get('trades', [])
buys = [t for t in trades if t.get('side') == 'buy']
sells = [t for t in trades if t.get('side') == 'sell']

num_cycles = min(len(buys), len(sells))
total_pnl = state.get('realized_pnl', 0)

print("="*70)
print("📊 WEEKLY BOT PERFORMANCE REVIEW")
print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
print("="*70)
print(f"\n💰 Total Capital: Rp {config['starting_idr']:,.0f}")
print(f"   Current Equity: Rp {state.get('cash_idr', 0):,.0f}")
print(f"   Net P&L: Rp {total_pnl:,.0f} ({total_pnl/config['starting_idr']*100:+.2f}%)")
print(f"\n🔄 Completed Trades: {num_cycles}")
print(f"   Buys: {len(buys)} | Sells: {len(sells)}")
print(f"\n🛡️ Strategy:")
print(f"   SMA: {config['strategy']['fast_sma']}/{config['strategy']['slow_sma']}")
print(f"   Stop Loss: {float(config['risk']['stop_loss_pct'])*100:.0f}%")
print(f"   Take Profit: {float(config['risk']['take_profit_pct'])*100:.0f}%")
print(f"\n🎯 Next Steps:")
if num_cycles < 50:
    print("   • Collect more data (need >100 trades for statistical significance)")
else:
    print("   • Consider running strategy experiments via run_experiments.py")
print("   • Review audit_report.py for detailed metrics")
print("\n" + "="*70)
