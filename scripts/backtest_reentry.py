"""Backtest: faster re-entry after stop-loss to reduce idle time.

Current problem: stop-loss fires mid-week, then we sit in cash until Monday.
Options:
1. Daily re-entry check (if best coin has strong positive momentum, buy immediately)
2. Bi-weekly rebalance (Monday + Thursday)
3. Every-3-day rebalance
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd
from aurel2_crypto.core.assets import get_all_symbols, ASSET_REGISTRY, MOMENTUM_ASSETS
from aurel2_crypto.core.models import CryptoAsset
from aurel2_crypto.data.momentum import calculate_momentum_scores, rank_by_momentum


USDT_APY = 0.06


def run_backtest(
    prices, start, end, capital=10000.0, stop_pct=0.10,
    lookback=28, fee_pct=0.001,
    rebalance_days=7,
    daily_reentry=False, reentry_threshold=0.05,
):
    all_days = []
    d = start
    while d <= end:
        all_days.append(d)
        d += timedelta(days=1)

    # Build rebalance set
    rebalance_set = set()
    cur = start
    while cur.weekday() != 0:
        cur += timedelta(days=1)
    while cur <= end:
        rebalance_set.add(cur)
        cur += timedelta(days=rebalance_days)

    cash = capital
    holding = None  # CryptoAsset
    shares = 0.0
    hwm = 0.0
    snapshots = []
    days_in_cash = 0
    yield_acc = 0.0
    daily_yield = (1 + USDT_APY) ** (1 / 365) - 1
    trade_count = 0

    def get_price(symbol, calc_date):
        ap = prices[prices["symbol"] == symbol].copy()
        if ap.empty:
            return None
        ap["date"] = pd.to_datetime(ap["timestamp"]).dt.date
        av = ap[ap["date"] <= calc_date].sort_values("timestamp")
        return float(av.iloc[-1]["close"]) if not av.empty else None

    def sell():
        nonlocal cash, holding, shares, hwm, trade_count
        if not holding:
            return
        asset = ASSET_REGISTRY[holding]
        price = get_price(asset.symbol, calc_date)
        if price and shares > 0:
            proceeds = shares * price
            fee = proceeds * fee_pct
            cash += proceeds - fee
        holding = None
        shares = 0.0
        hwm = 0.0
        trade_count += 1

    def buy_best(scores):
        nonlocal cash, holding, shares, hwm, trade_count
        ranked = rank_by_momentum(scores)
        best_id, best_score = ranked[0]
        if best_score.momentum <= 0:
            return
        asset = ASSET_REGISTRY[best_id]
        price = get_price(asset.symbol, calc_date)
        if price and cash > 0:
            fee = cash * fee_pct
            buy_amount = cash - fee
            shares = buy_amount / price
            cash = 0.0
            holding = best_id
            hwm = price
            trade_count += 1

    def pv():
        total = cash + yield_acc
        if holding:
            asset = ASSET_REGISTRY[holding]
            price = get_price(asset.symbol, calc_date)
            if price:
                total += shares * price
        return total

    for calc_date in all_days:
        is_rebalance = calc_date in rebalance_set

        # Yield on idle cash
        if not holding and cash > 0:
            yield_acc += cash * daily_yield
            days_in_cash += 1

        # Trailing stop
        if stop_pct > 0 and holding:
            asset = ASSET_REGISTRY[holding]
            price = get_price(asset.symbol, calc_date)
            if price:
                hwm = max(hwm, price)
                if hwm > 0 and (hwm - price) / hwm >= stop_pct:
                    sell()

        # Daily re-entry: if stopped out and not a rebalance day, check for strong momentum
        if daily_reentry and not holding and not is_rebalance:
            scores = calculate_momentum_scores(prices, MOMENTUM_ASSETS, calc_date, lookback)
            if scores:
                ranked = rank_by_momentum(scores)
                best_id, best_score = ranked[0]
                # Only re-enter if best coin has strong positive momentum
                if best_score.momentum >= reentry_threshold:
                    buy_best(scores)

        # Regular rebalance
        if is_rebalance:
            scores = calculate_momentum_scores(prices, MOMENTUM_ASSETS, calc_date, lookback)
            if scores:
                ranked = rank_by_momentum(scores)
                all_negative = all(s.momentum <= 0 for s in scores.values())
                best_id, best_score = ranked[0]

                if all_negative:
                    sell()
                elif not holding:
                    buy_best(scores)
                elif best_id != holding:
                    current_score = scores.get(holding)
                    if current_score and current_score.momentum < 0:
                        sell()
                        buy_best(scores)
                    elif current_score and best_score.momentum - current_score.momentum > 0.03:
                        sell()
                        buy_best(scores)

        snapshots.append(pv())

    final = pv()
    total_days = (end - start).days
    years = total_days / 365.25
    cagr = (final / capital) ** (1 / years) - 1 if years > 0 else 0
    pct_cash = days_in_cash / total_days * 100

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

    return final, cagr, max_dd, sharpe, pct_cash, trade_count


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
    print(f"Fetched {len(prices)} candles\n")

    configs = [
        ("Weekly (current)", dict(rebalance_days=7)),
        ("Bi-weekly (Mon+Thu)", dict(rebalance_days=3)),  # ~every 3-4 days
        ("Every 3 days", dict(rebalance_days=3)),
        ("Daily re-entry (5%)", dict(daily_reentry=True, reentry_threshold=0.05)),
        ("Daily re-entry (10%)", dict(daily_reentry=True, reentry_threshold=0.10)),
        ("Daily re-entry (3%)", dict(daily_reentry=True, reentry_threshold=0.03)),
        ("3-day + re-entry 5%", dict(rebalance_days=3, daily_reentry=True, reentry_threshold=0.05)),
    ]

    print(f"{'Config':<30} {'Final':>12} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8} {'Cash%':>7} {'Trades':>8}")
    print("-" * 85)

    for label, kwargs in configs:
        final, cagr, max_dd, sharpe, pct_cash, trades = run_backtest(
            prices, start, end, capital, **kwargs,
        )
        print(
            f"{label:<30} ${final:>11,.0f} {cagr:>7.1%} {max_dd:>7.1%} "
            f"{sharpe:>7.2f} {pct_cash:>6.0f}% {trades:>7}"
        )


if __name__ == "__main__":
    from aurel2_crypto.data.providers.binance import BinanceDataProvider
    main()
