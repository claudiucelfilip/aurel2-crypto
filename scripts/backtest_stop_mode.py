"""Backtest: stop-loss evaluation mode (intraday wick vs daily close) x stop level x daily-exit deadband.

Motivation: the 10% trailing stop was validated on daily closes, but the live
daemon checks every 5 minutes — intraday wicks make it effectively tighter.
This sim replicates the daemon's rules on daily OHLC:
  - Monday: full momentum rebalance (28d lookback, 3% switch threshold,
    all-negative -> USDT, current-negative -> switch to best).
  - Tue-Sun: daily exit if ALL momentums <= -deadband.
  - Trailing stop checked per day: 'intraday' mode uses high/low (high-first,
    fill at stop level), 'close' mode uses closes only, 'none' disables it.
  - 0.1% fee per side, 6% APY on idle USDT (Flexible Earn).
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd

from aurel2_crypto.core.assets import ASSET_REGISTRY, MOMENTUM_ASSETS, get_all_symbols
from aurel2_crypto.data.providers.binance import BinanceDataProvider

FEE = 0.001
USDT_APY = 0.06
LOOKBACK = 28
SWITCH_THRESHOLD = 0.03


def load_series(prices: pd.DataFrame):
    """Per-symbol dicts date -> (close, high, low)."""
    out = {}
    for asset_id in MOMENTUM_ASSETS:
        sym = ASSET_REGISTRY[asset_id].symbol
        ap = prices[prices["symbol"] == sym].sort_values("timestamp")
        d = {}
        for _, row in ap.iterrows():
            d[pd.Timestamp(row["timestamp"]).date()] = (
                float(row["close"]), float(row["high"]), float(row["low"]),
            )
        out[asset_id] = d
    return out


def momentum(series, asset_id, day, lookback=LOOKBACK):
    """Close-to-close momentum, mirroring data/momentum.py (last close <= date)."""
    s = series[asset_id]
    cur = s.get(day)
    past = s.get(day - timedelta(days=lookback))
    if not cur or not past or past[0] <= 0:
        return None
    return (cur[0] - past[0]) / past[0]


def run(series, start, end, stop_mode, stop_pct, deadband, capital=10000.0):
    cash, shares, holding, hwm = capital, 0.0, None, 0.0
    daily_yield = (1 + USDT_APY) ** (1 / 365) - 1
    equity_curve, trades = [], 0

    day = start
    while day <= end:
        bar = series[holding].get(day) if holding else None

        # 1. Trailing stop
        if holding and bar and stop_mode != "none":
            close, high, low = bar
            if stop_mode == "intraday":
                hwm = max(hwm, high)
                level = hwm * (1 - stop_pct)
                if low <= level:
                    cash = shares * level * (1 - FEE)
                    shares, holding, hwm = 0.0, None, 0.0
                    trades += 1
            else:  # close mode
                hwm = max(hwm, close)
                if close <= hwm * (1 - stop_pct):
                    cash = shares * close * (1 - FEE)
                    shares, holding, hwm = 0.0, None, 0.0
                    trades += 1

        scores = {a: momentum(series, a, day) for a in MOMENTUM_ASSETS}
        scores = {a: m for a, m in scores.items() if m is not None}

        if scores:
            best, best_m = max(scores.items(), key=lambda kv: kv[1])
            all_neg = all(m <= 0 for m in scores.values())
            all_below_band = all(m <= -deadband for m in scores.values())

            if day.weekday() == 0:  # Monday rebalance
                target = None if all_neg else best
                if holding and target and holding in scores:
                    cur_m = scores[holding]
                    if cur_m >= 0 and (best_m - cur_m) <= SWITCH_THRESHOLD:
                        target = holding
                if target != holding:
                    px = series[holding].get(day) if holding else None
                    if holding and px:
                        cash = shares * px[0] * (1 - FEE)
                        shares, holding = 0.0, None
                        trades += 1
                    tpx = series[target].get(day) if target else None
                    if target and tpx:
                        shares = cash * (1 - FEE) / tpx[0]
                        cash, holding, hwm = 0.0, target, tpx[0]
                        trades += 1
            elif holding and all_below_band:  # daily exit
                px = series[holding].get(day)
                if px:
                    cash = shares * px[0] * (1 - FEE)
                    shares, holding, hwm = 0.0, None, 0.0
                    trades += 1

        if not holding and cash > 0:
            cash *= 1 + daily_yield

        px = series[holding].get(day) if holding else None
        if holding and px:
            equity_curve.append(cash + shares * px[0])
        elif holding:
            equity_curve.append(np.nan)  # missing bar, ffill later
        else:
            equity_curve.append(cash)
        day += timedelta(days=1)

    eq = pd.Series(equity_curve).ffill()
    rets = eq.pct_change().dropna()
    final = float(eq.iloc[-1])
    years = (end - start).days / 365.25
    cagr = (final / capital) ** (1 / years) - 1 if years > 0 else 0
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    sharpe = float(rets.mean() / rets.std() * np.sqrt(365)) if rets.std() > 0 else 0
    return final, cagr, dd, sharpe, trades


def main():
    provider = BinanceDataProvider()
    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(), timeframe="1d",
        start_date=datetime(2020, 11, 1), end_date=datetime(2026, 8, 25),
    )
    series = load_series(prices)

    windows = {
        "full 2021-2026.08": (date(2021, 1, 4), date(2026, 8, 24)),
        "last 12mo": (date(2025, 8, 25), date(2026, 8, 24)),
        "live window": (date(2026, 4, 2), date(2026, 8, 24)),
    }

    configs = []
    for mode in ("intraday", "close"):
        for stop in (0.10, 0.15, 0.20):
            for band in (0.0, 0.03, 0.05):
                configs.append((mode, stop, band))
    for band in (0.0, 0.03, 0.05):
        configs.append(("none", 0.0, band))

    for wname, (start, end) in windows.items():
        print(f"\n=== {wname} ({start} -> {end}) ===")
        print(f"{'mode':<9} {'stop':>5} {'band':>5} {'final':>10} {'CAGR':>8} {'maxDD':>7} {'Sharpe':>7} {'trades':>7}")
        for mode, stop, band in configs:
            final, cagr, dd, sharpe, trades = run(series, start, end, mode, stop, band)
            print(f"{mode:<9} {stop:>5.0%} {band:>5.0%} {final:>10,.0f} {cagr:>8.1%} {dd:>7.1%} {sharpe:>7.2f} {trades:>7}")


if __name__ == "__main__":
    main()
