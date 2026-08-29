#!/bin/bash
# Quick push for bot v3 - minimal but complete

cd /opt/data/my-trade-bot

echo "=== Bot V3 GitHub Push ==="
git add -A

if [ -n "$(git status --porcelain)" ]; then
    git commit -m "Add: Bot v3 CCXT with audit fixes" 2>&1 | tail -2
fi

git fetch origin main 2>/dev/null
git merge-base --is-ancestor HEAD origin/main 2>/dev/null || git push origin main 2>&1 | tail -3

echo "Done!"
