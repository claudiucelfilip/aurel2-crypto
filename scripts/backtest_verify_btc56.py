"""Deployed rule check: BTC-only, 56d momentum, Monday entry, daily exit, no stop. Hourly engine + daily calendar years."""
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import backtest_stop_hourly as H
import backtest_btc_only as B
import backtest_oos as O
import ccxt, pandas as pd

print("### hourly engine (BTC only), lookback 56 vs 28, no stop")
hourly, daily_close = H.build_data()
df = hourly["btc"].set_index("dt")[["open", "high", "low", "close"]].add_prefix("btc_")
merged = df.sort_index().reset_index()
END = merged.dt.max().date()
windows = {"2021-01 -> now": (date(2021, 1, 4), END), "2024-01 -> now": (date(2024, 1, 1), END),
           "last 12mo": (date(2025, 10, 23), END), "paper window 2026-04-02 -> now": (date(2026, 4, 2), END)}
for lb in (56, 28):
    H.LOOKBACK = lb
    print(f"\n-- lookback {lb} (data to {END})")
    print(f"{'window':<32} {'variant':<9} {'final':>9} {'CAGR':>7} {'maxDD':>7} {'Sharpe':>6} {'trd':>4}")
    for wn, (s, e) in windows.items():
        for v in ("bh", "mom-only"):
            f, c, dd, sh, t = B.run(merged, daily_close, s, e, v)
            print(f"{wn:<32} {v:<9} {f:>9,.0f} {c:>7.1%} {dd:>7.1%} {sh:>6.2f} {t:>4}")

print("\n### daily engine calendar years, BTC-only momentum, lookbacks 42/56/70 (neighbours) vs B&H")
ex = ccxt.binance({"enableRateLimit": True})
raw = O.fetch(ex, "BTC/USDT")
C = pd.DataFrame({"btc": raw.c}).sort_index(); Op = pd.DataFrame({"btc": raw.o}).sort_index()
print(f"{'year':<6} {'BTC B&H':>9} {'mom42':>9} {'mom56':>9} {'mom70':>9}")
for y in range(2018, 2027):
    s, e = date(y, 1, 1), min(date(y, 12, 31), C.index[-1].date() if hasattr(C.index[-1], 'date') else C.index[-1])
    b = C.btc[(C.index >= s) & (C.index <= e)]
    cells = []
    for lb in (42, 56, 70):
        m, _ = O.run(Op, C, ["btc"], s, e, lb)
        cells.append(f"{m.iloc[-1] / 1e4 - 1:>+9.0%}")
    print(f"{y:<6} {b.iloc[-1] / b.iloc[0] - 1:>+9.0%} " + " ".join(cells))
