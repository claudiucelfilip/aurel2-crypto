"""Backtest stop-loss mode x level x daily-exit deadband on HOURLY data.

Mirrors the live daemon: Monday ~00:00 UTC rebalance on momentum through
Sunday's close; Tue-Sun 01:00 daily all-negative exit; trailing stop tracked
hour-by-hour ('intraday') or only at the daily close ('close'), or disabled.
Hourly HWM/low tracking closely approximates the daemon's 5-min polling
without daily-bar ordering ambiguity.
"""

import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

SYMBOLS = {
    "btc": "BTC/USDT", "eth": "ETH/USDT", "sol": "SOL/USDT",
    "avax": "AVAX/USDT", "link": "LINK/USDT",
}
FEE = 0.001
USDT_APY = 0.06
LOOKBACK = 28
SWITCH_THRESHOLD = 0.03
CACHE = Path("/tmp/hourly_cache")


def fetch_hourly(ex, symbol, since_ms, until_ms):
    f = CACHE / f"{symbol.replace('/', '_')}.pkl"
    if f.exists():
        return pd.read_pickle(f)
    rows = []
    cursor = since_ms
    while cursor < until_ms:
        batch = ex.fetch_ohlcv(symbol, "1h", since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + 3600_000
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    CACHE.mkdir(exist_ok=True)
    df.to_pickle(f)
    return df


def build_data():
    ex = ccxt.binance({"enableRateLimit": True})
    since = int(datetime(2020, 11, 1).timestamp() * 1000)
    until = int(time.time() * 1000)
    hourly, daily_close = {}, {}
    for aid, sym in SYMBOLS.items():
        df = fetch_hourly(ex, sym, since, until)
        df["dt"] = pd.to_datetime(df["ts"], unit="ms")
        hourly[aid] = df
        # daily close = last hourly close of each UTC day
        dc = df.set_index("dt")["close"].resample("1D").last().dropna()
        daily_close[aid] = {d.date(): float(v) for d, v in dc.items()}
        print(f"{sym}: {len(df)} hourly candles", file=sys.stderr)
    return hourly, daily_close


def momentum_asof(daily_close, aid, day):
    """28d close-to-close momentum using closes up to `day` (inclusive)."""
    s = daily_close[aid]
    cur = s.get(day) or s.get(day - timedelta(days=1))
    past_day = day - timedelta(days=LOOKBACK)
    past = s.get(past_day) or s.get(past_day - timedelta(days=1))
    if cur is None or past is None or past <= 0:
        return None
    return (cur - past) / past


def run(merged, daily_close, start, end, stop_mode, stop_pct, band, capital=10000.0, log=None):
    cash, shares, holding, hwm = capital, 0.0, None, 0.0
    hourly_yield = (1 + USDT_APY) ** (1 / (365 * 24)) - 1
    day_equity = {}
    trades = 0
    last_rebalance_day = None
    last_filter_day = None

    for row in merged.itertuples():
        dt = row.dt
        d = dt.date()
        if d < start or d > end:
            continue
        hour = dt.hour

        bars = row  # per-asset columns: <aid>_open/high/low/close

        # trailing stop, evaluated per hour (intraday) or at 23:00 close (close mode)
        if holding and stop_mode != "none":
            hi = getattr(bars, f"{holding}_high")
            lo = getattr(bars, f"{holding}_low")
            cl = getattr(bars, f"{holding}_close")
            if not np.isnan(hi):
                if stop_mode == "intraday":
                    hwm = max(hwm, hi)
                    level = hwm * (1 - stop_pct)
                    if lo <= level:
                        cash = shares * level * (1 - FEE)
                        shares, holding, hwm = 0.0, None, 0.0
                        trades += 1
                        if log is not None:
                            log.append((str(dt), "stop", round(level, 4)))
                elif stop_mode == "close" and hour == 23:
                    hwm = max(hwm, cl)
                    if cl <= hwm * (1 - stop_pct):
                        cash = shares * cl * (1 - FEE)
                        shares, holding, hwm = 0.0, None, 0.0
                        trades += 1
                        if log is not None:
                            log.append((str(dt), "stop", round(cl, 4)))

        # Monday 00:00 rebalance (momentum through Sunday close)
        if hour == 0 and d.weekday() == 0 and last_rebalance_day != d:
            last_rebalance_day = d
            sig_day = d - timedelta(days=1)
            scores = {a: momentum_asof(daily_close, a, sig_day) for a in SYMBOLS}
            scores = {a: m for a, m in scores.items() if m is not None}
            if scores:
                best = max(scores, key=scores.get)
                all_neg = all(m <= 0 for m in scores.values())
                target = None if all_neg else best
                if holding and target and holding in scores:
                    cur_m = scores[holding]
                    if cur_m >= 0 and (scores[best] - cur_m) <= SWITCH_THRESHOLD:
                        target = holding
                if target != holding:
                    if holding:
                        px = getattr(bars, f"{holding}_open")
                        if not np.isnan(px):
                            cash = shares * px * (1 - FEE)
                            shares, holding = 0.0, None
                            trades += 1
                            if log is not None:
                                log.append((str(dt), "rebal_sell", round(px, 4)))
                    if target:
                        px = getattr(bars, f"{target}_open")
                        if not np.isnan(px):
                            shares = cash * (1 - FEE) / px
                            cash, holding, hwm = 0.0, target, px
                            trades += 1
                            if log is not None:
                                log.append((str(dt), f"buy_{target}", round(px, 4)))

        # Tue-Sun 01:00 daily exit filter
        if hour == 1 and d.weekday() != 0 and holding and last_filter_day != d:
            last_filter_day = d
            sig_day = d - timedelta(days=1)
            scores = {a: momentum_asof(daily_close, a, sig_day) for a in SYMBOLS}
            scores = {a: m for a, m in scores.items() if m is not None}
            if scores and all(m <= -band for m in scores.values()):
                px = getattr(bars, f"{holding}_open")
                if not np.isnan(px):
                    cash = shares * px * (1 - FEE)
                    shares, holding, hwm = 0.0, None, 0.0
                    trades += 1
                    if log is not None:
                        log.append((str(dt), "daily_exit", round(px, 4)))

        if not holding and cash > 0:
            cash *= 1 + hourly_yield

        if holding:
            cl = getattr(bars, f"{holding}_close")
            if not np.isnan(cl):
                day_equity[d] = cash + shares * cl
        else:
            day_equity[d] = cash

    eq = pd.Series(day_equity).sort_index().ffill()
    rets = eq.pct_change().dropna()
    final = float(eq.iloc[-1])
    years = (end - start).days / 365.25
    cagr = (final / capital) ** (1 / years) - 1 if years > 0 else 0
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    sharpe = float(rets.mean() / rets.std() * np.sqrt(365)) if rets.std() > 0 else 0
    return final, cagr, dd, sharpe, trades


def main():
    hourly, daily_close = build_data()
    frames = []
    for aid, df in hourly.items():
        f = df.set_index("dt")[["open", "high", "low", "close"]].add_prefix(f"{aid}_")
        frames.append(f)
    merged = pd.concat(frames, axis=1).sort_index().reset_index()

    windows = {
        "full 2021-2026.08": (date(2021, 1, 4), date(2026, 8, 24)),
        "2024-2026.08": (date(2024, 1, 1), date(2026, 8, 24)),
        "last 12mo": (date(2025, 8, 25), date(2026, 8, 24)),
        "live window": (date(2026, 4, 2), date(2026, 8, 24)),
    }

    # Fidelity check: current live config over live window, with trade log
    log = []
    res = run(merged, daily_close, *windows["live window"], "intraday", 0.10, 0.0, log=log)
    print("== fidelity check: intraday 10% band 0, live window ==")
    print("final %.0f  trades %d" % (res[0], res[4]))
    for entry in log:
        print("  ", entry)

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
            final, cagr, dd, sharpe, trades = run(merged, daily_close, start, end, mode, stop, band)
            print(f"{mode:<9} {stop:>5.0%} {band:>5.0%} {final:>10,.0f} {cagr:>8.1%} {dd:>7.1%} {sharpe:>7.2f} {trades:>7}")


if __name__ == "__main__":
    main()
