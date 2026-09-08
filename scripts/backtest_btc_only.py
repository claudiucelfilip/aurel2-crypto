"""BTC-only variants on the hourly engine: does the 10% daily-close stop help a single-asset holder?

Variants:
  bh            buy-and-hold, no stop
  bh+stop       hold BTC; 10% trailing stop on daily close; re-enter next Monday unconditionally
  system(btc)   the live rules on a BTC-only universe: Monday entry if 28d momentum > 0,
                daily all-negative exit, 10% daily-close stop
  mom-only      momentum filter (enter/exit on 28d momentum) without the stop
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backtest_stop_hourly as H

H.SYMBOLS = {"btc": "BTC/USDT"}
import numpy as np
import pandas as pd


def run(merged, daily_close, start, end, variant, stop_pct=0.10, capital=10000.0):
    cash, shares, holding, hwm = capital, 0.0, None, 0.0
    hourly_yield = (1 + H.USDT_APY) ** (1 / (365 * 24)) - 1
    day_equity, trades = {}, 0
    last_reb, last_filt = None, None
    use_stop = variant in ("bh+stop", "system")
    use_mom = variant in ("system", "mom-only")

    for row in merged.itertuples():
        d = row.dt.date()
        if d < start or d > end:
            continue
        hour = row.dt.hour
        cl = row.btc_close
        if np.isnan(cl):
            continue

        if holding and use_stop and hour == 23:
            hwm = max(hwm, cl)
            if cl <= hwm * (1 - stop_pct):
                cash = shares * cl * (1 - H.FEE)
                shares, holding, hwm = 0.0, None, 0.0
                trades += 1

        mom = H.momentum_asof(daily_close, "btc", d - timedelta(days=1))

        # Monday 00:00 entry / rebalance
        if hour == 0 and d.weekday() == 0 and last_reb != d:
            last_reb = d
            if variant == "bh" and not holding:
                want = True
            elif variant == "bh+stop":
                want = True
            else:
                want = mom is not None and mom > 0
            if want and not holding:
                px = row.btc_open
                shares = cash * (1 - H.FEE) / px
                cash, holding, hwm = 0.0, "btc", px
                trades += 1
            elif not want and holding and use_mom:
                px = row.btc_open
                cash = shares * px * (1 - H.FEE)
                shares, holding, hwm = 0.0, None, 0.0
                trades += 1

        # Tue-Sun 01:00 daily exit filter (momentum variants only)
        if use_mom and hour == 1 and d.weekday() != 0 and holding and last_filt != d:
            last_filt = d
            if mom is not None and mom <= 0:
                px = row.btc_open
                cash = shares * px * (1 - H.FEE)
                shares, holding, hwm = 0.0, None, 0.0
                trades += 1

        if not holding and cash > 0:
            cash *= 1 + hourly_yield
        day_equity[d] = cash + shares * cl if holding else cash

    eq = pd.Series(day_equity).sort_index()
    rets = eq.pct_change().dropna()
    final = float(eq.iloc[-1])
    years = (end - start).days / 365.25
    cagr = (final / capital) ** (1 / years) - 1 if years > 0 else 0
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    sharpe = float(rets.mean() / rets.std() * np.sqrt(365)) if rets.std() > 0 else 0
    return final, cagr, dd, sharpe, trades


def main():
    hourly, daily_close = H.build_data()
    df = hourly["btc"].set_index("dt")[["open", "high", "low", "close"]].add_prefix("btc_")
    merged = df.sort_index().reset_index()
    windows = {
        "full 2021-2026.09": (date(2021, 1, 4), date(2026, 9, 7)),
        "2024-2026.09": (date(2024, 1, 1), date(2026, 9, 7)),
        "last 12mo": (date(2025, 9, 8), date(2026, 9, 7)),
        "live window": (date(2026, 4, 2), date(2026, 9, 7)),
    }
    for wname, (s, e) in windows.items():
        print(f"\n=== BTC only: {wname} ===")
        print(f"{'variant':<12} {'final':>10} {'CAGR':>8} {'maxDD':>7} {'Sharpe':>7} {'trades':>7}")
        for v in ("bh", "bh+stop", "system", "mom-only"):
            f, c, dd, sh, t = run(merged, daily_close, s, e, v)
            print(f"{v:<12} {f:>10,.0f} {c:>8.1%} {dd:>7.1%} {sh:>7.2f} {t:>7}")


if __name__ == "__main__":
    main()
