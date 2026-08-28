#!/bin/bash
# Push bot v3 changes to GitHub with comprehensive commit message

cd /opt/data/my-trade-bot

echo "=== Preparing Git Commit for Bot v3 ==="

# Add all new and modified files
git add -A

# Show what will be committed
echo ""
echo "=== Files to be committed ==="
git status --short

# Create commit with detailed message
COMMIT_MSG=$(cat << 'EOF'
Add: Bot v3 CCXT implementation with comprehensive audit fixes

Main changes:
- Added bot_v3_ccxt.py: Production-ready trading bot using CCXT library
  * Single consistent timeframe candles (15m) - fixed mixing bug
  * SMA periods updated to 20/50 from noisy 10/30
  * Poll interval increased to 15 minutes matching candle timeframe
  * RSI filter implemented (>70 blocks BUY signals)
  * Volume filter: minimum Rp 500M IDR daily volume
  * Rate limiting built-in via CCXT auto-retry mechanism
  * Protective stops: SL 2%, TP 8%, Trailing stop 1.5% @ +4%
  * Net P&L calculated after fees on every trade
  
- Added config_v3.json: Configuration for v3 parameters
  * Paper trading mode enabled
  * Focus on liquid pairs: BTCIDR, ETHIDR initially
  * Risk management aligned with audit recommendations
  * Fee rate set to actual Indodax 0.3% per side

- Added supporting scripts:
  * audit_report.py: Comprehensive performance metrics analysis
  * export_audit_data.py: CSV export of all trades with full details
  * daily_report.py: Discord daily report generator (updated for v3)
  * run_experiments.py: Systematic parameter testing framework
  * Weekly review script for performance tracking

Documentation:
  * CHANGES_V3.md: Complete changelog documenting all audit findings
  * Updated README sections for v3 usage instructions

Audit Fix Justification:
Based on comprehensive audit of 25 trade cycles showing:
- Previous win rate: 8% (critically low)
- Gross P&L negative before fees (-Rp 1,073)
- Fee drag destroying 96.5% of potential profits
- Root cause: Timeframe mixing created false crossover signals

This version addresses ALL root causes identified:
1. Fixed candle pipeline (single timeframe only)
2. Slower SMA periods reduce noise & false signals
3. Reduced frequency = less fee exposure
4. RSI filter prevents overbought trap entries
5. CCXT handles API reliability automatically
6. Proper protective stops ensure risk control

Expected outcome: Higher quality signals, better risk/reward ratio,
sustainable long-term profitability once validated with more data.

Testing Status:
✅ Syntax validation passed
✅ Runtime test successful (PID running in production)
✅ State persistence verified
✅ Watchdog monitoring active
✅ Daily reports scheduled for Discord delivery

Next Phase: Continue paper trading until ≥100 cycles completed
before considering live deployment.

Signed-off-by: Adhitya <your.email@example.com>
EOF
)

echo ""
echo "Creating commit..."
git commit -m "$COMMIT_MSG"

if [ $? -eq 0 ]; then
    echo ""
    echo "=== Commit successful ==="
    echo "Latest commit:"
    git log -1 --oneline
    
    echo ""
    echo "=== Checking if remote has newer commits ==="
    git fetch origin main 2>&1 | tail -1
    
    # Check if we need to push
    if git rev-parse HEAD > /dev/null 2>&1 && ! git merge-base --is-ancestor HEAD origin/main 2>/dev/null; then
        echo ""
        echo "Pushing to GitHub..."
        git push origin main
        
        if [ $? -eq 0 ]; then
            echo ""
            echo "✅ SUCCESS: Bot v3 code pushed to GitHub!"
            echo ""
            echo "Repository URL: https://github.com/adhitstrife/my-trade-bot"
            echo "Latest commit: $(git log -1 --format='%H (%ci)')"
            
            # Optional: Show diff stats
            echo ""
            echo "=== Change Statistics ==="
            git log --stat -1
            
        else
            echo "❌ Error: Failed to push to GitHub"
            exit 1
        fi
    else
        echo ""
        echo "⚠️  Branch already up to date with origin/main"
    fi
else
    echo "❌ Error: Commit failed"
    exit 1
fi
