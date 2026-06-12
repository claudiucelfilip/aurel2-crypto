"""Backtest: daily absolute momentum check vs weekly-only."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pandas as pd
from aurel2_crypto.core.assets import get_all_symbols, ASSET_REGISTRY, MOMENTUM_ASSETS
from aurel2_crypto.core.models import CryptoAsset
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.data.momentum import calculate_momentum_scores
from aurel2_crypto.engine.backtest import BacktestEngine
from aurel2_crypto.strategies.momentum import ShortTermMomentumStrategy
from aurel2_crypto.strategies.base import BaseStrategy, StrategySignal
from aurel2_crypto.core.models import SignalAction


class DailyFilterMomentumStrategy(BaseStrategy):
    """Momentum with daily absolute momentum exit check.

    Weekly: full rebalance (pick best coin, switch if needed)
    Daily: check if ALL coins have negative momentum → go to cash immediately
    """
    name = "momentum_daily_filter"

    def __init__(self, lookback_days=28, rebalance_days=7, switch_threshold=0.03):
        self.lookback_days = lookback_days
        self.rebalance_days = rebalance_days
        self.switch_threshold = switch_threshold
        self.assets = MOMENTUM_ASSETS
        self._inner = ShortTermMomentumStrategy(lookback_days, rebalance_days, switch_threshold)

    def generate_signal(self, prices, calc_date, current_holding, context=None):
        is_monday = calc_date.weekday() == 0

        if is_monday:
            # Full rebalance on Mondays
            return self._inner.generate_signal(prices, calc_date, current_holding)

        # Daily: only check absolute momentum filter (all negative → exit)
        if not current_holding or current_holding == CryptoAsset.USDT:
            return StrategySignal(
                strategy_name=self.name, date=calc_date,
                action=SignalAction.HOLD, asset_id=current_holding,
                confidence=0.5, reasoning="Daily check: no position, waiting for Monday",
            )

        scores = calculate_momentum_scores(prices, self.assets, calc_date, self.lookback_days)
        if not scores:
            return StrategySignal(
                strategy_name=self.name, date=calc_date,
                action=SignalAction.HOLD, asset_id=current_holding,
                confidence=0.3, reasoning="Daily check: no data",
            )

        all_negative = all(s.momentum <= 0 for s in scores.values())
        if all_negative:
            return StrategySignal(
                strategy_name=self.name, date=calc_date,
                action=SignalAction.SELL, asset_id=CryptoAsset.USDT,
                confidence=0.9,
                reasoning=f"DAILY EXIT: all momentum negative",
                metadata={"scores": {k.value: v.momentum for k, v in scores.items()}},
            )

        return StrategySignal(
            strategy_name=self.name, date=calc_date,
            action=SignalAction.HOLD, asset_id=current_holding,
            confidence=0.6, reasoning="Daily check: momentum still positive",
        )

    def get_rebalance_dates(self, start_date, end_date):
        """Daily dates (for the filter check)."""
        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += timedelta(days=1)
        return dates


def main():
    start = date(2021, 1, 1)
    end = date(2026, 3, 31)
    capital = 10000.0

    print("Fetching data...")
    provider = BinanceDataProvider()
    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(), timeframe="1d",
        start_date=datetime(2020, 12, 1), end_date=datetime(2026, 3, 31),
    )
    print(f"Fetched {len(prices)} candles\n")

    configs = [
        ("Weekly only + 10% stop", ShortTermMomentumStrategy(28, 7, 0.03), 0.10),
        ("Daily filter + 10% stop", DailyFilterMomentumStrategy(28, 7, 0.03), 0.10),
        ("Daily filter + 15% stop", DailyFilterMomentumStrategy(28, 7, 0.03), 0.15),
        ("Daily filter + no stop", DailyFilterMomentumStrategy(28, 7, 0.03), 0.0),
    ]

    print(f"{'Config':<30} {'Final':>12} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8} {'Trades':>8}")
    print("-" * 78)

    for label, strategy, stop in configs:
        engine = BacktestEngine(initial_capital=capital, transaction_cost_pct=0.001, trailing_stop_pct=stop)
        result = engine.run(strategy, prices, start, end)
        print(
            f"{label:<30} ${result.final_value:>11,.0f} {result.cagr:>7.1%} "
            f"{result.max_drawdown:>7.1%} {result.sharpe_ratio:>7.2f} {result.num_trades:>7}"
        )

    # BTC benchmark
    engine = BacktestEngine(initial_capital=capital, transaction_cost_pct=0.001)
    strategy = ShortTermMomentumStrategy(28, 7, 0.03)
    result = engine.run(strategy, prices, start, end)
    if result.benchmark_final:
        years = (end - start).days / 365.25
        btc_cagr = (result.benchmark_final / capital) ** (1 / years) - 1
        print(f"\n{'BTC B&H':<30} ${result.benchmark_final:>11,.0f} {btc_cagr:>7.1%}")


if __name__ == "__main__":
    main()
