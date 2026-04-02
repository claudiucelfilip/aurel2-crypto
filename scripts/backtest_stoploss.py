"""Backtest momentum strategy with different trailing stop-loss levels."""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aurel2_crypto.core.assets import get_all_symbols
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.engine.backtest import BacktestEngine
from aurel2_crypto.strategies.momentum import ShortTermMomentumStrategy


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

    stop_levels = [0.0, 0.10, 0.15, 0.20, 0.25, 0.30]

    print(f"{'Stop %':<10} {'Final':>12} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8} {'Trades':>8} {'Win%':>8}")
    print("-" * 66)

    for stop in stop_levels:
        strategy = ShortTermMomentumStrategy(lookback_days=28, rebalance_days=7, switch_threshold=0.03)
        engine = BacktestEngine(
            initial_capital=capital,
            transaction_cost_pct=0.001,
            trailing_stop_pct=stop,
        )
        result = engine.run(strategy, prices, start, end)

        label = f"{stop:.0%}" if stop > 0 else "None"
        print(
            f"{label:<10} ${result.final_value:>11,.0f} {result.cagr:>7.1%} "
            f"{result.max_drawdown:>7.1%} {result.sharpe_ratio:>7.2f} "
            f"{result.num_trades:>7} {result.win_rate:>7.0%}"
        )

    # BTC benchmark
    engine = BacktestEngine(initial_capital=capital, transaction_cost_pct=0.001)
    strategy = ShortTermMomentumStrategy(lookback_days=28, rebalance_days=7)
    result = engine.run(strategy, prices, start, end)
    if result.benchmark_final:
        years = (end - start).days / 365.25
        btc_cagr = (result.benchmark_final / capital) ** (1 / years) - 1
        print(f"\n{'BTC B&H':<10} ${result.benchmark_final:>11,.0f} {btc_cagr:>7.1%}")


if __name__ == "__main__":
    main()
