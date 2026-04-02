"""Backtest: diversified momentum (top N coins) vs concentrated (top 1)."""

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd
from aurel2_crypto.core.assets import get_all_symbols, ASSET_REGISTRY, MOMENTUM_ASSETS
from aurel2_crypto.core.models import CryptoAsset, SignalAction, Trade, PortfolioSnapshot, Position
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.data.momentum import calculate_momentum_scores, rank_by_momentum


def run_diversified_backtest(
    prices, start, end, capital=10000.0, top_n=1, stop_pct=0.10,
    lookback=28, rebalance_days=7, fee_pct=0.001,
):
    """Backtest momentum spreading across top N coins."""
    # Generate weekly rebalance dates (Mondays)
    rebalance_dates = []
    current = start
    while current.weekday() != 0:
        current += timedelta(days=1)
    while current <= end:
        rebalance_dates.append(current)
        current += timedelta(days=rebalance_days)
    rebalance_set = set(rebalance_dates)

    # Build all days for stop-loss
    all_days = []
    d = start
    while d <= end:
        all_days.append(d)
        d += timedelta(days=1)

    cash = capital
    # Holdings: {CryptoAsset: (shares, high_water_mark)}
    holdings: dict[CryptoAsset, tuple[float, float]] = {}
    trades = []
    snapshots = []

    def get_price(symbol, calc_date):
        ap = prices[prices["symbol"] == symbol].copy()
        if ap.empty:
            return None
        ap["date"] = pd.to_datetime(ap["timestamp"]).dt.date
        av = ap[ap["date"] <= calc_date].sort_values("timestamp")
        return float(av.iloc[-1]["close"]) if not av.empty else None

    def sell_all():
        nonlocal cash
        for asset_id, (shares, _) in list(holdings.items()):
            asset = ASSET_REGISTRY[asset_id]
            price = get_price(asset.symbol, calc_date)
            if price and shares > 0:
                proceeds = shares * price
                fee = proceeds * fee_pct
                cash += proceeds - fee
        holdings.clear()

    def portfolio_value(calc_date):
        total = cash
        for asset_id, (shares, _) in holdings.items():
            asset = ASSET_REGISTRY[asset_id]
            price = get_price(asset.symbol, calc_date)
            if price:
                total += shares * price
        return total

    for calc_date in all_days:
        is_rebalance = calc_date in rebalance_set

        # Daily stop-loss check per position
        if stop_pct > 0:
            stopped = []
            for asset_id, (shares, hwm) in list(holdings.items()):
                asset = ASSET_REGISTRY[asset_id]
                price = get_price(asset.symbol, calc_date)
                if not price:
                    continue
                new_hwm = max(hwm, price)
                holdings[asset_id] = (shares, new_hwm)
                if new_hwm > 0:
                    dd = (new_hwm - price) / new_hwm
                    if dd >= stop_pct:
                        proceeds = shares * price
                        fee = proceeds * fee_pct
                        cash += proceeds - fee
                        stopped.append(asset_id)
            for a in stopped:
                del holdings[a]

        # Weekly rebalance
        if is_rebalance:
            scores = calculate_momentum_scores(prices, MOMENTUM_ASSETS, calc_date, lookback)
            if not scores:
                snapshots.append(portfolio_value(calc_date))
                continue

            ranked = rank_by_momentum(scores)
            all_negative = all(s.momentum <= 0 for _, s in ranked)

            if all_negative:
                sell_all()
            else:
                # Target: top N with positive momentum
                targets = [(a, s) for a, s in ranked[:top_n] if s.momentum > 0]
                if not targets:
                    sell_all()
                else:
                    # Sell holdings not in targets
                    target_ids = {a for a, _ in targets}
                    for asset_id in list(holdings.keys()):
                        if asset_id not in target_ids:
                            shares, _ = holdings[asset_id]
                            asset = ASSET_REGISTRY[asset_id]
                            price = get_price(asset.symbol, calc_date)
                            if price and shares > 0:
                                proceeds = shares * price
                                fee = proceeds * fee_pct
                                cash += proceeds - fee
                            del holdings[asset_id]

                    # Allocate equally across targets
                    total_val = portfolio_value(calc_date)
                    per_target = total_val / len(targets)

                    for asset_id, score in targets:
                        asset = ASSET_REGISTRY[asset_id]
                        price = get_price(asset.symbol, calc_date)
                        if not price:
                            continue

                        current_val = 0
                        if asset_id in holdings:
                            current_val = holdings[asset_id][0] * price

                        # Rebalance if allocation is off by >10%
                        if abs(current_val - per_target) / max(per_target, 1) > 0.10:
                            # Sell existing
                            if asset_id in holdings:
                                old_shares = holdings[asset_id][0]
                                proceeds = old_shares * price
                                fee = proceeds * fee_pct
                                cash += proceeds - fee
                                del holdings[asset_id]

                            # Buy target allocation
                            buy_amount = min(per_target, cash) * (1 - fee_pct)
                            if buy_amount > 0 and price > 0:
                                shares = buy_amount / price
                                cash -= buy_amount / (1 - fee_pct)
                                holdings[asset_id] = (shares, price)

        snapshots.append(portfolio_value(calc_date))

    final = portfolio_value(end)

    # Metrics
    years = (end - start).days / 365.25
    cagr = (final / capital) ** (1 / years) - 1 if years > 0 else 0

    peak = snapshots[0]
    max_dd = 0
    for v in snapshots:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    returns = pd.Series(snapshots).pct_change().dropna()
    avg_days = (end - start).days / max(len(snapshots) - 1, 1)
    ppy = 365 / max(avg_days, 1)
    sharpe = (returns.mean() * ppy) / (returns.std() * np.sqrt(ppy)) if returns.std() > 0 else 0

    return final, cagr, max_dd, sharpe


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
        ("Top 1 (current)", 1, 0.10),
        ("Top 2", 2, 0.10),
        ("Top 3", 3, 0.10),
        ("Top 2 + 15% stop", 2, 0.15),
        ("Top 3 + 15% stop", 3, 0.15),
    ]

    print(f"{'Config':<25} {'Final':>12} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8}")
    print("-" * 65)

    for label, top_n, stop in configs:
        final, cagr, max_dd, sharpe = run_diversified_backtest(
            prices, start, end, capital, top_n=top_n, stop_pct=stop,
        )
        print(f"{label:<25} ${final:>11,.0f} {cagr:>7.1%} {max_dd:>7.1%} {sharpe:>7.2f}")

    # BTC benchmark
    btc = prices[prices["symbol"] == "BTC/USDT"].copy()
    btc["date"] = pd.to_datetime(btc["timestamp"]).dt.date
    sp = float(btc[btc["date"] <= start].sort_values("timestamp").iloc[-1]["close"])
    ep = float(btc[btc["date"] <= end].sort_values("timestamp").iloc[-1]["close"])
    btc_final = (capital * 0.999 / sp) * ep
    years = (end - start).days / 365.25
    btc_cagr = (btc_final / capital) ** (1 / years) - 1
    print(f"\n{'BTC B&H':<25} ${btc_final:>11,.0f} {btc_cagr:>7.1%}")


if __name__ == "__main__":
    main()
