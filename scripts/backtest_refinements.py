"""Backtest: risk-adjusted momentum + Friday rebalance + RSI filter."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd
from aurel2_crypto.core.assets import get_all_symbols, ASSET_REGISTRY, MOMENTUM_ASSETS
from aurel2_crypto.core.models import CryptoAsset, SignalAction
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.data.momentum import calculate_momentum_scores, rank_by_momentum
from aurel2_crypto.data.indicators import calculate_rsi


def calc_risk_adjusted_momentum(prices, asset_id, calc_date, lookback=28):
    """Momentum / volatility = risk-adjusted momentum (Sharpe-of-returns)."""
    asset = ASSET_REGISTRY[asset_id]
    symbol = asset.symbol
    ap = prices[prices["symbol"] == symbol].copy()
    if ap.empty:
        return None
    ap["date"] = pd.to_datetime(ap["timestamp"]).dt.date
    ap = ap[ap["date"] <= calc_date].sort_values("timestamp")
    if len(ap) < lookback + 1:
        return None

    recent = ap.tail(lookback + 1)
    returns = recent["close"].pct_change().dropna()
    if len(returns) < lookback or returns.std() == 0:
        return None

    momentum = (float(recent.iloc[-1]["close"]) - float(recent.iloc[0]["close"])) / float(recent.iloc[0]["close"])
    vol = float(returns.std())
    return momentum / vol


def run_backtest(
    prices, start, end, capital=10000.0, stop_pct=0.10,
    lookback=28, fee_pct=0.001,
    rebalance_weekday=0,  # 0=Monday, 4=Friday
    risk_adjusted=False,
    rsi_filter=False, rsi_threshold=80,
):
    """Flexible backtest with all enhancements."""
    all_days = []
    d = start
    while d <= end:
        all_days.append(d)
        d += timedelta(days=1)

    rebalance_set = set()
    cur = start
    while cur.weekday() != rebalance_weekday:
        cur += timedelta(days=1)
    while cur <= end:
        rebalance_set.add(cur)
        cur += timedelta(days=7)

    cash = capital
    holding = None
    shares = 0.0
    hwm = 0.0
    snapshots = []

    def get_price(symbol, calc_date):
        ap = prices[prices["symbol"] == symbol].copy()
        if ap.empty:
            return None
        ap["date"] = pd.to_datetime(ap["timestamp"]).dt.date
        av = ap[ap["date"] <= calc_date].sort_values("timestamp")
        return float(av.iloc[-1]["close"]) if not av.empty else None

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

    def pv():
        total = cash
        if holding and holding != CryptoAsset.USDT:
            asset = ASSET_REGISTRY[holding]
            price = get_price(asset.symbol, calc_date)
            if price:
                total += shares * price
        return total

    for calc_date in all_days:
        is_rebalance = calc_date in rebalance_set

        # Trailing stop
        if stop_pct > 0 and holding and holding != CryptoAsset.USDT:
            asset = ASSET_REGISTRY[holding]
            price = get_price(asset.symbol, calc_date)
            if price:
                hwm = max(hwm, price)
                if hwm > 0 and (hwm - price) / hwm >= stop_pct:
                    sell()

        if is_rebalance:
            if risk_adjusted:
                # Risk-adjusted momentum ranking
                ra_scores = {}
                for asset_id in MOMENTUM_ASSETS:
                    score = calc_risk_adjusted_momentum(prices, asset_id, calc_date, lookback)
                    if score is not None:
                        ra_scores[asset_id] = score
                if not ra_scores:
                    snapshots.append(pv())
                    continue
                ranked = sorted(ra_scores.items(), key=lambda x: x[1], reverse=True)
                all_negative = all(s <= 0 for _, s in ranked)
                best_id = ranked[0][0]
                best_score = ranked[0][1]
            else:
                scores = calculate_momentum_scores(prices, MOMENTUM_ASSETS, calc_date, lookback)
                if not scores:
                    snapshots.append(pv())
                    continue
                ranked = rank_by_momentum(scores)
                all_negative = all(s.momentum <= 0 for _, s in ranked)
                best_id = ranked[0][0]
                best_score = ranked[0][1].momentum if hasattr(ranked[0][1], 'momentum') else ranked[0][1]

            if all_negative:
                sell()
            elif best_score <= 0:
                sell()
            elif best_id != holding:
                # RSI filter: skip buy if RSI > threshold (overbought)
                if rsi_filter:
                    asset = ASSET_REGISTRY[best_id]
                    rsi = calculate_rsi(prices, asset.symbol, period=7, calc_date=calc_date)
                    if rsi and rsi > rsi_threshold:
                        # Skip this entry — too overbought
                        snapshots.append(pv())
                        continue

                sell()
                asset = ASSET_REGISTRY[best_id]
                price = get_price(asset.symbol, calc_date)
                if price and cash > 0:
                    fee = cash * fee_pct
                    buy_amount = cash - fee
                    shares = buy_amount / price
                    cash = 0.0
                    holding = best_id
                    hwm = price

        snapshots.append(pv())

    final = pv()
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
        ("Baseline (Mon, raw mom)", dict()),
        # Friday rebalance
        ("Friday rebalance", dict(rebalance_weekday=4)),
        ("Wednesday rebalance", dict(rebalance_weekday=2)),
        ("Sunday rebalance", dict(rebalance_weekday=6)),
        # Risk-adjusted momentum
        ("Risk-adj momentum (Mon)", dict(risk_adjusted=True)),
        ("Risk-adj momentum (Fri)", dict(risk_adjusted=True, rebalance_weekday=4)),
        # RSI filter
        ("RSI filter (Mon)", dict(rsi_filter=True)),
        ("RSI filter (Fri)", dict(rsi_filter=True, rebalance_weekday=4)),
        # Combined best
        ("Risk-adj + Fri", dict(risk_adjusted=True, rebalance_weekday=4)),
        ("Risk-adj + Fri + RSI", dict(risk_adjusted=True, rebalance_weekday=4, rsi_filter=True)),
        ("Risk-adj + RSI (Mon)", dict(risk_adjusted=True, rsi_filter=True)),
    ]

    print(f"{'Config':<30} {'Final':>12} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8}")
    print("-" * 70)

    for label, kwargs in configs:
        final, cagr, max_dd, sharpe = run_backtest(
            prices, start, end, capital, stop_pct=0.10, **kwargs,
        )
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
