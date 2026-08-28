#!/usr/bin/env python3
"""
Indodax Trading Bot - Comprehensive Export Tool
Exports all trade cycles to CSV for detailed analysis.
Includes every metric needed for performance evaluation.
"""

import os
import sys
import json
import csv
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

BOT_DIR = Path("/opt/data/my-trade-bot")
os.chdir(BOT_DIR)
sys.path.insert(0, str(BOT_DIR))

def load_state():
    """Load current trading state."""
    with open(BOT_DIR / "data/state.json", "r") as f:
        return json.load(f)


def load_config():
    """Load bot configuration."""
    import bot
    return bot.load_config(BOT_DIR / "config.json")


def calculate_metrics_from_raw(raw_trades):
    """Calculate all aggregate metrics from raw cycle data."""
    if not raw_trades:
        return {}
    
    total_trades = len(raw_trades)
    wins = [t for t in raw_trades if t['net_pnl'] > 0]
    losses = [t for t in raw_trades if t['net_pnl'] <= 0]
    
    win_rate = len(wins) / total_trades * 100
    
    # Gross vs Net
    total_gross = sum(t['gross_pnl'] for t in raw_trades)
    total_net = sum(t['net_pnl'] for t in raw_trades)
    total_fees = abs(total_gross - total_net)
    
    # Profit Factor (gross)
    gross_wins = sum(t['gross_pnl'] for t in wins)
    gross_losses = abs(sum(t['gross_pnl'] for t in losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')
    
    # Expectancy per trade (net)
    expectancy = total_net / total_trades
    
    # Average win/loss
    avg_win = sum(t['net_pnl'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['net_pnl'] for t in losses) / len(losses) if losses else 0
    
    # By Pair Analysis
    by_pair = defaultdict(list)
    for t in raw_trades:
        by_pair[t['pair']].append(t)
    
    pair_results = {}
    for pair, trades_list in by_pair.items():
        pwins = sum(1 for t in trades_list if t['net_pnl'] > 0)
        plosses = len(trades_list) - pwins
        pair_results[pair.upper()] = {
            'trades': len(trades_list),
            'wins': pwins,
            'losses': plosses,
            'win_rate_pct': pwins / len(trades_list) * 100,
            'total_pnl': sum(t['net_pnl'] for t in trades_list),
            'avg_holding_minutes': sum(t['holding_minutes'] for t in trades_list) / len(trades_list)
        }
    
    # By Exit Reason
    by_reason = defaultdict(list)
    for t in raw_trades:
        reason = t['exit_reason'].replace(',', '_')  # Clean for dict key
        by_reason[reason].append(t)
    
    reason_results = {}
    for reason, trades_list in by_reason.items():
        rwins = sum(1 for t in trades_list if t['net_pnl'] > 0)
        reason_results[reason] = {
            'count': len(trades_list),
            'pct': len(trades_list) / total_trades * 100,
            'wins': rwins,
            'win_rate_pct': rwins / len(trades_list) * 100,
            'total_pnl': sum(t['net_pnl'] for t in trades_list)
        }
    
    # Holding Time Analysis
    all_holdings = [t['holding_minutes'] for t in raw_trades]
    median_hold = sorted(all_holdings)[len(all_holdings)//2] if all_holdings else 0
    
    # Fee Analysis
    avg_fee_per_trade = total_fees / total_trades
    
    return {
        'total_trades': total_trades,
        'wins': len(wins),
        'losses': len(losses),
        'win_rate_pct': win_rate,
        'total_gross_pnl': total_gross,
        'total_net_pnl': total_net,
        'total_fees_paid': total_fees,
        'profit_factor': profit_factor,
        'expectancy_per_trade': expectancy,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'by_pair': dict(pair_results),
        'by_exit_reason': reason_results,
        'median_holding_minutes': median_hold,
        'avg_fee_per_trade': avg_fee_per_trade
    }


def match_buys_to_sells(buys, sells):
    """Match each BUY to its corresponding SELL based on timing."""
    matched_cycles = []
    used_sell_indices = set()
    
    for buy in buys:
        best_sell_idx = None
        min_time_diff = float('inf')
        
        for i, sell in enumerate(sells):
            if i in used_sell_indices:
                continue
            
            try:
                buy_time = datetime.fromisoformat(buy['time'].replace('+00:00', ''))
                sell_time = datetime.fromisoformat(sell['time'].replace('+00:00', ''))
                
                # Must be after buy and within reasonable time window (7 days max)
                if sell_time > buy_time:
                    time_diff = abs((sell_time - buy_time).total_seconds())
                    
                    if time_diff < min_time_diff and time_diff < 604800:  # 7 days
                        min_time_diff = time_diff
                        best_sell_idx = i
                        
            except Exception:
                continue
        
        if best_sell_idx is not None:
            used_sell_indices.add(best_sell_idx)
            
            # Calculate metrics inline
            try:
                entry_time = datetime.fromisoformat(buy['time'].replace('+00:00', ''))
                exit_time = datetime.fromisoformat(sells[best_sell_idx]['time'].replace('+00:00', ''))
                
                entry_price = float(buy['price'])
                exit_price = float(sells[best_sell_idx]['price'])
                idr_spent = float(buy['amount'])
                asset_amount = idr_spent / entry_price
                
                fee_rate = 0.003
                buy_fee = idr_spent * fee_rate
                proceeds = exit_price * asset_amount * (1 - fee_rate)
                sell_fee_on_proceeds = (proceeds / (1 - fee_rate)) * fee_rate
                
                gross_pnl = exit_price * asset_amount - idr_spent
                net_pnl = proceeds - (idr_spent + buy_fee)
                price_change_pct = ((exit_price - entry_price) / entry_price) * 100
                exit_reason = buy.get('reason', 'unknown').replace('buy', 'sma_crossover')
                
                matched_cycles.append({
                    'cycle_id': len(matched_cycles) + 1,
                    'pair': buy.get('pair', 'unknown'),
                    'entry_time': buy['time'],
                    'exit_time': sells[best_sell_idx]['time'],
                    'entry_price': round(entry_price, 2),
                    'exit_price': round(exit_price, 2),
                    'idr_spent': round(idr_spent, 2),
                    'asset_amount': round(asset_amount, 8),
                    'buy_fee': round(buy_fee, 2),
                    'sell_fee': round(sell_fee_on_proceeds, 2),
                    'gross_pnl': round(gross_pnl, 2),
                    'net_pnl': round(net_pnl, 2),
                    'price_change_pct': round(price_change_pct, 3),
                    'exit_reason': exit_reason,
                    'holding_minutes': int((exit_time - entry_time).total_seconds() / 60),
                    'highest_price_after_buy': None,
                })
                
            except Exception as e:
                print(f"Warning: Failed to match trades - {e}")
                continue
    
    return matched_cycles


def main():
    print("\n" + "="*80)
    print("📊 EXPORTING RAW TRADE DATA")
    print("="*80)
    
    # Load state
    state = load_state()
    config = load_config()
    
    trades = state.get('trades', [])
    
    if len(trades) < 2:
        print("\n❌ Insufficient trade history (< 2 trades)")
        return
    
    # Separate buys and sells
    buys = [t for t in trades if t.get('side') == 'buy']
    sells = [t for t in trades if t.get('side') == 'sell']
    
    print(f"\nFound {len(buys)} BUYs and {len(sells)} SELLs")
    print("Matching pairs...")
    
    # Match cycles
    matched_cycles = match_buys_to_sells(buys, sells)
    
    print(f"✓ Matched {len(matched_cycles)} complete cycles\n")
    
    if not matched_cycles:
        print("❌ No valid trade pairs found!")
        return
    
    # Output CSV
    csv_filename = f"/opt/data/trade_cycles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = [
            'cycle_id', 'pair', 'entry_time', 'exit_time', 
            'entry_price', 'exit_price', 'idr_spent', 'asset_amount',
            'buy_fee', 'sell_fee', 'gross_pnl', 'net_pnl', 'price_change_pct',
            'exit_reason', 'holding_minutes', 'highest_price_after_buy'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(matched_cycles)
    
    print(f"\n💾 CSV exported to: {csv_filename}\n")
    
    # Print summary
    if matched_cycles:
        metrics = calculate_metrics_from_raw(matched_cycles)
        
        lines = []
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        
        lines.append("="*80)
        lines.append("📊 EXPORT SUMMARY")
        lines.append(f"   Generated: {timestamp}")
        lines.append("="*80)
        lines.append("")
        lines.append(f"Total Cycles:     {metrics['total_trades']}")
        lines.append(f"Win Rate:         {metrics['win_rate_pct']:.1f}% ({metrics['wins']}W / {metrics['losses']}L)")
        lines.append(f"Profit Factor:    {metrics['profit_factor']:.2f}x (gross)")
        lines.append(f"Expectancy/Trade: Rp {metrics['expectancy_per_trade']:,.2f}")
        lines.append(f"Avg Win:          Rp {abs(metrics['avg_win']):,.2f}")
        lines.append(f"Avg Loss:         Rp {abs(metrics['avg_loss']):,.2f}")
        lines.append(f"Total Fees Paid:  Rp {metrics['total_fees_paid']:,.2f}")
        lines.append(f"Median Holding:   {metrics['median_holding_minutes']} minutes")
        
        print("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
