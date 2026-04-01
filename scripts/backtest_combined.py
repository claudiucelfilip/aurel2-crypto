"""Backtest combined carry + momentum portfolio."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aurel2_crypto.core.assets import CARRY_ASSETS, ASSET_REGISTRY, get_all_symbols
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.engine.backtest_combined import run_combined_backtest


def main():
    start = date(2021, 1, 1)
    end = date(2026, 3, 31)
    capital = 10000.0

    print(f"Fetching data: {start} to {end}")
    provider = BinanceDataProvider()

    # Fetch OHLCV
    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(),
        timeframe="1d",
        start_date=datetime.combine(start, datetime.min.time()),
        end_date=datetime.combine(end, datetime.min.time()),
    )

    # Fetch funding rates
    perp_symbols = [ASSET_REGISTRY[a].perp_symbol for a in CARRY_ASSETS]
    funding_rates = provider.get_multi_funding_rates(
        symbols=perp_symbols,
        start_date=datetime.combine(start, datetime.min.time()),
        end_date=datetime.combine(end, datetime.min.time()),
    )
    print(f"Data: {len(prices)} candles, {len(funding_rates)} funding records\n")

    # Test different allocations
    allocations = [
        (0.30, 0.70, "30/70 carry/momentum"),
        (0.40, 0.60, "40/60 carry/momentum"),
        (0.50, 0.50, "50/50 carry/momentum"),
        (0.60, 0.40, "60/40 carry/momentum"),
        (0.70, 0.30, "70/30 carry/momentum"),
    ]

    best_sharpe = -999
    best_result = None
    best_label = ""

    for carry_pct, mom_pct, label in allocations:
        print(f"\n{'='*70}")
        print(f"ALLOCATION: {label}")
        print(f"{'='*70}")

        result = run_combined_backtest(
            prices=prices,
            funding_rates=funding_rates,
            start_date=start,
            end_date=end,
            initial_capital=capital,
            carry_pct=carry_pct,
            momentum_pct=mom_pct,
        )
        result.print_summary()

        if result.sharpe_ratio > best_sharpe:
            best_sharpe = result.sharpe_ratio
            best_result = result
            best_label = label

    print(f"\n\n{'*'*70}")
    print(f"BEST ALLOCATION: {best_label} (Sharpe {best_sharpe:.2f})")
    print(f"{'*'*70}")

    # Save results
    if best_result:
        output = {
            "strategy": "combined_carry_momentum",
            "start_date": str(start),
            "end_date": str(end),
            "initial_capital": capital,
            "best_allocation": best_label,
            "final_value": round(best_result.final_value, 2),
            "total_return_pct": round(best_result.total_return * 100, 1),
            "cagr_pct": round(best_result.cagr * 100, 1),
            "max_drawdown_pct": round(best_result.max_drawdown * 100, 1),
            "sharpe_ratio": round(best_result.sharpe_ratio, 2),
            "carry_final": round(best_result.carry_result.final_value, 2),
            "momentum_final": round(best_result.momentum_result.final_value, 2),
            "benchmark_final": round(best_result.benchmark_final, 2) if best_result.benchmark_final else None,
            "all_allocations": {
                label: {
                    "cagr": round(run_combined_backtest(
                        prices, funding_rates, start, end, capital, cp, mp,
                    ).cagr * 100, 1),
                }
                for cp, mp, label in allocations
            },
        }

        out_path = Path(__file__).parent.parent / "data" / "combined_backtest.json"
        out_path.write_text(json.dumps(output, indent=2))
        print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
