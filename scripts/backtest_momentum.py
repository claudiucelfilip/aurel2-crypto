"""Backtest the short-term momentum strategy on historical Binance data."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aurel2_crypto.core.assets import get_all_symbols
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.engine.backtest import BacktestEngine
from aurel2_crypto.strategies.momentum import ShortTermMomentumStrategy


def main():
    # Parameters
    start = date(2021, 1, 1)
    end = date(2026, 3, 31)
    capital = 10000.0

    print(f"Fetching historical data from Binance: {start} to {end}")
    print(f"Symbols: {get_all_symbols()}")

    provider = BinanceDataProvider()
    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(),
        timeframe="1d",
        start_date=datetime.combine(start, datetime.min.time()),
        end_date=datetime.combine(end, datetime.min.time()),
    )

    print(f"Fetched {len(prices)} candles across {prices['symbol'].nunique()} symbols")
    print(f"Date range: {prices['timestamp'].min()} to {prices['timestamp'].max()}")
    print()

    # Run multiple lookback periods
    lookbacks = [14, 21, 28, 42]

    for lb in lookbacks:
        print(f"\n{'='*60}")
        print(f"LOOKBACK: {lb} days")
        print(f"{'='*60}")

        strategy = ShortTermMomentumStrategy(
            lookback_days=lb,
            rebalance_days=7,
            switch_threshold=0.03,
        )

        engine = BacktestEngine(
            initial_capital=capital,
            transaction_cost_pct=0.001,  # 0.1% Binance spot fee
            benchmark_symbol="BTC/USDT",
        )

        result = engine.run(strategy, prices, start, end)
        result.print_summary()

    # Save best result details
    print("\n\nSaving detailed results...")
    best_strategy = ShortTermMomentumStrategy(lookback_days=28, rebalance_days=7)
    best_engine = BacktestEngine(initial_capital=capital, transaction_cost_pct=0.001)
    best_result = best_engine.run(best_strategy, prices, start, end)

    output = {
        "strategy": "momentum",
        "start_date": str(start),
        "end_date": str(end),
        "initial_capital": capital,
        "final_value": best_result.final_value,
        "total_return_pct": round(best_result.total_return * 100, 1),
        "cagr_pct": round(best_result.cagr * 100, 1),
        "max_drawdown_pct": round(best_result.max_drawdown * 100, 1),
        "sharpe_ratio": round(best_result.sharpe_ratio, 2),
        "sortino_ratio": round(best_result.sortino_ratio, 2),
        "num_trades": best_result.num_trades,
        "win_rate": round(best_result.win_rate * 100, 1),
        "benchmark_final": best_result.benchmark_final,
        "benchmark_return_pct": round(
            ((best_result.benchmark_final / capital) - 1) * 100, 1
        ) if best_result.benchmark_final else None,
    }

    out_path = Path(__file__).parent.parent / "data" / "momentum_backtest.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
