"""Backtest: expanded coin pool (10 coins vs current 5)."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd
from aurel2_crypto.core.models import CryptoAsset
from aurel2_crypto.data.providers.binance import BinanceDataProvider


# Extended pools to test
POOL_5 = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT"]
POOL_8 = POOL_5 + ["DOT/USDT", "ADA/USDT", "XRP/USDT"]
POOL_10 = POOL_8 + ["DOGE/USDT", "UNI/USDT"]
POOL_12 = POOL_10 + ["MATIC/USDT", "BNB/USDT"]


def run_pool_backtest(
    prices, start, end, pool, capital=10000.0, stop_pct=0.10,
    lookback=28, fee_pct=0.001,
):
    """Backtest momentum on arbitrary coin pool."""
    all_days = []
    d = start
    while d <= end:
        all_days.append(d)
        d += timedelta(days=1)

    # Monday rebalance
    rebalance_set = set()
    cur = start
    while cur.weekday() != 0:
        cur += timedelta(days=1)
    while cur <= end:
        rebalance_set.add(cur)
        cur += timedelta(days=7)

    cash = capital
    holding = None  # symbol string
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
        current_price = float(ap.iloc[-1]["close"])
        past_price = float(past.iloc[-1]["close"])
        if past_price <= 0:
            return None
        return (current_price - past_price) / past_price

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

        # Trailing stop
        if stop_pct > 0 and holding:
            price = get_price(holding, calc_date)
            if price:
                hwm = max(hwm, price)
                if hwm > 0 and (hwm - price) / hwm >= stop_pct:
                    sell()

        if is_rebalance:
            # Rank all coins by momentum
            scores = {}
            for symbol in pool:
                m = calc_momentum(symbol, calc_date)
                if m is not None:
                    scores[symbol] = m

            if not scores:
                snapshots.append(pv())
                continue

            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            all_negative = all(s <= 0 for _, s in ranked)
            best_sym, best_score = ranked[0]

            if all_negative or best_score <= 0:
                sell()
            elif best_sym != holding:
                # Switch threshold
                if holding and holding in scores:
                    advantage = best_score - scores[holding]
                    if advantage < 0.03:
                        snapshots.append(pv())
                        continue

                sell()
                price = get_price(best_sym, calc_date)
                if price and cash > 0:
                    fee = cash * fee_pct
                    buy_amount = cash - fee
                    shares = buy_amount / price
                    cash = 0.0
                    holding = best_sym
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

    # Count trades
    trades = sum(1 for i in range(1, len(snapshots))
                 if abs(snapshots[i] - snapshots[i-1]) / max(snapshots[i-1], 1) > 0.005)

    return final, cagr, max_dd, sharpe


def main():
    start = date(2021, 1, 1)
    end = date(2026, 3, 31)
    capital = 10000.0

    # Fetch all symbols we might need
    all_symbols = list(set(POOL_12))
    print(f"Fetching data for {len(all_symbols)} symbols...")
    provider = BinanceDataProvider()
    prices = provider.get_multi_ohlcv(
        symbols=all_symbols, timeframe="1d",
        start_date=datetime(2020, 12, 1), end_date=datetime(2026, 3, 31),
    )
    print(f"Fetched {len(prices)} candles, {prices['symbol'].nunique()} symbols\n")

    # Check data availability
    for sym in all_symbols:
        sym_data = prices[prices["symbol"] == sym]
        if sym_data.empty:
            print(f"  WARNING: no data for {sym}")
        else:
            first = sym_data["timestamp"].min()
            print(f"  {sym}: {len(sym_data)} candles from {first.date()}")

    print()

    pools = [
        ("5 coins (current)", POOL_5),
        ("8 coins (+DOT,ADA,XRP)", POOL_8),
        ("10 coins (+DOGE,UNI)", POOL_10),
        ("12 coins (+MATIC,BNB)", POOL_12),
    ]

    print(f"{'Pool':<30} {'Final':>12} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8}")
    print("-" * 70)

    for label, pool in pools:
        final, cagr, max_dd, sharpe = run_pool_backtest(
            prices, start, end, pool, capital,
        )
        print(f"{label:<30} ${final:>11,.0f} {cagr:>7.1%} {max_dd:>7.1%} {sharpe:>7.2f}")

    # BTC benchmark
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
