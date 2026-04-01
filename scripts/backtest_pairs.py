"""Backtest the BTC/ETH pairs trading strategy."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aurel2_crypto.core.assets import get_all_symbols
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.engine.backtest_pairs import PairsBacktestEngine
from aurel2_crypto.strategies.pairs import PairsTradingStrategy


def main():
    start = date(2021, 1, 1)
    end = date(2026, 3, 31)
    capital = 10000.0

    print(f"Fetching historical data: {start} to {end}")
    provider = BinanceDataProvider()
    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(),
        timeframe="1d",
        start_date=datetime.combine(start, datetime.min.time()),
        end_date=datetime.combine(end, datetime.min.time()),
    )
    print(f"Fetched {len(prices)} candles\n")

    # Test different z-score thresholds and lookback windows
    configs = [
        (1.5, 0.5, 60, "z=1.5, lookback=60d"),
        (2.0, 0.5, 60, "z=2.0, lookback=60d"),
        (2.5, 0.5, 60, "z=2.5, lookback=60d"),
        (2.0, 0.5, 30, "z=2.0, lookback=30d"),
        (2.0, 0.5, 90, "z=2.0, lookback=90d"),
        (2.0, 0.0, 60, "z=2.0, exit=0.0, lookback=60d"),
    ]

    for zscore_entry, zscore_exit, lookback, label in configs:
        print(f"\n{'='*60}")
        print(f"CONFIG: {label}")
        print(f"{'='*60}")

        strategy = PairsTradingStrategy(
            zscore_entry=zscore_entry,
            zscore_exit=zscore_exit,
            lookback_days=lookback,
            max_hold_days=30,
        )

        engine = PairsBacktestEngine(
            initial_capital=capital,
            fee_pct=0.0005,
            position_pct=0.90,
        )

        result = engine.run(strategy, prices, start, end)
        result.print_summary()

        # Print individual trades for best config
        if label == "z=2.0, lookback=60d" and result.trades:
            print(f"\nTrade details ({len(result.trades)} trades):")
            for t in result.trades:
                direction = "Long BTC/Short ETH" if t.side == "long_a_short_b" else "Short BTC/Long ETH"
                held = (t.exit_date - t.entry_date).days
                print(
                    f"  {t.entry_date} → {t.exit_date} ({held}d) "
                    f"{direction} z={t.entry_zscore:+.1f}→{t.exit_zscore:+.1f} "
                    f"P&L: ${t.pnl:+,.0f} ({t.pnl_pct:+.1%})"
                )

    # Save best result
    print("\n\nSaving results...")
    best_strategy = PairsTradingStrategy(zscore_entry=2.0, zscore_exit=0.5, lookback_days=60)
    best_engine = PairsBacktestEngine(initial_capital=capital)
    best_result = best_engine.run(best_strategy, prices, start, end)

    output = {
        "strategy": "pairs_btc_eth",
        "start_date": str(start),
        "end_date": str(end),
        "initial_capital": capital,
        "final_value": round(best_result.final_value, 2),
        "total_return_pct": round(best_result.total_return * 100, 1),
        "cagr_pct": round(best_result.cagr * 100, 1),
        "max_drawdown_pct": round(best_result.max_drawdown * 100, 1),
        "sharpe_ratio": round(best_result.sharpe_ratio, 2),
        "num_trades": best_result.num_trades,
        "win_rate": round(best_result.win_rate * 100, 1),
        "avg_trade_pnl_pct": round(best_result.avg_trade_pnl_pct * 100, 2),
        "avg_hold_days": round(best_result.avg_hold_days, 1),
        "pct_time_in_trade": round(best_result.pct_time_in_trade * 100, 1),
        "benchmark_final": round(best_result.benchmark_final, 2) if best_result.benchmark_final else None,
    }

    out_path = Path(__file__).parent.parent / "data" / "pairs_backtest.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
