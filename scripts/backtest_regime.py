"""Backtest combined portfolio with vs without regime overlay."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aurel2_crypto.core.assets import CARRY_ASSETS, ASSET_REGISTRY, get_all_symbols
from aurel2_crypto.data.providers.binance import BinanceDataProvider
import pandas as pd

from aurel2_crypto.data.onchain import RegimeDetector
from aurel2_crypto.engine.backtest_combined import run_combined_backtest


def main():
    start = date(2021, 1, 1)
    end = date(2026, 3, 31)
    capital = 10000.0
    carry_pct = 0.30
    mom_pct = 0.70

    print(f"Fetching data: {start} to {end}")
    provider = BinanceDataProvider()

    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(),
        timeframe="1d",
        start_date=datetime.combine(start, datetime.min.time()),
        end_date=datetime.combine(end, datetime.min.time()),
    )

    perp_symbols = [ASSET_REGISTRY[a].perp_symbol for a in CARRY_ASSETS]
    funding_rates = provider.get_multi_funding_rates(
        symbols=perp_symbols,
        start_date=datetime.combine(start, datetime.min.time()),
        end_date=datetime.combine(end, datetime.min.time()),
    )
    print(f"Data: {len(prices)} candles, {len(funding_rates)} funding records\n")

    # 1. Without regime overlay (baseline)
    print("=" * 70)
    print("WITHOUT REGIME OVERLAY (baseline)")
    print("=" * 70)
    baseline = run_combined_backtest(
        prices, funding_rates, start, end, capital, carry_pct, mom_pct,
    )
    baseline.print_summary()

    # 2. With regime overlay
    print("\n" + "=" * 70)
    print("WITH REGIME OVERLAY (200DMA position sizing)")
    print("=" * 70)
    detector = RegimeDetector(ma_period=200)
    with_regime = run_combined_backtest(
        prices, funding_rates, start, end, capital, carry_pct, mom_pct,
        regime_detector=detector,
    )
    with_regime.print_summary()

    # 3. Show regime history
    print("\nRegime history (sampled monthly):")
    regime_history = detector.get_regime_history(prices, start, end)
    if not regime_history.empty:
        regime_history["date"] = pd.to_datetime(regime_history["date"])
        monthly = regime_history.set_index("date").resample("ME").last()
        for _, row in monthly.iterrows():
            print(
                f"  {row.name.date()}: {row['regime']:<14} "
                f"price/200DMA={row['ratio']:.2f}  "
                f"multiplier={row['position_multiplier']:.0%}"
            )

        # Regime distribution
        print(f"\nRegime distribution:")
        dist = regime_history["regime"].value_counts(normalize=True)
        for regime, pct in dist.items():
            print(f"  {regime:<14}: {pct:.0%}")

    # 4. Comparison
    print(f"\n{'*'*70}")
    print("COMPARISON")
    print(f"{'*'*70}")
    print(f"  {'Metric':<20} {'No Overlay':>15} {'With Overlay':>15} {'Diff':>10}")
    print(f"  {'-'*60}")
    print(f"  {'CAGR':<20} {baseline.cagr:>14.1%} {with_regime.cagr:>14.1%} {with_regime.cagr - baseline.cagr:>+9.1%}")
    print(f"  {'Max Drawdown':<20} {baseline.max_drawdown:>14.1%} {with_regime.max_drawdown:>14.1%} {with_regime.max_drawdown - baseline.max_drawdown:>+9.1%}")
    print(f"  {'Sharpe':<20} {baseline.sharpe_ratio:>14.2f} {with_regime.sharpe_ratio:>14.2f} {with_regime.sharpe_ratio - baseline.sharpe_ratio:>+9.2f}")
    print(f"  {'Final Value':<20} ${baseline.final_value:>13,.0f} ${with_regime.final_value:>13,.0f}")

    # Save
    output = {
        "baseline": {
            "final_value": round(baseline.final_value, 2),
            "cagr_pct": round(baseline.cagr * 100, 1),
            "max_drawdown_pct": round(baseline.max_drawdown * 100, 1),
            "sharpe": round(baseline.sharpe_ratio, 2),
        },
        "with_regime": {
            "final_value": round(with_regime.final_value, 2),
            "cagr_pct": round(with_regime.cagr * 100, 1),
            "max_drawdown_pct": round(with_regime.max_drawdown * 100, 1),
            "sharpe": round(with_regime.sharpe_ratio, 2),
        },
    }
    out_path = Path(__file__).parent.parent / "data" / "regime_backtest.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
