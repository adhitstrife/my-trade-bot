import copy
import unittest

import bot


CONFIG = {
    "mode": "paper", "pair": "btcidr", "poll_seconds": 60,
    "starting_idr": 1_000_000, "fee_rate": 0.003, "execution_slippage_pct": 0.001,
    "strategy": {"fast_sma": 2, "slow_sma": 3}, "candle_timeframe": "15",
    "risk": {"max_position_pct": 0.2, "max_risk_per_trade_pct": 0.005,
             "max_daily_loss_pct": 0.03, "min_order_idr": 10_000,
             "stop_loss_pct": 0.02, "take_profit_pct": 0.08,
             "trailing_stop_pct": 0.015, "trailing_activation_pct": 0.04},
    "screener": {"enabled": False},
}


class BotTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(CONFIG)

    def ledger(self):
        return bot.Ledger(cash_idr=1_000_000, peak_equity=1_000_000,
                          day_start_equity=1_000_000, day="2026-01-01")

    def test_paper_buy_applies_fee_slippage_and_risk_cap(self):
        ledger = self.ledger()
        trade = bot.fill_paper(ledger, "buy", 100, self.config)
        # Risk budget is Rp5,000 and a 2% stop permits Rp250,000, so the
        # 20% exposure cap (Rp200,000) is the active limit.
        self.assertEqual(trade["amount"], 200_000)
        self.assertAlmostEqual(trade["price"], 100.1)
        self.assertLess(ledger.asset, 2_000)

    def test_trailing_stop_is_measured_from_highest_price(self):
        ledger = self.ledger()
        bot.fill_paper(ledger, "buy", 100, self.config)
        self.assertIsNone(bot.exit_reason(ledger, 105, self.config))
        self.assertEqual(bot.exit_reason(ledger, 103, self.config), "trailing_stop")

    def test_crossover_requires_a_real_cross(self):
        self.assertEqual(bot.signal_from_prices([10, 9, 8, 11], 2, 3), "buy")
        self.assertIsNone(bot.signal_from_prices([8, 9, 10, 11], 2, 3))


if __name__ == "__main__":
    unittest.main()
