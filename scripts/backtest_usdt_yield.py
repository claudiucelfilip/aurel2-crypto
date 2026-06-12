"""Calculate impact of USDT Flexible Earn yield on idle cash periods."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd
from aurel2_crypto.core.assets import get_all_symbols, ASSET_REGISTRY, MOMENTUM_ASSETS
from aurel2_crypto.core.models import CryptoAsset
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.data.momentum import calculate_momentum_scores, rank_by_momentum


def run_backtest_with_yield(
    prices, start, end, capital=10000.0, stop_pct=0.10,
    lookback=28, fee_pct=0.001, usdt_apy=0.0,
):
    """Backtest momentum with optional USDT yield on idle cash."""
    all_days = []
    d = start
    while d <= end:
        all_days.append(d)
        d += timedelta(days=1)

    rebalance_set = set()
    cur = start
    while cur.weekday() != 0:
        cur += timedelta(days=1)
    while cur <= end:
        rebalance_set.add(cur)
        cur += timedelta(days=7)

    cash = capital
    holding = None
    shares = 0.0
    hwm = 0.0
    snapshots = []
    days_in_cash = 0
    total_yield_earned = 0.0
    daily_yield_rate = (1 + usdt_apy) ** (1 / 365) - 1

    def get_price(symbol, calc_date):
        ap = prices[prices["symbol"] == symbol].copy()
        if ap.empty:
            return None
        ap["date"] = pd.to_datetime(ap["timestamp"]).dt.date
        av = ap[ap["date"] <= calc_date].sort_values("timestamp")
        return float(av.iloc[-1]["close"]) if not av.empty else None

    def calc_momentum(symbol, calc_date):
        ap = prices[prices["symbol"] == symbol].copy()
        if ap.empty:
            return None
        ap["date"] = pd.to_datetime(ap["timestamp"]).dt.date
        ap = ap[ap["date"] <= calc_date].sort_values("timestamp")
        lookback_date = calc_date - timedelta(days=lookback)
        past = ap[ap["date"] <= lookback_date]
        if past.empty or ap.empty:
            return None
        cp = float(ap.iloc[-1]["close"])
        pp = float(past.iloc[-1]["close"])
        if pp <= 0:
            return None
        return (cp - pp) / pp

    def sell():
        nonlocal cash, holding, shares, hwm
        if not holding:
            return
        price = get_price(holding, calc_date)
        if price and shares > 0:
            proceeds = shares * price
            fee = proceeds * fee_pct
            cash += proceeds - fee
        holding = None
        shares = 0.0
        hwm = 0.0

    def pv():
        total = cash
        if holding:
            price = get_price(holding, calc_date)
            if price:
                total += shares * price
        return total

    for calc_date in all_days:
        is_rebalance = calc_date in rebalance_set

        # USDT yield: earn daily interest when in cash
        if not holding and usdt_apy > 0 and cash > 0:
            daily_interest = cash * daily_yield_rate
            cash += daily_interest
            total_yield_earned += daily_interest
            days_in_cash += 1

        # Trailing stop
        if stop_pct > 0 and holding:
            price = get_price(holding, calc_date)
            if price:
                hwm = max(hwm, price)
                if hwm > 0 and (hwm - price) / hwm >= stop_pct:
                    sell()

        if is_rebalance:
            scores = {}
            for asset_id in MOMENTUM_ASSETS:
                asset = ASSET_REGISTRY[asset_id]
                m = calc_momentum(asset.symbol, calc_date)
                if m is not None:
                    scores[asset_id] = m

            if not scores:
                snapshots.append(pv())
                continue

            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            all_negative = all(s <= 0 for _, s in ranked)
            best_id, best_score = ranked[0]
            best_symbol = ASSET_REGISTRY[best_id].symbol

            if all_negative or best_score <= 0:
                sell()
            elif best_symbol != holding:
                current_score = -999
                if holding:
                    for aid, sc in scores.items():
                        if ASSET_REGISTRY[aid].symbol == holding:
                            current_score = sc
                            break
                if holding and best_score - current_score < 0.03:
                    snapshots.append(pv())
                    continue

                sell()
                price = get_price(best_symbol, calc_date)
                if price and cash > 0:
                    fee = cash * fee_pct
                    buy_amount = cash - fee
                    shares = buy_amount / price
                    cash = 0.0
                    holding = best_symbol
                    hwm = price

        snapshots.append(pv())

    final = pv()
    total_days = (end - start).days
    years = total_days / 365.25
    cagr = (final / capital) ** (1 / years) - 1 if years > 0 else 0
    pct_in_cash = days_in_cash / total_days * 100

    peak = snapshots[0] if snapshots else capital
    max_dd = 0
    for v in snapshots:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    returns = pd.Series(snapshots).pct_change().dropna()
    sharpe = 0
    if len(returns) > 0 and returns.std() > 0:
        sharpe = (returns.mean() * 365) / (returns.std() * np.sqrt(365))

    return final, cagr, max_dd, sharpe, days_in_cash, pct_in_cash, total_yield_earned


def main():
    capital = 10000.0

    print("Fetching data...")
    provider = BinanceDataProvider()
    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(), timeframe="1d",
        start_date=datetime(2020, 12, 1), end_date=datetime(2026, 3, 31),
    )
    print(f"Fetched {len(prices)} candles\n")

    periods = [
        ("Full (2021-2026)", date(2021, 1, 1), date(2026, 3, 31)),
        ("5y (Apr 2021)", date(2021, 4, 1), date(2026, 3, 31)),
        ("3y", date(2023, 4, 1), date(2026, 3, 31)),
        ("1y", date(2025, 4, 1), date(2026, 3, 31)),
    ]

    apy_rates = [0.0, 0.04, 0.06, 0.10]  # 0%, 4%, 6%, 10% APY

    for period_label, start, end in periods:
        print(f"\n{'='*85}")
        print(f"  {period_label}")
        print(f"{'='*85}")
        print(f"{'USDT APY':<12} {'Final':>12} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8} {'Days Cash':>10} {'% Cash':>8} {'Yield $':>10}")
        print("-" * 85)

        for apy in apy_rates:
            final, cagr, max_dd, sharpe, days_cash, pct_cash, yield_earned = run_backtest_with_yield(
                prices, start, end, capital, usdt_apy=apy,
            )
            label = f"{apy:.0%}" if apy > 0 else "None"
            print(
                f"{label:<12} ${final:>11,.0f} {cagr:>7.1%} {max_dd:>7.1%} "
                f"{sharpe:>7.2f} {days_cash:>9} {pct_cash:>7.0f}% ${yield_earned:>9,.0f}"
            )

    # Summary
    print(f"\n{'='*85}")
    print("SUMMARY: Impact of 6% APY on idle USDT")
    print(f"{'='*85}")
    print(f"{'Period':<25} {'Without':>12} {'With 6%':>12} {'Diff':>12} {'Diff %':>8}")
    print("-" * 70)
    for period_label, start, end in periods:
        f_no, c_no, _, _, _, _, _ = run_backtest_with_yield(prices, start, end, capital, usdt_apy=0.0)
        f_yes, c_yes, _, _, _, _, yield_e = run_backtest_with_yield(prices, start, end, capital, usdt_apy=0.06)
        diff = f_yes - f_no
        diff_pct = diff / f_no * 100 if f_no > 0 else 0
        print(f"{period_label:<25} ${f_no:>11,.0f} ${f_yes:>11,.0f} ${diff:>11,.0f} {diff_pct:>+7.1f}%")


if __name__ == "__main__":
    main()
