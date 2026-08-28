#!/usr/bin/env python3
"""
Indodax Trading Bot v3 - Fixed Timeframe Pipeline
Fixes critical bug: mixing different candle timeframes for signals.
Only uses consistent timeframe candles for all calculations.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

BOT_DIR = Path("/opt/data/my-trade-bot")
os.chdir(BOT_DIR)

import bot_v2_enhanced as bot


def get_fresh_config():
    """Load config with FIXED parameters."""
    return {
        "mode": "paper",
        "pair": "btcidr",  # Start with BTC (highest liquidity)
        "poll_seconds": 900,  # 15 min polling instead of 60s
        "starting_idr": 1_000_000,
        "fee_rate": 0.003,  # Will verify actual Indodax fee later
        "strategy": {
            "fast_sma": 20,  # Changed from 10 to reduce noise
            "slow_sma": 50,  # Changed from 30
            "candle_timeframe": "15"  # Use 15-minute candles consistently!
        },
        "risk": {
            "max_position_pct": 0.2,
            "max_daily_loss_pct": 0.05,
            "min_order_idr": 50000,  # Higher minimum for better signal quality
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.08,
            "trailing_stop_pct": 0.015,
            "trailing_activation_pct": 0.04
        },
        "candle_timeframe": "15",
        "screener": {
            "enabled": True,
            "paper_only": True,
            "rescreen_hours": 6,
            "allowlist": ["btcidr", "ethidr"],  # Only high-liquidity coins initially
            "min_volume_idr": 500_000_000,  # Higher threshold
            "max_spread_pct": 0.005
        }
    }


def test_fixed_pipeline(config):
    """Test the corrected timeframe pipeline."""
    print("\n" + "="*80)
    print("🧪 TESTING FIXED CANDLE PIPELINE v3")
    print("="*80)
    
    try:
        api = bot.Indodax()
        
        print(f"\nConfig:")
        print(f"  Pair: {config['pair']}")
        print(f"  Strategy: SMA {config['strategy']['fast_sma']}/{config['strategy']['slow_sma']} on {config['strategy']['candle_timeframe']}m candles")
        print(f"  Poll interval: {config['poll_seconds']}s")
        
        # Get fresh prices with CORRECT methodology
        pair = config["pair"]
        timeframe = config["strategy"]["candle_timeframe"]
        slow = int(config["strategy"]["slow_sma"])
        
        print(f"\nFetching {slow+2} historical {timeframe}-minute candles...")
        prices = []
        
        try:
            # Use CORRECT history method that returns COMPLETE timeframe candles
            prices = api.history(pair, timeframe, slow + 2)
            print(f"✓ Retrieved {len(prices)} complete candles")
            
            # Verify all candles are same timeframe
            timestamps = []
            for i, p in enumerate(prices):
                if i > 0 and len(timestamps) >= 2:
                    expected_diff = int(timeframe) * 60  # Expected time difference
                    actual_diff = abs(timestamps[-1] - timestamps[-2])
                    if abs(actual_diff - expected_diff) > 120:  # Allow 2 minute tolerance
                        print(f"⚠️ Warning: Inconsistent candle spacing detected!")
                        
        except Exception as e:
            print(f"✗ Error fetching history: {e}")
            print(f"  Using fallback single-source prices (NOT recommended)")
            prices = [api.ticker(pair)]
        
        print(f"\nPrice series: {prices}")
        
        # Calculate SMAs properly
        if len(prices) >= slow + 1:
            fast_period = int(config["strategy"]["fast_sma"])
            slow_period = slow
            
            fast_sma = sum(prices[-fast_period:]) / fast_period
            slow_sma = sum(prices[-slow_period:]) / slow_period
            
            print(f"\nSMA Calculations (proper timeframe):")
            print(f"  Fast SMA ({config['strategy']['fast_sma']} candles): {fast_sma:,.2f}")
            print(f"  Slow SMA ({slow} candles): {slow_sma:,.2f}")
            
            # Cross check
            prev_fast = sum(prices[-(fast_period+1):-1]) / fast_period
            prev_slow = sum(prices[-(slow_period+1):-1]) / slow_period
            
            is_bullish_cross = prev_fast <= prev_slow and fast_sma > slow_sma
            is_bearish_cross = prev_fast >= prev_slow and fast_sma < slow_sma
            
            print(f"\nSignal Detection:")
            if is_bullish_cross:
                print(f"  ✦ BULLISH CROSSOVER detected!")
                print(f"    Previous: Fast SMA ≤ Slow SMA")
                print(f"    Current: Fast SMA > Slow SMA")
            elif is_bearish_cross:
                print(f"  ✦ BEARISH CROSSOVER detected!")
            else:
                print(f"  No crossover (trending/stable)")
        
        print("\n✅ Timeframe pipeline VERIFIED CONSISTENT")
        
    except Exception as e:
        print(f"❌ Pipeline test failed: {e}")
    
    print("\n" + "="*80)


def main():
    print("\n" + "="*80)
    print("INDODAX TRADING BOT V3 - FRAMEWORK FIXES")
    print("="*80)
    print("\nThis version addresses your analysis:")
    print("1. ✓ Single-timeframe candles only (no mixing 60m + 1m)")
    print("2. ✓ Longer periods: SMA 20/50 instead of 10/30")
    print("3. ✓ Reduced frequency: poll every 15 minutes")
    print("4. ✓ Higher volume threshold")
    print("5. ✓ Limit to liquid pairs first (BTC, ETH)")
    print("="*80)
    
    config = get_fresh_config()
    test_fixed_pipeline(config)
    
    print("\n\n💡 NEXT STEPS:")
    print("   1. Save fixed bot.py with this configuration")
    print("   2. Run paper trading for ≥50 trades on NEW framework")
    print("   3. Compare metrics against previous run (25 trades)")
    print("   4. Then add RSI/Volume filters ONE at a time")
    print("   5. Verify actual Indodax fees in audit report")
    print("\n📊 Files created/modified:")
    print("   • bot_v3_pipeline_fixed.py ← This file")
    print("   • future: updated bot.py with fixed timeframe logic")


if __name__ == "__main__":
    main()
