"""Backtesting engine for BTC/ETH pairs trading strategy.

Pairs trading is market-neutral: simultaneously long one asset and short the other.
Profit comes from the ratio reverting to mean, not from directional price movement.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import structlog

from aurel2_crypto.core.assets import ASSET_REGISTRY
from aurel2_crypto.core.models import CryptoAsset, SignalAction
from aurel2_crypto.strategies.pairs import PairsTradingStrategy

logger = structlog.get_logger()


@dataclass
class PairsTrade:
    """Record of a completed pairs trade (open + close)."""
    entry_date: date
    exit_date: date
    side: str  # "long_a_short_b" or "short_a_long_b"
    entry_ratio: float
    exit_ratio: float
    entry_zscore: float
    exit_zscore: float
    pnl: float
    pnl_pct: float


@dataclass
class PairsBacktestResult:
    """Results of pairs trading backtest."""
    start_date: date
    end_date: date
    initial_capital: float
    final_value: float
    trades: list[PairsTrade]
    equity_curve: list[float] = field(default_factory=list)

    # Calculated metrics
    total_return: float = 0.0
    cagr: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    num_trades: int = 0
    win_rate: float = 0.0
    avg_trade_pnl_pct: float = 0.0
    avg_hold_days: float = 0.0
    pct_time_in_trade: float = 0.0

    # Benchmark
    benchmark_final: float | None = None

    def calculate_metrics(self):
        if not self.equity_curve:
            return

        self.total_return = (self.final_value / self.initial_capital) - 1
        years = (self.end_date - self.start_date).days / 365.25
        if years > 0:
            self.cagr = (self.final_value / self.initial_capital) ** (1 / years) - 1

        # Max drawdown
        peak = self.equity_curve[0]
        max_dd = 0.0
        for v in self.equity_curve:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        self.max_drawdown = max_dd

        # Sharpe (daily)
        if len(self.equity_curve) > 1:
            returns = pd.Series(self.equity_curve).pct_change().dropna()
            if len(returns) > 0 and returns.std() > 0:
                self.sharpe_ratio = (returns.mean() * 365) / (returns.std() * np.sqrt(365))

        # Trade stats
        self.num_trades = len(self.trades)
        if self.num_trades > 0:
            wins = sum(1 for t in self.trades if t.pnl > 0)
            self.win_rate = wins / self.num_trades
            self.avg_trade_pnl_pct = np.mean([t.pnl_pct for t in self.trades])
            self.avg_hold_days = np.mean([
                (t.exit_date - t.entry_date).days for t in self.trades
            ])
            days_in_trade = sum(
                (t.exit_date - t.entry_date).days for t in self.trades
            )
            total_days = (self.end_date - self.start_date).days
            self.pct_time_in_trade = days_in_trade / max(total_days, 1)

    def print_summary(self):
        print("\n" + "=" * 60)
        print("PAIRS TRADING BACKTEST RESULTS")
        print("=" * 60)
        print(f"Period: {self.start_date} to {self.end_date}")
        print(f"Initial Capital: ${self.initial_capital:,.2f}")
        print(f"Final Value: ${self.final_value:,.2f}")
        print("-" * 60)
        print(f"Total Return: {self.total_return:.2%}")
        print(f"CAGR: {self.cagr:.2%}")
        print(f"Max Drawdown: {self.max_drawdown:.2%}")
        print(f"Sharpe Ratio: {self.sharpe_ratio:.2f}")
        print("-" * 60)
        print(f"Number of Trades: {self.num_trades}")
        print(f"Win Rate: {self.win_rate:.0%}")
        print(f"Avg Trade P&L: {self.avg_trade_pnl_pct:.2%}")
        print(f"Avg Hold Days: {self.avg_hold_days:.0f}")
        print(f"Time in Trade: {self.pct_time_in_trade:.0%}")
        if self.benchmark_final:
            bench_return = (self.benchmark_final / self.initial_capital) - 1
            print("-" * 60)
            print(f"BTC buy-and-hold: ${self.benchmark_final:,.2f} ({bench_return:+.2%})")
            print(f"Alpha: {self.total_return - bench_return:+.2%}")
        print("=" * 60)


class PairsBacktestEngine:
    """Backtest engine for BTC/ETH pairs trading.

    Simulates market-neutral pairs trades:
    - When entering: allocate capital equally to long and short legs
    - P&L comes from the ratio change, not absolute price change
    - Fees on both entry and exit (4 legs total per round-trip)
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        fee_pct: float = 0.0005,  # Futures taker fee per leg
        position_pct: float = 0.90,
    ):
        self.initial_capital = initial_capital
        self.fee_pct = fee_pct
        self.position_pct = position_pct

    def run(
        self,
        strategy: PairsTradingStrategy,
        prices: pd.DataFrame,
        start_date: date,
        end_date: date,
    ) -> PairsBacktestResult:
        rebalance_dates = strategy.get_rebalance_dates(start_date, end_date)

        capital = self.initial_capital
        equity_curve = [capital]
        completed_trades: list[PairsTrade] = []

        # Position state
        position_side: str | None = None
        entry_date: date | None = None
        entry_ratio: float = 0.0
        entry_zscore: float = 0.0
        position_notional: float = 0.0  # Notional per leg

        # Precompute price ratio
        symbol_a = ASSET_REGISTRY[strategy.asset_a].symbol
        symbol_b = ASSET_REGISTRY[strategy.asset_b].symbol

        for calc_date in rebalance_dates:
            context = {
                "position_side": position_side,
                "entry_date": entry_date,
            }

            signal = strategy.generate_signal(prices, calc_date, None, context)
            zscore = signal.metadata.get("zscore", 0)
            current_ratio = signal.metadata.get("ratio", 0)

            if signal.action in (SignalAction.SHORT, SignalAction.BUY) and not position_side:
                # Open new pairs position
                new_side = signal.metadata.get("position_side")
                if new_side:
                    position_notional = capital * self.position_pct / 2  # Per leg
                    entry_fees = position_notional * 2 * self.fee_pct  # 2 legs
                    capital -= entry_fees
                    position_side = new_side
                    entry_date = calc_date
                    entry_ratio = current_ratio
                    entry_zscore = zscore

            elif signal.action == SignalAction.COVER and position_side:
                # Close pairs position — calculate P&L from ratio change
                if entry_ratio > 0 and current_ratio > 0:
                    ratio_change = (current_ratio - entry_ratio) / entry_ratio

                    if position_side == "long_a_short_b":
                        # Long A (BTC), Short B (ETH) — profit when ratio rises
                        pnl = position_notional * ratio_change
                    else:
                        # Short A (BTC), Long B (ETH) — profit when ratio falls
                        pnl = position_notional * (-ratio_change)

                    exit_fees = position_notional * 2 * self.fee_pct
                    net_pnl = pnl - exit_fees
                    capital += net_pnl
                    pnl_pct = net_pnl / (position_notional * 2) if position_notional > 0 else 0

                    completed_trades.append(PairsTrade(
                        entry_date=entry_date,
                        exit_date=calc_date,
                        side=position_side,
                        entry_ratio=entry_ratio,
                        exit_ratio=current_ratio,
                        entry_zscore=entry_zscore,
                        exit_zscore=zscore,
                        pnl=net_pnl,
                        pnl_pct=pnl_pct,
                    ))

                position_side = None
                entry_date = None
                position_notional = 0.0

            # Mark-to-market for equity curve
            if position_side and entry_ratio > 0 and current_ratio > 0:
                ratio_change = (current_ratio - entry_ratio) / entry_ratio
                if position_side == "long_a_short_b":
                    mtm_pnl = position_notional * ratio_change
                else:
                    mtm_pnl = position_notional * (-ratio_change)
                equity_curve.append(capital + mtm_pnl)
            else:
                equity_curve.append(capital)

        # Close any open position at end
        if position_side:
            equity_curve.append(capital)

        # Benchmark
        benchmark_final = self._calc_benchmark(prices, start_date, end_date)

        result = PairsBacktestResult(
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_value=capital,
            trades=completed_trades,
            equity_curve=equity_curve,
            benchmark_final=benchmark_final,
        )
        result.calculate_metrics()
        return result

    def _calc_benchmark(self, prices: pd.DataFrame, start_date: date, end_date: date) -> float | None:
        btc = prices[prices["symbol"] == "BTC/USDT"].copy()
        if btc.empty:
            return None
        btc["date"] = pd.to_datetime(btc["timestamp"]).dt.date
        sp = btc[btc["date"] <= start_date].sort_values("timestamp")
        ep = btc[btc["date"] <= end_date].sort_values("timestamp")
        if sp.empty or ep.empty:
            return None
        return (self.initial_capital * 0.999 / float(sp.iloc[-1]["close"])) * float(ep.iloc[-1]["close"])
