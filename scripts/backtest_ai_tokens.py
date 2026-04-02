"""Backtest: AI token momentum pool vs current pool vs combined."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd
from aurel2_crypto.data.providers.binance import BinanceDataProvider


POOL_MAIN = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT"]
POOL_AI = ["FET/USDT", "RENDER/USDT", "TAO/USDT", "NEAR/USDT", "INJ/USDT"]
POOL_COMBINED = POOL_MAIN + POOL_AI


def run_pool_backtest(
    prices, start, end, pool, capital=10000.0, stop_pct=0.10,
    lookback=28, fee_pct=0.001,
):
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
    trade_count = 0

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

        if stop_pct > 0 and holding:
            price = get_price(holding, calc_date)
            if price:
                hwm = max(hwm, price)
                if hwm > 0 and (hwm - price) / hwm >= stop_pct:
                    sell()
                    trade_count += 1

        if is_rebalance:
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
                if holding and holding in scores:
                    if best_score - scores[holding] < 0.03:
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
                    trade_count += 1

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

    return final, cagr, max_dd, sharpe, trade_count


def main():
    start = date(2021, 1, 1)
    end = date(2026, 3, 31)
    capital = 10000.0

    all_symbols = list(set(POOL_COMBINED))
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

    # Find common start date (some AI tokens are newer)
    earliest_dates = {}
    for sym in all_symbols:
        sym_data = prices[prices["symbol"] == sym]
        if not sym_data.empty:
            earliest_dates[sym] = sym_data["timestamp"].min().date()

    # Test from 2021 (all main coins available, some AI tokens may be missing)
    # Also test from latest AI token start
    ai_start = max(earliest_dates.get(s, start) for s in POOL_AI if s in earliest_dates)
    print(f"AI tokens available from: {ai_start}\n")

    # Full period (2021-2026) — AI tokens that don't exist yet get skipped
    pools = [
        ("Main 5 (current)", POOL_MAIN),
        ("AI 5 tokens", POOL_AI),
        ("Combined 10", POOL_COMBINED),
    ]

    for period_label, period_start in [("Full (2021-2026)", start), (f"Since AI available ({ai_start})", ai_start)]:
        print(f"=== {period_label} ===")
        print(f"{'Pool':<25} {'Final':>12} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8} {'Trades':>8}")
        print("-" * 75)

        for label, pool in pools:
            final, cagr, max_dd, sharpe, trades = run_pool_backtest(
                prices, period_start, end, pool, capital,
            )
            print(f"{label:<25} ${final:>11,.0f} {cagr:>7.1%} {max_dd:>7.1%} {sharpe:>7.2f} {trades:>7}")

        # BTC benchmark
        btc = prices[prices["symbol"] == "BTC/USDT"].copy()
        btc["date"] = pd.to_datetime(btc["timestamp"]).dt.date
        sp = float(btc[btc["date"] <= period_start].sort_values("timestamp").iloc[-1]["close"])
        ep = float(btc[btc["date"] <= end].sort_values("timestamp").iloc[-1]["close"])
        btc_final = (capital * 0.999 / sp) * ep
        years = (end - period_start).days / 365.25
        btc_cagr = (btc_final / capital) ** (1 / years) - 1
        print(f"{'BTC B&H':<25} ${btc_final:>11,.0f} {btc_cagr:>7.1%}")
        print()

    # Also test: split allocation (70% main pool, 30% AI pool)
    print("=== Split Allocation (since AI available) ===")
    main_final, main_cagr, main_dd, _, _ = run_pool_backtest(prices, ai_start, end, POOL_MAIN, capital * 0.70)
    ai_final, ai_cagr, ai_dd, _, _ = run_pool_backtest(prices, ai_start, end, POOL_AI, capital * 0.30)
    combined_final = main_final + ai_final
    combined_cagr = (combined_final / capital) ** (1 / ((end - ai_start).days / 365.25)) - 1
    print(f"70% Main + 30% AI: ${combined_final:>11,.0f}  CAGR: {combined_cagr:.1%}")
    print(f"  Main portion: ${main_final:,.0f}  AI portion: ${ai_final:,.0f}")


if __name__ == "__main__":
    main()
