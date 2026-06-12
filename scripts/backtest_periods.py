"""Backtest combined portfolio across different time periods."""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aurel2_crypto.core.assets import CARRY_ASSETS, ASSET_REGISTRY, get_all_symbols
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.engine.backtest_combined import run_combined_backtest


def main():
    # Fetch all data once (widest range)
    provider = BinanceDataProvider()
    print("Fetching data...")
    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(),
        timeframe="1d",
        start_date=datetime(2020, 1, 1),
        end_date=datetime(2026, 3, 31),
    )
    perp_symbols = [ASSET_REGISTRY[a].perp_symbol for a in CARRY_ASSETS]
    funding_rates = provider.get_multi_funding_rates(
        symbols=perp_symbols,
        start_date=datetime(2020, 1, 1),
        end_date=datetime(2026, 3, 31),
    )
    print(f"Data: {len(prices)} candles, {len(funding_rates)} funding records\n")

    periods = [
        ("5y", date(2021, 4, 1), date(2026, 3, 31)),
        ("1y", date(2025, 4, 1), date(2026, 3, 31)),
        ("1m", date(2026, 3, 1), date(2026, 3, 31)),
    ]

    print(f"{'Period':<8} {'Start':>12} {'End':>12} {'Combined CAGR':>15} {'Mom CAGR':>10} {'Carry CAGR':>12} {'MaxDD':>8} {'vs BTC':>10}")
    print("-" * 90)

    for label, start, end in periods:
        result = run_combined_backtest(
            prices, funding_rates, start, end,
            initial_capital=10000.0,
            carry_pct=0.30,
            momentum_pct=0.70,
        )
        bench_return = ((result.benchmark_final / 10000) - 1) if result.benchmark_final else 0
        years = (end - start).days / 365.25
        bench_cagr = (result.benchmark_final / 10000) ** (1 / years) - 1 if result.benchmark_final and years > 0 else 0
        alpha = result.cagr - bench_cagr

        print(
            f"{label:<8} {str(start):>12} {str(end):>12} "
            f"{result.cagr:>14.1%} {result.momentum_result.cagr:>9.1%} "
            f"{result.carry_result.cagr:>11.1%} {result.max_drawdown:>7.1%} "
            f"{alpha:>+9.1%}"
        )


if __name__ == "__main__":
    main()
