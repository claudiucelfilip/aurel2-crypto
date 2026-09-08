"""Invest-and-forget check: BTC buy-and-hold core + momentum sleeve, rebalanced monthly.

Grid: sleeve = 5-coin system with stop {none, 10%, 20%} (daily-close), sleeve share {0, 50, 100}%.
Scored per window on final value and on 'vs BTC' (final / BTC-B&H final - 1).
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backtest_stop_hourly as H
import numpy as np
import pandas as pd

# Patch H.run to expose its daily equity series
_src = Path(H.__file__).read_text()
if "LAST_EQ" not in _src:
    _src = _src.replace(
        "    eq = pd.Series(day_equity).sort_index().ffill()",
        "    eq = pd.Series(day_equity).sort_index().ffill()\n    global LAST_EQ\n    LAST_EQ = eq",
    )
    exec(compile(_src, H.__file__, "exec"), H.__dict__)


def btc_bh_series(daily_close, start, end, capital=10000.0):
    s = pd.Series(daily_close["btc"]).sort_index()
    s = s[(s.index >= start) & (s.index <= end)]
    return capital * s / s.iloc[0]


def blend(eq_sys, eq_bh, w_sys, capital=10000.0):
    idx = eq_sys.index.intersection(eq_bh.index)
    rs = eq_sys.reindex(idx).pct_change().fillna(0)
    rb = eq_bh.reindex(idx).pct_change().fillna(0)
    vs, vb = capital * w_sys, capital * (1 - w_sys)
    out, last_month = [], None
    for d, (a, b) in zip(idx, zip(rs, rb)):
        vs *= 1 + a
        vb *= 1 + b
        if last_month is not None and d.month != last_month:  # monthly rebalance
            tot = vs + vb
            vs, vb = tot * w_sys, tot * (1 - w_sys)
        last_month = d.month
        out.append(vs + vb)
    return pd.Series(out, index=idx)


def stats(eq, start, end, capital=10000.0):
    final = float(eq.iloc[-1])
    years = (end - start).days / 365.25
    cagr = (final / capital) ** (1 / years) - 1
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    return final, cagr, dd


def main():
    hourly, daily_close = H.build_data()
    frames = [df.set_index("dt")[["open", "high", "low", "close"]].add_prefix(f"{a}_") for a, df in hourly.items()]
    merged = pd.concat(frames, axis=1).sort_index().reset_index()
    windows = {
        "full 2021-2026.09": (date(2021, 1, 4), date(2026, 9, 7)),
        "2024-2026.09": (date(2024, 1, 1), date(2026, 9, 7)),
        "last 12mo": (date(2025, 9, 8), date(2026, 9, 7)),
        "live window": (date(2026, 4, 2), date(2026, 9, 7)),
    }
    stops = [("none", 0.0), ("close", 0.10), ("close", 0.20)]
    for wname, (s, e) in windows.items():
        eq_bh = btc_bh_series(daily_close, s, e)
        bh_final = float(eq_bh.iloc[-1])
        print(f"\n=== {wname} | BTC B&H final {bh_final:,.0f} ===")
        print(f"{'sleeve stop':<12} {'share':>6} {'final':>10} {'CAGR':>8} {'maxDD':>7} {'vs BTC':>8}")
        for mode, pct in stops:
            H.run(merged, daily_close, s, e, mode, pct, 0.0)
            eq_sys = H.LAST_EQ
            for w in (1.0, 0.5):
                eq = eq_sys if w == 1.0 else blend(eq_sys, eq_bh, w)
                f, c, dd = stats(eq, s, e)
                label = f"{mode} {pct:.0%}" if mode != "none" else "none"
                print(f"{label:<12} {w:>6.0%} {f:>10,.0f} {c:>8.1%} {dd:>7.1%} {f / bh_final - 1:>8.1%}")
        f, c, dd = stats(eq_bh, s, e)
        print(f"{'btc b&h':<12} {'0%':>6} {f:>10,.0f} {c:>8.1%} {dd:>7.1%} {'0.0%':>8}")


if __name__ == "__main__":
    main()
