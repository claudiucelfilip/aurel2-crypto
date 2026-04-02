"""Backtest remaining improvements: re-entry cooldown + volatility scaling."""

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd
from aurel2_crypto.core.assets import get_all_symbols, ASSET_REGISTRY, MOMENTUM_ASSETS
from aurel2_crypto.core.models import CryptoAsset, SignalAction
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.data.momentum import calculate_momentum_scores, rank_by_momentum


def run_backtest(
    prices, start, end, capital=10000.0, stop_pct=0.10,
    lookback=28, rebalance_days=7, fee_pct=0.001,
    cooldown_days=0, vol_scaling=False, vol_window=14,
):
    """Backtest with optional cooldown and volatility scaling."""
    # Build all days
    all_days = []
    d = start
    while d <= end:
        all_days.append(d)
        d += timedelta(days=1)

    # Rebalance dates (Mondays)
    rebalance_set = set()
    cur = start
    while cur.weekday() != 0:
        cur += timedelta(days=1)
    while cur <= end:
        rebalance_set.add(cur)
        cur += timedelta(days=rebalance_days)

    cash = capital
    holding = None  # CryptoAsset or None
    shares = 0.0
    hwm = 0.0
    snapshots = []
    last_stop_date = None  # For cooldown

    def get_price(symbol, calc_date):
        ap = prices[prices["symbol"] == symbol].copy()
        if ap.empty:
            return None
        ap["date"] = pd.to_datetime(ap["timestamp"]).dt.date
        av = ap[ap["date"] <= calc_date].sort_values("timestamp")
        return float(av.iloc[-1]["close"]) if not av.empty else None

    def get_volatility(symbol, calc_date, window=14):
        """Get annualized volatility (std of daily returns)."""
        ap = prices[prices["symbol"] == symbol].copy()
        if ap.empty:
            return None
        ap["date"] = pd.to_datetime(ap["timestamp"]).dt.date
        ap = ap[ap["date"] <= calc_date].sort_values("timestamp").tail(window + 1)
        if len(ap) < window:
            return None
        returns = ap["close"].pct_change().dropna()
        return float(returns.std() * np.sqrt(365))

    def sell():
        nonlocal cash, holding, shares, hwm
        if not holding or holding == CryptoAsset.USDT:
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

    def portfolio_value():
        total = cash
        if holding and holding != CryptoAsset.USDT:
            asset = ASSET_REGISTRY[holding]
            price = get_price(asset.symbol, calc_date)
            if price:
                total += shares * price
        return total

    for calc_date in all_days:
        is_rebalance = calc_date in rebalance_set

        # Trailing stop check
        if stop_pct > 0 and holding and holding != CryptoAsset.USDT:
            asset = ASSET_REGISTRY[holding]
            price = get_price(asset.symbol, calc_date)
            if price:
                hwm = max(hwm, price)
                if hwm > 0:
                    dd = (hwm - price) / hwm
                    if dd >= stop_pct:
                        sell()
                        last_stop_date = calc_date

        # Weekly rebalance
        if is_rebalance:
            # Cooldown: skip re-entry if recently stopped out
            if cooldown_days > 0 and last_stop_date:
                days_since_stop = (calc_date - last_stop_date).days
                if days_since_stop < cooldown_days:
                    snapshots.append(portfolio_value())
                    continue

            scores = calculate_momentum_scores(prices, MOMENTUM_ASSETS, calc_date, lookback)
            if not scores:
                snapshots.append(portfolio_value())
                continue

            ranked = rank_by_momentum(scores)
            all_negative = all(s.momentum <= 0 for _, s in ranked)

            if all_negative:
                sell()
            else:
                best_id, best_score = ranked[0]
                if best_score.momentum <= 0:
                    sell()
                elif best_id != holding:
                    # Switch
                    sell()
                    asset = ASSET_REGISTRY[best_id]
                    price = get_price(asset.symbol, calc_date)
                    if price and cash > 0:
                        # Volatility scaling
                        deploy_pct = 1.0
                        if vol_scaling:
                            vol = get_volatility(asset.symbol, calc_date, vol_window)
                            if vol:
                                # Target 50% annualized vol → scale down when vol is high
                                target_vol = 0.50
                                deploy_pct = min(target_vol / vol, 1.0)
                                deploy_pct = max(deploy_pct, 0.30)  # Floor at 30%

                        deploy = cash * deploy_pct
                        fee = deploy * fee_pct
                        buy_amount = deploy - fee
                        shares = buy_amount / price
                        cash -= deploy
                        holding = best_id
                        hwm = price

        snapshots.append(portfolio_value())

    final = portfolio_value()
    years = (end - start).days / 365.25
    cagr = (final / capital) ** (1 / years) - 1 if years > 0 else 0

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
        ("Baseline (current)", dict(stop_pct=0.10)),
        # Cooldown variations
        ("+ 3d cooldown", dict(stop_pct=0.10, cooldown_days=3)),
        ("+ 7d cooldown", dict(stop_pct=0.10, cooldown_days=7)),
        ("+ 14d cooldown", dict(stop_pct=0.10, cooldown_days=14)),
        # Vol scaling
        ("+ vol scaling", dict(stop_pct=0.10, vol_scaling=True)),
        # Combined
        ("+ 7d cooldown + vol", dict(stop_pct=0.10, cooldown_days=7, vol_scaling=True)),
        ("+ 3d cooldown + vol", dict(stop_pct=0.10, cooldown_days=3, vol_scaling=True)),
    ]

    print(f"{'Config':<30} {'Final':>12} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8}")
    print("-" * 70)

    for label, kwargs in configs:
        final, cagr, max_dd, sharpe = run_backtest(prices, start, end, capital, **kwargs)
        print(f"{label:<30} ${final:>11,.0f} {cagr:>7.1%} {max_dd:>7.1%} {sharpe:>7.2f}")

    # BTC
    btc = prices[prices["symbol"] == "BTC/USDT"].copy()
    btc["date"] = pd.to_datetime(btc["timestamp"]).dt.date
    sp = float(btc[btc["date"] <= start].sort_values("timestamp").iloc[-1]["close"])
    ep = float(btc[btc["date"] <= end].sort_values("timestamp").iloc[-1]["close"])
    btc_final = (capital * 0.999 / sp) * ep
    years = (end - start).days / 365.25
    btc_cagr = (btc_final / capital) ** (1 / years) - 1
    print(f"\n{'BTC B&H':<30} ${btc_final:>11,.0f} {btc_cagr:>7.1%}")


if __name__ == "__main__":
    main()
