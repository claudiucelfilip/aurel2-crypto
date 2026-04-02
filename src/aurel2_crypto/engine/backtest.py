"""Backtesting engine for crypto strategies.

Adapted from Aurel2's backtest engine for 24/7 crypto markets:
- Weekly rebalancing (vs monthly)
- Instant settlement (vs T+2)
- Transaction fees modeled as percentage
- Supports spot-only strategies (momentum) for now
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import structlog

from aurel2_crypto.core.assets import ASSET_REGISTRY
from aurel2_crypto.core.models import (
    CryptoAsset,
    PortfolioSnapshot,
    Position,
    PositionSide,
    SignalAction,
    Trade,
)
from aurel2_crypto.strategies.base import BaseStrategy

logger = structlog.get_logger()


@dataclass
class BacktestResult:
    """Results of a backtest run."""
    start_date: date
    end_date: date
    initial_capital: float
    final_value: float
    trades: list[Trade]
    snapshots: list[PortfolioSnapshot]
    benchmark_symbol: str = "BTC/USDT"
    benchmark_final: float | None = None

    # Calculated metrics
    total_return: float = 0.0
    cagr: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    num_trades: int = 0
    turnover: float = 0.0
    win_rate: float = 0.0

    def calculate_metrics(self):
        if not self.snapshots:
            return

        values = [float(s.total_value) for s in self.snapshots]
        dates = [s.date for s in self.snapshots]
        self.total_return = (self.final_value / self.initial_capital) - 1

        years = (self.end_date - self.start_date).days / 365.25
        if years > 0:
            self.cagr = (self.final_value / self.initial_capital) ** (1 / years) - 1

        # Max drawdown
        peak = values[0]
        max_dd = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        self.max_drawdown = max_dd

        # Sharpe ratio (annualized)
        if len(values) > 1:
            returns = pd.Series(values).pct_change().dropna()
            if len(returns) > 0 and returns.std() > 0:
                # Detect frequency from snapshot spacing
                avg_days = (dates[-1] - dates[0]).days / max(len(dates) - 1, 1)
                periods_per_year = 365 / max(avg_days, 1)
                self.sharpe_ratio = (
                    returns.mean() * periods_per_year
                ) / (returns.std() * np.sqrt(periods_per_year))

        # Sortino ratio
        if len(values) > 1:
            returns = pd.Series(values).pct_change().dropna()
            downside = returns[returns < 0]
            if len(downside) > 0 and downside.std() > 0:
                periods_per_year = 52
                self.sortino_ratio = (
                    returns.mean() * periods_per_year
                ) / (downside.std() * np.sqrt(periods_per_year))

        # Calmar ratio
        if self.max_drawdown > 0:
            self.calmar_ratio = self.cagr / self.max_drawdown

        # Trade stats
        buy_trades = [t for t in self.trades if t.action in (SignalAction.BUY,)]
        self.num_trades = len(buy_trades)
        if years > 0:
            self.turnover = self.num_trades / years

        # Win rate (compare sell price vs buy price)
        if self.num_trades > 0:
            wins = 0
            for i, trade in enumerate(self.trades):
                if trade.action == SignalAction.SELL and i > 0:
                    prev_buy = None
                    for j in range(i - 1, -1, -1):
                        if self.trades[j].action == SignalAction.BUY:
                            prev_buy = self.trades[j]
                            break
                    if prev_buy and trade.price > prev_buy.price:
                        wins += 1
            sell_trades = [t for t in self.trades if t.action == SignalAction.SELL]
            if sell_trades:
                self.win_rate = wins / len(sell_trades)

    def print_summary(self):
        print("\n" + "=" * 60)
        print("BACKTEST RESULTS")
        print("=" * 60)
        print(f"Period: {self.start_date} to {self.end_date}")
        print(f"Initial Capital: ${self.initial_capital:,.2f}")
        print(f"Final Value: ${self.final_value:,.2f}")
        print("-" * 60)
        print(f"Total Return: {self.total_return:.2%}")
        print(f"CAGR: {self.cagr:.2%}")
        print(f"Max Drawdown: {self.max_drawdown:.2%}")
        print(f"Sharpe Ratio: {self.sharpe_ratio:.2f}")
        print(f"Sortino Ratio: {self.sortino_ratio:.2f}")
        print(f"Calmar Ratio: {self.calmar_ratio:.2f}")
        print(f"Number of Trades: {self.num_trades}")
        print(f"Win Rate: {self.win_rate:.0%}")
        if self.benchmark_final:
            bench_return = (self.benchmark_final / self.initial_capital) - 1
            years = (self.end_date - self.start_date).days / 365.25
            bench_cagr = (self.benchmark_final / self.initial_capital) ** (1 / years) - 1 if years > 0 else 0
            print("-" * 60)
            print(f"Benchmark ({self.benchmark_symbol}):")
            print(f"  Final Value: ${self.benchmark_final:,.2f}")
            print(f"  Total Return: {bench_return:.2%}")
            print(f"  CAGR: {bench_cagr:.2%}")
            print(f"  Alpha (total): {self.total_return - bench_return:+.2%}")
            print(f"  Alpha (CAGR): {self.cagr - bench_cagr:+.2%}")
        print("=" * 60)


class BacktestEngine:
    """Backtesting engine for crypto strategies.

    Runs a strategy on historical price data with:
    - Configurable transaction costs
    - Portfolio snapshots at each rebalance
    - Benchmark comparison (default: BTC buy-and-hold)
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        transaction_cost_pct: float = 0.001,
        benchmark_symbol: str = "BTC/USDT",
        trailing_stop_pct: float = 0.0,
    ):
        self.initial_capital = initial_capital
        self.transaction_cost_pct = transaction_cost_pct
        self.benchmark_symbol = benchmark_symbol
        self.trailing_stop_pct = trailing_stop_pct

    def run(
        self,
        strategy: BaseStrategy,
        prices: pd.DataFrame,
        start_date: date,
        end_date: date,
        regime_detector=None,
    ) -> BacktestResult:
        """Run a backtest for a single strategy.

        Args:
            strategy: Strategy instance to test
            prices: Historical OHLCV data with columns: timestamp, close, symbol
            start_date: Backtest start date
            end_date: Backtest end date
            regime_detector: Optional RegimeDetector for position sizing overlay
        """
        rebalance_dates = strategy.get_rebalance_dates(start_date, end_date)
        if not rebalance_dates:
            raise ValueError("No rebalance dates generated")

        # Build set of all trading days for daily stop-loss checks
        rebalance_set = set(rebalance_dates)
        all_days = []
        if self.trailing_stop_pct > 0:
            current = start_date
            while current <= end_date:
                all_days.append(current)
                current += timedelta(days=1)
        else:
            all_days = rebalance_dates

        cash = Decimal(str(self.initial_capital))
        current_holding: CryptoAsset | None = None
        current_shares = Decimal("0")
        trades: list[Trade] = []
        snapshots: list[PortfolioSnapshot] = []
        high_water_mark: float = 0.0
        stop_triggered = False

        for calc_date in all_days:
            is_rebalance = calc_date in rebalance_set

            # Daily trailing stop-loss check
            if (self.trailing_stop_pct > 0
                    and current_holding
                    and current_holding != CryptoAsset.USDT):
                asset = ASSET_REGISTRY[current_holding]
                price = self._get_price(prices, asset.symbol, calc_date)
                if price:
                    if price > high_water_mark:
                        high_water_mark = price
                    if high_water_mark > 0:
                        drawdown = (high_water_mark - price) / high_water_mark
                        if drawdown >= self.trailing_stop_pct:
                            # Stop triggered — sell immediately
                            proceeds = float(current_shares) * price
                            fee = proceeds * self.transaction_cost_pct
                            cash = Decimal(str(proceeds - fee))
                            trades.append(Trade(
                                date=calc_date,
                                asset=asset,
                                action=SignalAction.SELL,
                                shares=current_shares,
                                price=price,
                                commission=fee,
                            ))
                            current_holding = CryptoAsset.USDT
                            current_shares = Decimal("0")
                            high_water_mark = 0.0
                            stop_triggered = True

            # Only run strategy on rebalance dates
            if not is_rebalance:
                # Still take daily snapshot if we have a stop-loss
                if self.trailing_stop_pct > 0:
                    total = cash
                    positions = []
                    if current_holding and current_holding != CryptoAsset.USDT:
                        a = ASSET_REGISTRY[current_holding]
                        p = self._get_price(prices, a.symbol, calc_date)
                        if p:
                            total += current_shares * Decimal(str(p))
                            positions.append(Position(
                                asset=a, shares=current_shares,
                                entry_price=p, entry_date=calc_date, current_price=p,
                            ))
                    snapshots.append(PortfolioSnapshot(
                        date=calc_date, cash=cash, positions=positions, total_value=total,
                    ))
                continue

            stop_triggered = False
            signal = strategy.generate_signal(prices, calc_date, current_holding)

            # Get current price for portfolio valuation
            position_value = Decimal("0")
            if current_holding and current_holding != CryptoAsset.USDT:
                asset = ASSET_REGISTRY[current_holding]
                price = self._get_price(prices, asset.symbol, calc_date)
                if price:
                    position_value = current_shares * Decimal(str(price))

            # Execute signal
            if signal.action == SignalAction.BUY and signal.asset_id:
                target = signal.asset_id

                if target == CryptoAsset.USDT:
                    # Going to cash
                    if current_holding and current_holding != CryptoAsset.USDT:
                        sell_asset = ASSET_REGISTRY[current_holding]
                        sell_price = self._get_price(prices, sell_asset.symbol, calc_date)
                        if sell_price:
                            proceeds = float(current_shares) * sell_price
                            fee = proceeds * self.transaction_cost_pct
                            cash = Decimal(str(proceeds - fee))
                            trades.append(Trade(
                                date=calc_date,
                                asset=sell_asset,
                                action=SignalAction.SELL,
                                shares=current_shares,
                                price=sell_price,
                                commission=fee,
                            ))
                            current_holding = CryptoAsset.USDT
                            current_shares = Decimal("0")

                elif target != current_holding:
                    # Sell current if holding something
                    if current_holding and current_holding != CryptoAsset.USDT:
                        sell_asset = ASSET_REGISTRY[current_holding]
                        sell_price = self._get_price(prices, sell_asset.symbol, calc_date)
                        if sell_price:
                            proceeds = float(current_shares) * sell_price
                            fee = proceeds * self.transaction_cost_pct
                            cash = Decimal(str(proceeds - fee))
                            trades.append(Trade(
                                date=calc_date,
                                asset=sell_asset,
                                action=SignalAction.SELL,
                                shares=current_shares,
                                price=sell_price,
                                commission=fee,
                            ))

                    # Buy new target (apply regime overlay if available)
                    buy_asset = ASSET_REGISTRY[target]
                    buy_price = self._get_price(prices, buy_asset.symbol, calc_date)
                    if buy_price and float(cash) > 0:
                        available = float(cash)
                        # Regime overlay: scale position by multiplier
                        if regime_detector:
                            regime = regime_detector.detect_regime(prices, calc_date)
                            multiplier = regime["position_multiplier"]
                            deploy = available * min(multiplier, 1.0)
                            # Excess cash held as buffer (multiplier > 1 uses all cash)
                            if multiplier >= 1.0:
                                deploy = available
                        else:
                            deploy = available
                        fee = deploy * self.transaction_cost_pct
                        buy_amount = deploy - fee
                        shares = Decimal(str(buy_amount / buy_price))
                        cash = Decimal(str(available - deploy))
                        current_holding = target
                        current_shares = shares
                        high_water_mark = buy_price  # Reset HWM on new position
                        trades.append(Trade(
                            date=calc_date,
                            asset=buy_asset,
                            action=SignalAction.BUY,
                            shares=shares,
                            price=buy_price,
                            commission=fee,
                        ))

            elif signal.action == SignalAction.SELL:
                # Sell to cash
                if current_holding and current_holding != CryptoAsset.USDT:
                    sell_asset = ASSET_REGISTRY[current_holding]
                    sell_price = self._get_price(prices, sell_asset.symbol, calc_date)
                    if sell_price:
                        proceeds = float(current_shares) * sell_price
                        fee = proceeds * self.transaction_cost_pct
                        cash = Decimal(str(proceeds - fee))
                        trades.append(Trade(
                            date=calc_date,
                            asset=sell_asset,
                            action=SignalAction.SELL,
                            shares=current_shares,
                            price=sell_price,
                            commission=fee,
                        ))
                        current_holding = CryptoAsset.USDT
                        current_shares = Decimal("0")

            # Take snapshot
            total = cash
            positions = []
            if current_holding and current_holding != CryptoAsset.USDT:
                asset = ASSET_REGISTRY[current_holding]
                price = self._get_price(prices, asset.symbol, calc_date)
                if price:
                    mv = current_shares * Decimal(str(price))
                    total += mv
                    positions.append(Position(
                        asset=asset,
                        shares=current_shares,
                        entry_price=float(trades[-1].price) if trades else price,
                        entry_date=calc_date,
                        current_price=price,
                    ))
            else:
                total = cash

            snapshots.append(PortfolioSnapshot(
                date=calc_date,
                cash=cash,
                positions=positions,
                total_value=total,
            ))

        # Final value
        final_value = float(snapshots[-1].total_value) if snapshots else self.initial_capital

        # Benchmark: BTC buy-and-hold
        benchmark_final = self._calc_benchmark(prices, start_date, end_date)

        result = BacktestResult(
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_value=final_value,
            trades=trades,
            snapshots=snapshots,
            benchmark_symbol=self.benchmark_symbol,
            benchmark_final=benchmark_final,
        )
        result.calculate_metrics()
        return result

    def _get_price(self, prices: pd.DataFrame, symbol: str, calc_date: date) -> float | None:
        """Get the closing price for a symbol on or before calc_date."""
        asset_prices = prices[prices["symbol"] == symbol].copy()
        if asset_prices.empty:
            return None

        asset_prices["date"] = pd.to_datetime(asset_prices["timestamp"]).dt.date
        available = asset_prices[asset_prices["date"] <= calc_date]
        if available.empty:
            return None

        return float(available.sort_values("timestamp").iloc[-1]["close"])

    def _calc_benchmark(self, prices: pd.DataFrame, start_date: date, end_date: date) -> float | None:
        """Calculate benchmark buy-and-hold final value."""
        start_price = self._get_price(prices, self.benchmark_symbol, start_date)
        end_price = self._get_price(prices, self.benchmark_symbol, end_date)

        if start_price and end_price:
            fee = self.initial_capital * self.transaction_cost_pct
            shares = (self.initial_capital - fee) / start_price
            return shares * end_price

        return None
