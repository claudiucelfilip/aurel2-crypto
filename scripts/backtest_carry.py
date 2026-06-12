"""Backtest the funding rate carry strategy on historical Binance data."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aurel2_crypto.core.assets import CARRY_ASSETS, ASSET_REGISTRY, get_all_symbols
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.engine.backtest_carry import CarryBacktestEngine


def main():
    start = date(2021, 1, 1)
    end = date(2026, 3, 31)
    capital = 10000.0

    provider = BinanceDataProvider()

    # Fetch funding rates for BTC and ETH perps
    perp_symbols = [ASSET_REGISTRY[a].perp_symbol for a in CARRY_ASSETS]
    print(f"Fetching funding rates: {perp_symbols}")
    print(f"Period: {start} to {end}")

    funding_rates = provider.get_multi_funding_rates(
        symbols=perp_symbols,
        start_date=datetime.combine(start, datetime.min.time()),
        end_date=datetime.combine(end, datetime.min.time()),
    )
    print(f"Fetched {len(funding_rates)} funding rate records")

    if not funding_rates.empty:
        for symbol in perp_symbols:
            sym_rates = funding_rates[funding_rates["symbol"] == symbol]
            avg_rate = sym_rates["funding_rate"].mean()
            print(f"  {symbol}: {len(sym_rates)} records, avg rate: {avg_rate:.4%}/8h ({avg_rate * 3 * 365:.1%} annualized)")

    # Fetch spot prices for benchmark
    print("\nFetching spot prices for benchmark...")
    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(),
        timeframe="1d",
        start_date=datetime.combine(start, datetime.min.time()),
        end_date=datetime.combine(end, datetime.min.time()),
    )

    # Test different entry thresholds
    thresholds = [
        (0.00005, "0.005%/8h (~5.5% ann)"),
        (0.0001, "0.01%/8h (~11% ann)"),
        (0.0002, "0.02%/8h (~22% ann)"),
        (0.0005, "0.05%/8h (~55% ann)"),
    ]

    for entry_rate, label in thresholds:
        print(f"\n{'='*60}")
        print(f"ENTRY THRESHOLD: {label}")
        print(f"{'='*60}")

        engine = CarryBacktestEngine(
            initial_capital=capital,
            entry_rate=entry_rate,
            exit_rate=-0.0001,
            spot_fee_pct=0.001,
            futures_fee_pct=0.0005,
            position_pct=0.90,
        )

        result = engine.run(funding_rates, prices, start, end)
        result.print_summary()

    # Save best result
    print("\n\nSaving results...")
    best_engine = CarryBacktestEngine(
        initial_capital=capital,
        entry_rate=0.0001,
        exit_rate=-0.0001,
    )
    best_result = best_engine.run(funding_rates, prices, start, end)

    output = {
        "strategy": "funding_carry",
        "start_date": str(start),
        "end_date": str(end),
        "initial_capital": capital,
        "final_value": round(best_result.final_value, 2),
        "total_return_pct": round(best_result.total_return * 100, 1),
        "cagr_pct": round(best_result.cagr * 100, 1),
        "max_drawdown_pct": round(best_result.max_drawdown * 100, 1),
        "sharpe_ratio": round(best_result.sharpe_ratio, 2),
        "net_funding": round(best_result.net_funding, 2),
        "funding_collected": round(best_result.total_funding_collected, 2),
        "funding_paid": round(best_result.total_funding_paid, 2),
        "carry_periods": best_result.num_carry_periods,
        "pct_time_in_carry": round(best_result.pct_time_in_carry * 100, 1),
        "benchmark_final": round(best_result.benchmark_final, 2) if best_result.benchmark_final else None,
    }

    out_path = Path(__file__).parent.parent / "data" / "carry_backtest.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
