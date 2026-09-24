"""Overfitting check: BTC-only momentum vs rotation on untouched 2017-2020 data + lookback robustness.

Daily engine (no stop by default): signal on prior close, trade at today's open.
Monday = full rebalance; Tue-Sun = exit to cash if all momenta <= 0. 0.1% fee, 6% APY idle.
Rotation universe = whichever of BTC/ETH/SOL/AVAX/LINK had listed history at the time.
"""
import time
from datetime import date, datetime
import ccxt
import numpy as np
import pandas as pd

SYMS = {"btc": "BTC/USDT", "eth": "ETH/USDT", "sol": "SOL/USDT", "avax": "AVAX/USDT", "link": "LINK/USDT"}
FEE, APY = 0.001, 0.06


def fetch(ex, sym):
    rows, cur = [], int(datetime(2017, 8, 1).timestamp() * 1000)
    while True:
        for a in range(10):
            try:
                b = ex.fetch_ohlcv(sym, "1d", since=cur, limit=1000); break
            except Exception:
                time.sleep(2 * (a + 1))
        if not b:
            break
        rows += b
        if len(b) < 1000:
            break
        cur = b[-1][0] + 86400_000
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).drop_duplicates("ts")
    df.index = pd.to_datetime(df.ts, unit="ms").dt.date
    return df


def run(O, C, assets, start, end, lb, stop=None, cap=10000.0):
    days = [d for d in C.index if start <= d <= end]
    cash, sh, hold, hwm = cap, 0.0, None, 0.0
    dy = (1 + APY) ** (1 / 365) - 1
    eq, trades = [], 0
    idx = {d: i for i, d in enumerate(C.index)}
    for d in days:
        i = idx[d]
        if i < lb + 1:
            eq.append(cash); continue
        prev, past = C.index[i - 1], C.index[i - 1 - lb]
        mom = {a: C.at[prev, a] / C.at[past, a] - 1 for a in assets
               if not np.isnan(C.at[prev, a]) and not np.isnan(C.at[past, a])}
        if d.weekday() == 0 and mom:
            pos = {a: m for a, m in mom.items() if m > 0}
            tgt = max(pos, key=pos.get) if pos else None
            if hold and tgt and hold in pos and pos[tgt] - pos[hold] <= 0.03:
                tgt = hold
            if tgt != hold:
                if hold:
                    cash, sh, hold = sh * O.at[d, hold] * (1 - FEE), 0.0, None; trades += 1
                if tgt:
                    sh, cash, hold, hwm = cash * (1 - FEE) / O.at[d, tgt], 0.0, tgt, O.at[d, tgt]; trades += 1
        elif hold and mom and all(m <= 0 for m in mom.values()):
            cash, sh, hold = sh * O.at[d, hold] * (1 - FEE), 0.0, None; trades += 1
        if hold and stop:
            hwm = max(hwm, C.at[d, hold])
            if C.at[d, hold] <= hwm * (1 - stop):
                cash, sh, hold = sh * C.at[d, hold] * (1 - FEE), 0.0, None; trades += 1
        if not hold:
            cash *= 1 + dy
        eq.append(cash + (sh * C.at[d, hold] if hold else 0))
    return pd.Series(eq, index=days), trades


def st(e):
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    return e.iloc[-1], (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1, ((e - e.cummax()) / e.cummax()).min()


def main():
    ex = ccxt.binance({"enableRateLimit": True})
    raw = {a: fetch(ex, s) for a, s in SYMS.items()}
    C = pd.DataFrame({a: d.c for a, d in raw.items()}).sort_index()
    O = pd.DataFrame({a: d.o for a, d in raw.items()}).sort_index()
    for a in SYMS:
        print(f"{a}: data from {raw[a].index[0]}")
    windows = {
        "OUT-OF-SAMPLE 2017-09 -> 2020-12": (date(2017, 9, 1), date(2020, 12, 31)),
        "IN-SAMPLE 2021-01 -> 2026-09": (date(2021, 1, 1), date(2026, 9, 22)),
        "FULL 2017-09 -> 2026-09": (date(2017, 9, 1), date(2026, 9, 22)),
    }
    for wn, (s, e) in windows.items():
        bh = C.btc[(C.index >= s) & (C.index <= e)]
        bh = 10000 * bh / bh.iloc[0]
        bf, bc, bd = st(bh)
        print(f"\n=== {wn} | BTC B&H {bf:,.0f}  CAGR {bc:.1%}  maxDD {bd:.1%} ===")
        print(f"{'variant':<26} {'lb':>3} {'final':>10} {'CAGR':>7} {'maxDD':>7} {'trd':>4} {'vsBTC':>8}")
        for name, assets, stop in (("BTC-only momentum", ["btc"], None),
                                   ("rotation, no stop", list(SYMS), None),
                                   ("rotation +10% stop (live)", list(SYMS), 0.10)):
            for lb in (14, 21, 28, 42, 56):
                eqs, t = run(O, C, assets, s, e, lb, stop)
                f, c, dd = st(eqs)
                print(f"{name:<26} {lb:>3} {f:>10,.0f} {c:>7.1%} {dd:>7.1%} {t:>4} {f / bf - 1:>+8.1%}")
    # calendar-year view, 28d
    print("\n=== calendar years (28d lookback), return % ===")
    print(f"{'year':<6} {'BTC B&H':>9} {'BTC-mom':>9} {'rotation':>9}")
    for y in range(2018, 2027):
        s, e = date(y, 1, 1), min(date(y, 12, 31), date(2026, 9, 22))
        b = C.btc[(C.index >= s) & (C.index <= e)]
        m, _ = run(O, C, ["btc"], s, e, 28)
        r, _ = run(O, C, list(SYMS), s, e, 28)
        print(f"{y:<6} {b.iloc[-1] / b.iloc[0] - 1:>+9.0%} {m.iloc[-1] / 1e4 - 1:>+9.0%} {r.iloc[-1] / 1e4 - 1:>+9.0%}")


if __name__ == "__main__":
    main()
