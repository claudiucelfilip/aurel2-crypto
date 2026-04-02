"""Generate equity curve data for dashboard charts."""

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
    start = date(2021, 1, 1)
    end = date(2026, 3, 31)
    capital = 10000.0

    print("Fetching data...")
    provider = BinanceDataProvider()
    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(), timeframe="1d",
        start_date=datetime(2020, 12, 1), end_date=datetime(2026, 3, 31),
    )
    # Momentum equity curve (100% allocation)
    print("Running momentum backtest...")
    mom_strategy = ShortTermMomentumStrategy(lookback_days=28, rebalance_days=7, switch_threshold=0.03)
    mom_engine = BacktestEngine(initial_capital=capital, transaction_cost_pct=0.001)
    mom_result = mom_engine.run(mom_strategy, prices, start, end)

    # BTC benchmark
    btc_prices = prices[prices["symbol"] == "BTC/USDT"].copy()
    btc_prices["date"] = btc_prices["timestamp"].dt.date
    btc_prices = btc_prices.sort_values("timestamp").drop_duplicates("date", keep="last")
    btc_start_price = float(btc_prices[btc_prices["date"] <= start].iloc[-1]["close"])
    btc_shares = (capital * 0.999) / btc_start_price

    chart_data = {
        "dates": [],
        "momentum": [],
        "btc_benchmark": [],
    }

    for snap in mom_result.snapshots:
        d = snap.date
        mom_val = float(snap.total_value)

        btc_row = btc_prices[btc_prices["date"] <= d]
        btc_price = float(btc_row.iloc[-1]["close"]) if not btc_row.empty else btc_start_price
        btc_val = btc_shares * btc_price

        chart_data["dates"].append(str(d))
        chart_data["momentum"].append(round(mom_val, 2))
        chart_data["btc_benchmark"].append(round(btc_val, 2))

    chart_data["summary"] = {
        "start_date": str(start),
        "end_date": str(end),
        "initial_capital": capital,
        "momentum_final": round(mom_result.final_value, 2),
        "momentum_cagr": round(mom_result.cagr * 100, 1),
        "max_drawdown": round(mom_result.max_drawdown * 100, 1),
        "sharpe": round(mom_result.sharpe_ratio, 2),
        "num_trades": mom_result.num_trades,
        "btc_final": round(chart_data["btc_benchmark"][-1], 2),
    }

    out_path = Path(__file__).parent.parent / "data" / "chart_data.json"
    out_path.write_text(json.dumps(chart_data))
    print(f"Saved {len(chart_data['dates'])} data points to {out_path}")
    print(f"Momentum final: ${chart_data['summary']['momentum_final']:,.2f}")
    print(f"BTC final: ${chart_data['summary']['btc_final']:,.2f}")


if __name__ == "__main__":
    main()
