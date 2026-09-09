"""1M / 1Y / 5Y comparison ending today: BTC B&H vs BTC-only momentum vs the live 5-coin system."""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backtest_stop_hourly as H
import pandas as pd

END = date(2026, 9, 8)
WINDOWS = {"1M": END - timedelta(days=30), "1Y": END - timedelta(days=365), "5Y": END - timedelta(days=5 * 365)}


def main():
    hourly, daily_close = H.build_data()  # all 5 coins
    frames = [df.set_index("dt")[["open", "high", "low", "close"]].add_prefix(f"{a}_") for a, df in hourly.items()]
    merged5 = pd.concat(frames, axis=1).sort_index().reset_index()

    import backtest_btc_only as B  # sets H.SYMBOLS to BTC only from here on
    merged_btc = hourly["btc"].set_index("dt")[["open", "high", "low", "close"]].add_prefix("btc_").sort_index().reset_index()

    for wname, start in WINDOWS.items():
        bh = B.run(merged_btc, daily_close, start, END, "bh")
        rows = [
            ("BTC buy & hold", bh),
            ("BTC momentum, no stop", B.run(merged_btc, daily_close, start, END, "mom-only")),
            ("BTC momentum + 10% stop", B.run(merged_btc, daily_close, start, END, "system")),
        ]
        H.SYMBOLS = {"btc": "BTC/USDT", "eth": "ETH/USDT", "sol": "SOL/USDT", "avax": "AVAX/USDT", "link": "LINK/USDT"}
        rows.append(("5-coin system (live cfg)", H.run(merged5, daily_close, start, END, "close", 0.10, 0.0)))
        H.SYMBOLS = {"btc": "BTC/USDT"}

        print(f"\n=== {wname}: {start} -> {END} ===")
        print(f"{'variant':<26} {'final':>9} {'return':>8} {'maxDD':>7} {'trades':>7} {'vs BTC':>8}")
        for name, (final, cagr, dd, sharpe, trades) in rows:
            print(f"{name:<26} {final:>9,.0f} {final / 10000 - 1:>8.1%} {dd:>7.1%} {trades:>7} {final / bh[0] - 1:>+8.1%}")


if __name__ == "__main__":
    main()
