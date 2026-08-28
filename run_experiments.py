#!/usr/bin/env python3
"""
Systematic Strategy Experiment Framework
Tests different parameters while keeping other variables constant.
Reports results for comparison.
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime
from statistics import mean, stdev

BOT_DIR = Path("/opt/data/my-trade-bot")
os.chdir(BOT_DIR)
sys.path.insert(0, str(BOT_DIR))

# Config templates for comparison
CONFIG_TEMPLATES = {
    "baseline": {
        "fast_sma": 10,
        "slow_sma": 30,
        "volume_multiplier": 1.0,
        "description": "Original strategy"
    },
    "conservative_sma": {
        "fast_sma": 20,
        "slow_sma": 50,
        "volume_multiplier": 2.0,
        "description": "More conservative EMA periods"
    },
    "high_volume_filter": {
        "fast_sma": 10,
        "slow_sma": 30,
        "volume_multiplier": 3.0,  # Higher volume requirement
        "description": "Only liquid pairs"
    }
}


def load_config_template(template_name):
    """Load config with template settings."""
    template = CONFIG_TEMPLATES[template_name]
    
    base_config = {
        "mode": "paper",
        "pair": "btcidr",
        "poll_seconds": 60,
        "starting_idr": 1_000_000,
        "fee_rate": 0.003,
        "strategy": {
            "fast_sma": template["fast_sma"],
            "slow_sma": template["slow_sma"]
        },
        "risk": {
            "max_position_pct": 0.2,
            "max_daily_loss_pct": 0.03,
            "min_order_idr": 10000,
            "stop_loss_pct": 0.03,
            "take_profit_pct": 0.08,
            "trailing_stop_pct": 0.025,
            "trailing_activation_pct": 0.04
        },
        "candle_timeframe": "60",
        "screener": {
            "enabled": True,
            "paper_only": True,
            "rescreen_hours": 4,
            "allowlist": ["btcidr", "ethidr", "solidr"],
            "min_volume_idr": 100_000_000 * template["volume_multiplier"],  # Apply multiplier
            "max_spread_pct": 0.01
        }
    }
    return base_config


def run_experiment(config_name, config_dict):
    """Run one experiment and record results."""
    print(f"\n🧪 RUNNING EXPERIMENT: {config_name}")
    print("-"*60)
    
    # Save temp config
    temp_config = f"config_{config_name}.json"
    (BOT_DIR / temp_config).write_text(json.dumps(config_dict))
    
    try:
        import bot
        
        # Load fresh ledger state
        initial_capital = config_dict['starting_idr']
        ledger = bot.load_ledger(initial_capital)
        
        # Get current ticker
        api = bot.Indodax()
        pair = ledger.active_pair or config_dict["pair"]
        price = api.ticker(pair)
        
        # Get performance metrics
        report = bot.report(ledger, price, config_dict)
        
        # Extract key metrics from report
        lines = report.split('\n')
        equity_line = [l for l in lines if 'Equity:' in l][0]
        pnl_line = [l for l in lines if 'P/L:' in l][0]
        trades_line = [l for l in lines if 'Trades:' in l][0]
        
        equity = float(equity_line.split(':')[1].strip().replace('Rp ', '').replace(',', ''))
        pnl_str = pnl_line.split(': ')[1]
        pnl_abs = float(pnl_str.replace('Rp', '').replace('%)', '').replace('(', '').strip())
        
        trade_count = int(trades_line.split(':')[1].strip())
        
        # Calculate returns
        return_pct = (equity / initial_capital - 1) * 100
        
        result = {
            'name': config_name,
            'config': config_dict.copy(),
            'timestamp': datetime.now().isoformat(),
            'equity': equity,
            'pnl_net': pnl_abs,
            'return_pct': return_pct,
            'trade_count': trade_count,
            'active_pair': pair.upper(),
            'status': 'completed'
        }
        
        print(f"   Equity: Rp {equity:,.0f} ({return_pct:+.2f}%)")
        print(f"   Trades: {trade_count}")
        print(f"   Pair: {pair.upper()}")
        print(f"   Status: ✅ OK\n")
        
        return result
        
    except Exception as e:
        print(f"   ❌ FAILED: {e}\n")
        return {'name': config_name, 'error': str(e), 'status': 'failed'}
    finally:
        # Clean up temp config
        if (BOT_DIR / temp_config).exists():
            (BOT_DIR / temp_config).unlink()


def generate_comparison_report(results):
    """Generate comparison table."""
    completed = [r for r in results if r.get('status') == 'completed']
    
    if not completed:
        print("\n❌ No successful experiments to compare!")
        return
    
    print("\n" + "="*80)
    print("📊 STRATEGY COMPARISON REPORT")
    print("="*80)
    
    headers = ["Config", "Description", "Return %", "Net P&L", "Trades"]
    print(f"{headers[0]:<15} {headers[1]:<25} {headers[2]:>10} {headers[3]:>12} {headers[4]:>8}")
    print("-"*80)
    
    sorted_results = sorted(completed, key=lambda x: x['return_pct'], reverse=True)
    
    for r in sorted_results:
        name = r['name'][:14]
        desc = r['config']['strategy'].get('description', '')[:24]
        ret = f"{r['return_pct']:+.2f}%"
        pnl = f"Rp {abs(r['pnl_net']):,.0f}"
        pnl_sign = "-" if r['pnl_net'] < 0 else "+"
        tr = r['trade_count']
        
        print(f"{name:<15} {desc:<25} {ret:>10} {pnl_sign}{pnl:>11} {tr:>8}")
    
    # Winner
    winner = sorted_results[0]
    print("\n" + "="*80)
    print(f"🏆 WINNER: {winner['name']} ({winner['return_pct']:+.2f}% return)")
    print("="*80)
    
    # Next steps recommendation
    if winner['return_pct'] > 0:
        print(f"\n✅ {winner['name']} shows positive edge! Consider:")
        print("   • Running live simulation")
        print("   • Backtesting longer history")
        print("   • Adding more filters")
    elif winner['return_pct'] > -2:
        print(f"\n⚠️ {winner['name']} nearly break-even:")
        print("   • Need larger sample size")
        print("   • Consider fee reduction")
        print("   • Review exit timing")
    else:
        print(f"\n❌ All strategies underperforming:")
        print("   • Reevaluate entry logic")
        print("   • Check volume requirements")
        print("   • Consider different indicator set")
    
    print("\n" + "="*80)


def main():
    print("="*80)
    print("🔬 SYSTEMATIC STRATEGY EXPERIMENTS")
    print("="*80)
    print("\nRunning controlled experiments to identify best configuration...")
    
    start_time = time.time()
    
    results = []
    for config_name, template in CONFIG_TEMPLATES.items():
        config = load_config_template(config_name)
        result = run_experiment(config_name, config)
        results.append(result)
    
    elapsed = time.time() - start_time
    print(f"\n⏱ Total runtime: {elapsed:.1f}s")
    
    # Generate comparison
    generate_comparison_report(results)
    
    # Save full results
    output_file = BOT_DIR / f"experiments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'total_runtime_seconds': elapsed,
            'results': results
        }, f, indent=2)
    print(f"\n💾 Full results saved to: {output_file}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
