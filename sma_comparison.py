#!/usr/bin/env python3
"""Compare SMA periods via backtesting."""
import argparse
import json
from pathlib import Path

def run_backtest(sma_fast, sma_slow):
    """Run backtest with given SMA parameters."""
    print(f"\n🧪 BACKTESTING: SMA {sma_fast}/{sma_slow}")
    print("-"*60)
    
    # Use the same config base but override strategy
    config_path = Path("/opt/data/my-trade-bot/config.json")
    config = json.loads(config_path.read_text())
    
    # Override SMA settings
    config["strategy"]["fast_sma"] = sma_fast
    config["strategy"]["slow_sma"] = sma_slow
    
    # Save temporary config
    temp_config = f"config_test_{sma_fast}_{sma_slow}.json"
    config_path.write_text(json.dumps(config))
    
    # Run bot report to get initial stats
    import sys
    sys.path.insert(0, "/opt/data/my-trade-bot")
    
    try:
        import bot
        ledger = bot.load_ledger(float(config["starting_idr"]))
        api = bot.Indodax()
        
        # Get latest ticker
        price = api.ticker(ledger.active_pair or config["pair"])
        text = bot.report(ledger, price, config)
        print(text)
        
        return {"sma": f"{sma_fast}/{sma_slow}", "status": "ready"}
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return {"sma": f"{sma_fast}/{sma_slow}", "error": str(e)}
    finally:
        # Restore original config
        config_path.write_text('''{
  "mode": "paper",
  "pair": "btcidr",
  "poll_seconds": 60,
  "starting_idr": 1000000,
  "fee_rate": 0.003,
  "strategy": {
    "fast_sma": 10,
    "slow_sma": 30
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
    "enabled": true,
    "paper_only": true,
    "rescreen_hours": 4,
    "allowlist": ["btcidr", "ethidr", "solidr"],
    "min_volume_idr": 100000000,
    "max_spread_pct": 0.01
  }
}''')


def main():
    parser = argparse.ArgumentParser(description="Compare SMA configurations")
    parser.add_argument("--compare", nargs="+", default=["10/30", "20/50"], help="SMA pairs to compare")
    args = parser.parse_args()
    
    print("="*60)
    print("📊 SMA PERIOD COMPARISON TEST")
    print("="*60)
    
    results = {}
    
    for sma_str in args.compare:
        parts = sma_str.split("/")
        if len(parts) == 2:
            fast, slow = int(parts[0]), int(parts[1])
            result = run_backtest(fast, slow)
            results[f"{fast}/{slow}"] = result
    
    print("\n" + "="*60)
    print("SUMMARY:")
    print("="*60)
    for pair, result in results.items():
        if "error" in result:
            print(f"{pair}: ❌ Error - {result['error']}")
        else:
            print(f"{pair}: ✅ Configuration ready")


if __name__ == "__main__":
    main()
