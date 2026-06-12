"""Generate equity curve data for dashboard charts (with USDT yield)."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aurel2_crypto.core.assets import get_all_symbols
from aurel2_crypto.core.models import CryptoAsset
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.engine.backtest import BacktestEngine
from aurel2_crypto.strategies.momentum import ShortTermMomentumStrategy


USDT_APY = 0.06  # 6% APY on idle USDT


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

    print("Running momentum backtest...")
    mom_strategy = ShortTermMomentumStrategy(lookback_days=28, rebalance_days=7, switch_threshold=0.03)
    mom_engine = BacktestEngine(initial_capital=capital, transaction_cost_pct=0.001, trailing_stop_pct=0.10)
    mom_result = mom_engine.run(mom_strategy, prices, start, end)

    # BTC benchmark
    btc_prices = prices[prices["symbol"] == "BTC/USDT"].copy()
    btc_prices["date"] = btc_prices["timestamp"].dt.date
    btc_prices = btc_prices.sort_values("timestamp").drop_duplicates("date", keep="last")
    btc_start_price = float(btc_prices[btc_prices["date"] <= start].iloc[-1]["close"])
    btc_shares = (capital * 0.999) / btc_start_price

    # Build chart with USDT yield applied to cash periods
    daily_yield_rate = (1 + USDT_APY) ** (1 / 365) - 1
    chart_data = {
        "dates": [],
        "momentum": [],       # With yield
        "momentum_no_yield": [],  # Without yield (for comparison)
        "btc_benchmark": [],
    }

    yield_accumulated = 0.0
    prev_snap = None
    days_in_cash = 0

    for snap in mom_result.snapshots:
        d = snap.date
        raw_val = float(snap.total_value)

        # Detect if in cash (no positions)
        in_cash = len(snap.positions) == 0
        if in_cash and prev_snap is not None:
            # Calculate days since last snapshot
            days_diff = (snap.date - prev_snap.date).days
            # Apply yield on cash portion
            cash_amount = float(snap.cash)
            for _ in range(days_diff):
                yield_accumulated += cash_amount * daily_yield_rate
            days_in_cash += days_diff

        val_with_yield = raw_val + yield_accumulated

        btc_row = btc_prices[btc_prices["date"] <= d]
        btc_price = float(btc_row.iloc[-1]["close"]) if not btc_row.empty else btc_start_price
        btc_val = btc_shares * btc_price

        chart_data["dates"].append(str(d))
        chart_data["momentum"].append(round(val_with_yield, 2))
        chart_data["momentum_no_yield"].append(round(raw_val, 2))
        chart_data["btc_benchmark"].append(round(btc_val, 2))

        prev_snap = snap

    final_with_yield = chart_data["momentum"][-1]
    final_no_yield = chart_data["momentum_no_yield"][-1]
    years = (end - start).days / 365.25
    cagr_with = (final_with_yield / capital) ** (1 / years) - 1
    cagr_without = (final_no_yield / capital) ** (1 / years) - 1

    # Max drawdown (with yield)
    peak = chart_data["momentum"][0]
    max_dd = 0
    for v in chart_data["momentum"]:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    total_days = (end - start).days
    chart_data["summary"] = {
        "start_date": str(start),
        "end_date": str(end),
        "initial_capital": capital,
        "momentum_final": round(final_with_yield, 2),
        "momentum_cagr": round(cagr_with * 100, 1),
        "momentum_no_yield_final": round(final_no_yield, 2),
        "momentum_no_yield_cagr": round(cagr_without * 100, 1),
        "max_drawdown": round(max_dd * 100, 1),
        "sharpe": round(mom_result.sharpe_ratio, 2),
        "num_trades": mom_result.num_trades,
        "btc_final": round(chart_data["btc_benchmark"][-1], 2),
        "yield_earned": round(yield_accumulated, 2),
        "usdt_apy": USDT_APY,
        "pct_time_in_cash": round(days_in_cash / total_days * 100, 1),
    }

    out_path = Path(__file__).parent.parent / "data" / "chart_data.json"
    out_path.write_text(json.dumps(chart_data))
    s = chart_data["summary"]
    print(f"Saved {len(chart_data['dates'])} data points to {out_path}")
    print(f"With yield:    ${s['momentum_final']:>11,.2f}  CAGR: {s['momentum_cagr']:.1f}%")
    print(f"Without yield: ${s['momentum_no_yield_final']:>11,.2f}  CAGR: {s['momentum_no_yield_cagr']:.1f}%")
    print(f"Yield earned:  ${s['yield_earned']:>11,.2f}  ({s['pct_time_in_cash']:.0f}% time in cash)")
    print(f"BTC B&H:       ${s['btc_final']:>11,.2f}")


if __name__ == "__main__":
    main()
