from __future__ import annotations

import unittest

from polymarket_lower_win.binance import BinancePeriodSnapshot
from polymarket_lower_win.paper import PaperConfig, evaluate_market
from polymarket_lower_win.polymarket import BinaryMarket


class PaperStrategyTest(unittest.TestCase):
    def test_buy_signal_when_flat_external_and_low_price_mispriced(self) -> None:
        cfg = PaperConfig(
            run_id="test",
            pre_max_seconds_remaining=300,
            shares_per_signal=10.0,
            child_shares=2.0,
            max_shares_per_market=10.0,
        )
        market = BinaryMarket(
            symbol="btc",
            timeframe="5m",
            slug="btc-updown-5m-1773502500",
            title="Bitcoin Up or Down",
            start_ts=1773502500,
            end_ts=1773502800,
            up_price=0.02,
            down_price=0.98,
            min_order_size=1.0,
            tick_size=0.01,
            active=True,
            closed=False,
            source="test",
        )
        external = BinancePeriodSnapshot(
            symbol="btc",
            timeframe="5m",
            start_ts=1773502500,
            end_ts=1773502800,
            open_price=100000.0,
            high_price=100030.0,
            low_price=99980.0,
            last_price=100010.0,
        )
        decision = evaluate_market(
            market,
            external,
            cfg,
            current_shares=0.0,
            now_ts=1773502700,
            open_position_count=0,
        )
        self.assertTrue(decision.should_buy)
        self.assertEqual(decision.outcome, "Up")
        self.assertEqual(decision.phase, "pre_close")
        self.assertEqual(decision.context_label, "flat")
        self.assertEqual(decision.shares, 2.0)
        self.assertGreater(float(decision.mispricing_points or 0.0), 0.0)

    def test_skip_dual_side_pair_market(self) -> None:
        cfg = PaperConfig(run_id="test")
        market = BinaryMarket(
            symbol="eth",
            timeframe="5m",
            slug="eth-updown-5m-1773503700",
            title="Ethereum Up or Down",
            start_ts=1773503700,
            end_ts=1773504000,
            up_price=0.015,
            down_price=0.015,
            min_order_size=1.0,
            tick_size=0.01,
            active=True,
            closed=False,
            source="test",
        )
        external = BinancePeriodSnapshot(
            symbol="eth",
            timeframe="5m",
            start_ts=1773503700,
            end_ts=1773504000,
            open_price=2500.0,
            high_price=2501.0,
            low_price=2499.0,
            last_price=2500.2,
        )
        decision = evaluate_market(
            market,
            external,
            cfg,
            current_shares=0.0,
            now_ts=1773503940,
            open_position_count=0,
        )
        self.assertFalse(decision.should_buy)
        self.assertEqual(decision.reason, "dual_side_pair_market")

    def test_skip_when_tail_is_too_late(self) -> None:
        cfg = PaperConfig(run_id="test", pre_min_seconds_remaining=30)
        market = BinaryMarket(
            symbol="sol",
            timeframe="5m",
            slug="sol-updown-5m-1773503700",
            title="Solana Up or Down",
            start_ts=1773503700,
            end_ts=1773504000,
            up_price=0.02,
            down_price=0.98,
            min_order_size=1.0,
            tick_size=0.01,
            active=True,
            closed=False,
            source="test",
        )
        external = BinancePeriodSnapshot(
            symbol="sol",
            timeframe="5m",
            start_ts=1773503700,
            end_ts=1773504000,
            open_price=150.0,
            high_price=150.1,
            low_price=149.9,
            last_price=150.0,
        )
        decision = evaluate_market(
            market,
            external,
            cfg,
            current_shares=0.0,
            now_ts=1773503980,
            open_position_count=0,
        )
        self.assertFalse(decision.should_buy)
        self.assertEqual(decision.reason, "tail")


if __name__ == "__main__":
    unittest.main()
