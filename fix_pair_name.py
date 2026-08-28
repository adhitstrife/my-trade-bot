#!/usr/bin/env python3
"""Quick fix: Re-export CSV with proper pair names."""
import csv
import json
from datetime import datetime


def main():
    """Main execution function."""
    with open('/opt/data/my-trade-bot/data/state.json') as f:
        state = json.load(f)
    
    with open('/opt/data/my-trade-bot/config.json') as f:
        config = json.load(f)

    trades = state.get('trades', [])
    active_pair = state.get('active_pair', config['pair'])

    print(f"Active pair: {active_pair}")
    print(f"Found {len(trades)} trades")

    # Update existing CSV
    with open('/opt/data/trade_cycles_20260828_093042.csv', 'r') as f:
        reader = list(csv.DictReader(f))

    print(f"Updated {len(reader)} rows with pair: {active_pair.upper()}")

    output_csv = '/opt/data/trade_cycles_with_pairs_fixed.csv'
    with open(output_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=reader[0].keys())
        writer.writeheader()
        for row in reader:
            row['pair'] = active_pair.upper()
            writer.writerow(row)

    print(f"✓ Saved to: {output_csv}")


if __name__ == "__main__":
    main()
