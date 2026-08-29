#!/usr/bin/env python3
"""
Indodax Trading Bot - Comprehensive Audit Report
Generates detailed performance metrics for transparency and analysis.
Report format suitable for investor review and strategy validation.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timezone

BOT_DIR = Path(__file__).resolve().parent
os.chdir(BOT_DIR)
sys.path.insert(0, str(BOT_DIR))

import json


def load_state():
    """Load current trading state."""
    with open(BOT_DIR / "data/state.json", "r") as f:
        return json.load(f)


def load_config():
    """Load bot configuration."""
    import bot
    return bot.load_config(BOT_DIR / "config.json")


def calculate_audit_metrics(trades, initial_capital, fee_rate):
    """
    Calculate comprehensive audit metrics.
    Returns dict with all required statistics.
    """
    buys = [t for t in trades if t.get("side") == "buy"]
    sells = [t for t in trades if t.get("side") == "sell"]
    
    # Match buy-sell pairs by timing
    completed_cycles = []
    used_sells = set()
    
    for buy in buys:
        best_idx = None
        min_diff = float('inf')
        
        for i, sell in enumerate(sells):
            if i in used_sells:
                continue
            
            try:
                buy_time = datetime.fromisoformat(buy['time'].replace('+00:00', ''))
                sell_time = datetime.fromisoformat(sell['time'].replace('+00:00', ''))
                
                if sell_time > buy_time:
                    diff = abs((sell_time - buy_time).total_seconds())
                    if diff < min_diff:
                        min_diff = diff
                        best_idx = i
            except Exception:
                continue
        
        if best_idx is not None:
            buy_data = buys[buys.index(buy)]
            sell_data = sells[best_idx]
            used_sells.add(best_idx)
            
            entry_price = buy_data['price']
            exit_price = sell_data['price']
            amount_bought = buy_data['amount']  # IDR spent
            
            # Calculate quantities
            quantity = amount_bought / entry_price
            proceeds = exit_price * quantity
            fee_paid_buy = amount_bought * fee_rate
            fee_paid_sell = proceeds * fee_rate
            total_fee = fee_paid_buy + fee_paid_sell
            
            # Gross vs Net P&L
            gross_pnl = proceeds - amount_bought
            net_pnl = gross_pnl - total_fee
            
            completed_cycles.append({
                'entry': entry_price,
                'exit': exit_price,
                'quantity': quantity,
                'gross_pnl': gross_pnl,
                'net_pnl': net_pnl,
                'fees': total_fee,
                'fee_rate': fee_rate,
                'reason': buy_data.get('reason', 'unknown'),
                'duration_seconds': min_diff,
                'pnl_pct': (proceeds / amount_bought - 1) * 100
            })
    
    if not completed_cycles:
        return {"error": "No completed cycles found"}
    
    wins = [c for c in completed_cycles if c['net_pnl'] > 0]
    losses = [c for c in completed_cycles if c['net_pnl'] <= 0]
    
    n_wins = len(wins)
    n_losses = len(losses)
    total_trades = len(completed_cycles)
    
    # Win rate
    win_rate = n_wins / total_trades if total_trades else 0
    
    # Average values
    avg_win = sum(c['net_pnl'] for c in wins) / max(n_wins, 1)
    avg_loss = sum(c['net_pnl'] for c in losses) / max(n_losses, 1)
    
    # Profit Factor (gross)
    total_gross_wins = sum(c['gross_pnl'] for c in wins)
    total_gross_losses = abs(sum(c['gross_pnl'] for c in losses))
    profit_factor = total_gross_wins / max(total_gross_losses, 0.001)
    
    # Expectancy per trade (net after fees)
    total_net_pnl = sum(c['net_pnl'] for c in completed_cycles)
    expectancy = total_net_pnl / total_trades if total_trades else 0
    
    # Total fees paid
    total_fees = sum(c['fees'] for c in completed_cycles)
    
    # Max drawdown calculation (peak to trough)
    equity_curve = [initial_capital]
    running_cash = initial_capital
    
    # Sort cycles by time
    sorted_cycles = sorted(completed_cycles, key=lambda x: x['entry'])
    
    for cycle in sorted_cycles[:len(sorted_cycles)//2]:  # Approximate half for cash flow
        running_cash += cycle['net_pnl']
        equity_curve.append(running_cash)
    
    peak_equity = max(equity_curve)
    max_dd = 0
    running_peak = peak_equity
    
    for val in equity_curve:
        if val > running_peak:
            running_peak = val
        dd = (running_peak - val) / running_peak * 100 if running_peak else 0
        if dd > max_dd:
            max_dd = dd
    
    # Risk/Reward Ratio
    rr_ratio = abs(avg_win) / abs(avg_loss) if avg_loss != 0 else 999
    
    # Break-even win rate needed
    required_win_rate = abs(avg_loss) / (abs(avg_win) + abs(avg_win)) if avg_win != 0 else 0
    
    # Return metrics
    total_return_pct = (total_net_pnl / initial_capital) * 100
    
    # Exit reason distribution
    exit_reasons = {}
    for c in completed_cycles:
        reason = c.get('reason', 'unknown')
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    
    return {
        'summary': {
            'total_trades': total_trades,
            'wins': n_wins,
            'losses': n_losses,
            'win_rate_pct': win_rate * 100,
            'total_return': total_net_pnl,
            'total_return_pct': total_return_pct,
        },
        'averages': {
            'avg_win_net': avg_win,
            'avg_loss_net': avg_loss,
            'profit_factor': profit_factor,
            'expectancy_per_trade': expectancy,
            'risk_reward_ratio': rr_ratio,
            'break_even_win_rate': required_win_rate * 100,
        },
        'fees': {
            'total_fees_paid': total_fees,
            'avg_fee_per_trade': total_fees / total_trades if total_trades else 0,
        },
        'drawdown': {
            'max_drawdown_pct': max_dd,
        },
        'exits': exit_reasons,
        'audit_complete': True
    }


def generate_report(state, config, metrics):
    """Generate formatted audit report."""
    lines = []
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    
    lines.append("="*80)
    lines.append("📊 INDO DAX TRADING BOT - COMPREHENSIVE AUDIT REPORT")
    lines.append(f"   Generated: {timestamp}")
    lines.append("="*80)
    lines.append("")
    
    # Executive Summary
    s = metrics['summary']
    lines.append("EXECUTIVE SUMMARY")
    lines.append("-"*80)
    lines.append(f"Trading Mode:      {config['mode']}")
    lines.append(f"Active Pair:       {(state.get('active_pair') or config['pair']).upper()}")
    lines.append(f"Initial Capital:   Rp {int(config['starting_idr']):,.0f}")
    lines.append(f"Total Trades:      {s['total_trades']} complete cycles")
    lines.append(f"Win Rate:          {s['win_rate_pct']:.1f}% ({s['wins']}W / {s['losses']}L)")
    lines.append(f"Net P&L:           Rp {s['total_return']:,.0f} ({s['total_return_pct']:+.2f}%)")
    lines.append("")
    
    # Detailed Metrics
    a = metrics['averages']
    lines.append("PERFORMANCE METRICS")
    lines.append("-"*80)
    lines.append(f"Avg Win (net):     Rp {a['avg_win_net']:,.2f}")
    lines.append(f"Avg Loss (net):    Rp {a['avg_loss_net']:,.2f}")
    lines.append(f"Profit Factor:     {a['profit_factor']:.2f}x")
    lines.append(f"Expectancy/Trade:  Rp {a['expectancy_per_trade']:,.2f}")
    lines.append(f"Risk/Reward Ratio: {a['risk_reward_ratio']:.2f}:1")
    lines.append(f"Required Win Rate: {a['break_even_win_rate']:.1f}% (to break even)")
    lines.append("")
    
    # Fee Analysis
    f = metrics['fees']
    lines.append("FEE ANALYSIS")
    lines.append("-"*80)
    lines.append(f"Fee Rate Applied:  {config['fee_rate']*100:.1f}% per side")
    lines.append(f"Total Fees Paid:   Rp {f['total_fees_paid']:,.2f}")
    lines.append(f"Avg Fee/Trade:     Rp {f['avg_fee_per_trade']:,.2f}")
    lines.append(f"Fees Impact on P&L: {f['total_fees_paid']/abs(s['total_return'])*100 if s['total_return'] else 0:+.1f}% of gross P&L")
    lines.append("")
    
    # Drawdown
    d = metrics['drawdown']
    lines.append("RISK ANALYSIS")
    lines.append("-"*80)
    lines.append(f"Max Drawdown:      {d['max_drawdown_pct']:.1f}%")
    lines.append(f"Stop Loss:         {float(config['risk']['stop_loss_pct'])*100:.0f}% (active)")
    lines.append(f"Take Profit:       {float(config['risk']['take_profit_pct'])*100:.0f}%")
    lines.append(f"Trailing Stop:     {float(config['risk']['trailing_stop_pct'])*100:.1f}% @ +{float(config['risk']['trailing_activation_pct'])*100:.0f}%")
    lines.append("")
    
    # Exit Reasons
    lines.append("EXIT REASON DISTRIBUTION")
    lines.append("-"*80)
    for reason, count in sorted(metrics['exits'].items(), key=lambda x: x[1], reverse=True):
        pct = count / s['total_trades'] * 100
        lines.append(f"• {reason}: {count} trades ({pct:.0f}%)")
    lines.append("")
    
    # Strategy Assessment
    lines.append("STRATEGY ASSESSMENT")
    lines.append("-"*80)
    
    if s['total_return_pct'] > 0:
        lines.append("✅ PROFITABLE: Strategy shows positive edge")
    elif abs(s['total_return_pct']) < 2:
        lines.append("⚠️ NEAR BREAK-EVEN: Within noise range, needs more data")
    else:
        lines.append("❌ UNDERPERFORMING: Negative expectancy requires revision")
    
    if a['profit_factor'] >= 1.5:
        lines.append("✅ GOOD: Profit factor indicates quality wins")
    elif a['profit_factor'] >= 1.0:
        lines.append("⚠️ ACCEPTABLE but marginal")
    else:
        lines.append("❌ POOR: Losses exceed gains")
    
    if d['max_drawdown_pct'] > 10:
        lines.append(f"⚠️ HIGH DRAWDOWN: {d['max_drawdown_pct']:.1f}% exceeds acceptable risk")
    else:
        lines.append("✅ ACCEPTABLE DRAWDOWN within risk limits")
    
    lines.append("")
    
    # Action Items
    lines.append("RECOMMENDATIONS FOR OPTIMIZATION")
    lines.append("-"*80)
    if s['win_rate_pct'] < 45:
        lines.append("• Increase win rate through additional filters (RSI, volume)")
    if a['risk_reward_ratio'] < 1.5:
        lines.append("• Improve risk/reward ratio by adjusting TP targets")
    if f['total_fees_paid'] > abs(s['total_return']) * 0.1:
        lines.append("• Reduce trade frequency to minimize fee drag")
    if len(metrics['exits']) == 1:
        lines.append("• Diversify exit strategies (not just SMA crossover)")
    
    lines.append("• Validate with larger sample size (>100 trades)")
    lines.append("• Compare different SMA periods via backtesting")
    lines.append("• Test volume filtering to avoid illiquid entries")
    lines.append("")
    
    lines.append("="*80)
    lines.append("🔒 END OF AUDIT REPORT")
    lines.append("="*80)
    
    return "\n".join(lines)


def main():
    try:
        state = load_state()
        config = load_config()
        fee_rate = config['fee_rate']
        initial_capital = config['starting_idr']
        
        trades = state.get('trades', [])
        
        if len(trades) < 2:
            print("❌ Insufficient trade history for audit (< 2 trades)")
            return
        
        # Run calculations
        metrics = calculate_audit_metrics(trades, initial_capital, fee_rate)
        
        if 'error' in metrics:
            print(f"❌ Audit error: {metrics['error']}")
            return
        
        # Generate report
        report = generate_report(state, config, metrics)
        print(report)
        
    except Exception as exc:
        print(f"❌ Audit failed: {exc}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
