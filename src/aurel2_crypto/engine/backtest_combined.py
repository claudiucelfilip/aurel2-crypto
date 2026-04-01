"""Combined backtest engine running carry + momentum as a single portfolio.

Simulates a portfolio that allocates capital between two independent strategies:
- Carry: market-neutral funding rate collection (steady income, low drawdown)
- Momentum: directional crypto rotation (high growth, high drawdown)

The two strategies run independently with their own capital pools.
Combined equity = carry equity + momentum equity.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import structlog

from aurel2_crypto.core.assets import CARRY_ASSETS, ASSET_REGISTRY, get_all_symbols
from aurel2_crypto.core.models import CryptoAsset
from aurel2_crypto.engine.backtest import BacktestEngine, BacktestResult
from aurel2_crypto.engine.backtest_carry import CarryBacktestEngine, CarryBacktestResult
from aurel2_crypto.strategies.momentum import ShortTermMomentumStrategy

logger = structlog.get_logger()


@dataclass
class CombinedBacktestResult:
    """Results of combined carry + momentum backtest."""
    start_date: date
    end_date: date
    initial_capital: float
    carry_allocation: float
    momentum_allocation: float

    carry_result: CarryBacktestResult
    momentum_result: BacktestResult

    # Combined metrics
    final_value: float = 0.0
    total_return: float = 0.0
    cagr: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0

    # Benchmark
    benchmark_final: float | None = None

    def calculate_metrics(self, combined_curve: list[float]):
        self.final_value = self.carry_result.final_value + self.momentum_result.final_value
        self.total_return = (self.final_value / self.initial_capital) - 1

        years = (self.end_date - self.start_date).days / 365.25
        if years > 0:
            self.cagr = (self.final_value / self.initial_capital) ** (1 / years) - 1

        # Max drawdown from combined curve
        if combined_curve:
            peak = combined_curve[0]
            max_dd = 0.0
            for v in combined_curve:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
            self.max_drawdown = max_dd

            # Sharpe (weekly from combined curve)
            if len(combined_curve) > 1:
                returns = pd.Series(combined_curve).pct_change().dropna()
                if len(returns) > 0 and returns.std() > 0:
                    self.sharpe_ratio = (returns.mean() * 52) / (returns.std() * np.sqrt(52))

    def print_summary(self):
        print("\n" + "=" * 70)
        print("COMBINED PORTFOLIO BACKTEST RESULTS")
        print("=" * 70)
        print(f"Period: {self.start_date} to {self.end_date}")
        print(f"Initial Capital: ${self.initial_capital:,.2f}")
        print(f"Allocation: {self.carry_allocation:.0%} carry / {self.momentum_allocation:.0%} momentum")
        print()

        carry_capital = self.initial_capital * self.carry_allocation
        mom_capital = self.initial_capital * self.momentum_allocation

        print(f"  {'Strategy':<15} {'Capital':>10} {'Final':>12} {'Return':>10} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8}")
        print(f"  {'-'*73}")
        print(
            f"  {'Carry':<15} ${carry_capital:>9,.0f} ${self.carry_result.final_value:>11,.2f} "
            f"{self.carry_result.total_return:>9.1%} {self.carry_result.cagr:>7.1%} "
            f"{self.carry_result.max_drawdown:>7.1%} {self.carry_result.sharpe_ratio:>7.2f}"
        )
        print(
            f"  {'Momentum':<15} ${mom_capital:>9,.0f} ${self.momentum_result.final_value:>11,.2f} "
            f"{self.momentum_result.total_return:>9.1%} {self.momentum_result.cagr:>7.1%} "
            f"{self.momentum_result.max_drawdown:>7.1%} {self.momentum_result.sharpe_ratio:>7.2f}"
        )
        print(f"  {'-'*73}")
        print(
            f"  {'COMBINED':<15} ${self.initial_capital:>9,.0f} ${self.final_value:>11,.2f} "
            f"{self.total_return:>9.1%} {self.cagr:>7.1%} "
            f"{self.max_drawdown:>7.1%} {self.sharpe_ratio:>7.2f}"
        )

        if self.benchmark_final:
            bench_return = (self.benchmark_final / self.initial_capital) - 1
            years = (self.end_date - self.start_date).days / 365.25
            bench_cagr = (self.benchmark_final / self.initial_capital) ** (1 / years) - 1 if years > 0 else 0
            print()
            print(f"  BTC buy-and-hold: ${self.benchmark_final:,.2f} ({bench_return:+.1%}, CAGR {bench_cagr:.1%})")
            print(f"  Alpha (CAGR): {self.cagr - bench_cagr:+.1%}")
        print("=" * 70)


def run_combined_backtest(
    prices: pd.DataFrame,
    funding_rates: pd.DataFrame,
    start_date: date,
    end_date: date,
    initial_capital: float = 10000.0,
    carry_pct: float = 0.50,
    momentum_pct: float = 0.50,
    momentum_lookback: int = 28,
) -> CombinedBacktestResult:
    """Run carry + momentum as a combined portfolio."""

    carry_capital = initial_capital * carry_pct
    momentum_capital = initial_capital * momentum_pct

    # Run carry backtest
    carry_engine = CarryBacktestEngine(
        initial_capital=carry_capital,
        entry_rate=0.0001,
        exit_rate=-0.0001,
        position_pct=0.90,
    )
    carry_result = carry_engine.run(funding_rates, prices, start_date, end_date)

    # Run momentum backtest
    momentum_strategy = ShortTermMomentumStrategy(
        lookback_days=momentum_lookback,
        rebalance_days=7,
        switch_threshold=0.03,
    )
    momentum_engine = BacktestEngine(
        initial_capital=momentum_capital,
        transaction_cost_pct=0.001,
    )
    momentum_result = momentum_engine.run(momentum_strategy, prices, start_date, end_date)

    # Build combined equity curve (weekly snapshots from momentum, interpolate carry)
    # Use momentum snapshots as the time axis since it's weekly
    combined_curve = []
    carry_snapshots = len(carry_result.funding_payments)

    # Simple approach: take momentum weekly snapshots and add carry's linear growth
    carry_daily_rate = (carry_result.final_value / carry_capital) ** (1 / max((end_date - start_date).days, 1)) - 1

    for snap in momentum_result.snapshots:
        days_elapsed = (snap.date - start_date).days
        carry_value = carry_capital * (1 + carry_daily_rate) ** days_elapsed
        combined_curve.append(float(snap.total_value) + carry_value)

    # Benchmark
    benchmark_final = None
    btc = prices[prices["symbol"] == "BTC/USDT"].copy()
    if not btc.empty:
        btc["date"] = pd.to_datetime(btc["timestamp"]).dt.date
        sp = btc[btc["date"] <= start_date].sort_values("timestamp")
        ep = btc[btc["date"] <= end_date].sort_values("timestamp")
        if not sp.empty and not ep.empty:
            benchmark_final = (initial_capital * 0.999 / float(sp.iloc[-1]["close"])) * float(ep.iloc[-1]["close"])

    result = CombinedBacktestResult(
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        carry_allocation=carry_pct,
        momentum_allocation=momentum_pct,
        carry_result=carry_result,
        momentum_result=momentum_result,
        benchmark_final=benchmark_final,
    )
    result.calculate_metrics(combined_curve)
    return result
