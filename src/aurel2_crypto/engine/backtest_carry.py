"""Backtesting engine for funding rate carry strategy.

Carry is fundamentally different from directional strategies:
- Profit comes from funding payments, not price movement
- Position is market-neutral (long spot + short perp)
- Need to simulate funding payments every 8 hours
- Main risk is basis divergence during liquidation events
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import structlog

from aurel2_crypto.core.assets import ASSET_REGISTRY, CARRY_ASSETS
from aurel2_crypto.core.models import CryptoAsset, FundingPayment

logger = structlog.get_logger()


@dataclass
class CarryBacktestResult:
    """Results of a carry strategy backtest."""
    start_date: date
    end_date: date
    initial_capital: float
    final_value: float
    funding_payments: list[FundingPayment]
    total_funding_collected: float = 0.0
    total_funding_paid: float = 0.0
    net_funding: float = 0.0
    num_carry_periods: int = 0
    pct_time_in_carry: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0

    # Annualized metrics
    total_return: float = 0.0
    cagr: float = 0.0

    # Benchmark
    benchmark_final: float | None = None

    def calculate_metrics(self, equity_curve: list[float]):
        if not equity_curve:
            return

        self.total_return = (self.final_value / self.initial_capital) - 1
        years = (self.end_date - self.start_date).days / 365.25
        if years > 0:
            self.cagr = (self.final_value / self.initial_capital) ** (1 / years) - 1

        # Max drawdown
        peak = equity_curve[0]
        max_dd = 0.0
        for v in equity_curve:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        self.max_drawdown = max_dd

        # Sharpe (daily)
        if len(equity_curve) > 1:
            returns = pd.Series(equity_curve).pct_change().dropna()
            if len(returns) > 0 and returns.std() > 0:
                self.sharpe_ratio = (returns.mean() * 365) / (returns.std() * np.sqrt(365))

        # Funding stats
        self.total_funding_collected = sum(
            p.payment for p in self.funding_payments if p.payment > 0
        )
        self.total_funding_paid = abs(sum(
            p.payment for p in self.funding_payments if p.payment < 0
        ))
        self.net_funding = self.total_funding_collected - self.total_funding_paid

    def print_summary(self):
        print("\n" + "=" * 60)
        print("CARRY STRATEGY BACKTEST RESULTS")
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
        print(f"Funding collected: ${self.total_funding_collected:,.2f}")
        print(f"Funding paid: ${self.total_funding_paid:,.2f}")
        print(f"Net funding: ${self.net_funding:,.2f}")
        print(f"Carry periods (8h): {self.num_carry_periods}")
        print(f"Time in carry: {self.pct_time_in_carry:.0%}")
        if self.benchmark_final:
            bench_return = (self.benchmark_final / self.initial_capital) - 1
            print("-" * 60)
            print(f"BTC buy-and-hold: ${self.benchmark_final:,.2f} ({bench_return:+.2%})")
            print(f"Alpha: {self.total_return - bench_return:+.2%}")
        print("=" * 60)


class CarryBacktestEngine:
    """Backtest engine for funding rate carry.

    Simulates the carry trade:
    1. When funding rate > threshold: open carry position
       - Long spot (buy BTC/ETH)
       - Short perp (sell BTC/ETH perp) — same notional
       - Net market exposure: ~0
    2. Every 8h funding settlement: collect/pay funding on short perp
    3. When funding rate < exit threshold: close carry
       - Sell spot, close perp short
       - P&L = sum of funding payments - trading fees - basis risk

    Simplifications in backtest:
    - Assumes perfect basis (spot price = perp mark price)
    - No margin requirements modeled (would reduce capital efficiency)
    - Funding applied at exact rate (no slippage on funding)
    - Spot buy/sell fees modeled (0.1% each way)
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        entry_rate: float = 0.0001,
        exit_rate: float = -0.0001,
        spot_fee_pct: float = 0.001,
        futures_fee_pct: float = 0.0005,
        position_pct: float = 0.90,  # Deploy 90% of capital to carry
    ):
        self.initial_capital = initial_capital
        self.entry_rate = entry_rate
        self.exit_rate = exit_rate
        self.spot_fee_pct = spot_fee_pct
        self.futures_fee_pct = futures_fee_pct
        self.position_pct = position_pct

    def run(
        self,
        funding_rates: pd.DataFrame,
        prices: pd.DataFrame,
        start_date: date,
        end_date: date,
        assets: list[CryptoAsset] | None = None,
    ) -> CarryBacktestResult:
        """Run carry backtest.

        Args:
            funding_rates: DataFrame with timestamp, symbol, funding_rate
            prices: OHLCV DataFrame for spot prices
            start_date, end_date: Backtest period
            assets: Which assets to trade carry on (default: BTC, ETH)
        """
        assets = assets or CARRY_ASSETS
        capital = self.initial_capital
        equity_curve = [capital]
        funding_payments: list[FundingPayment] = []
        total_funding_periods = 0

        # State per asset
        in_carry: dict[CryptoAsset, bool] = {a: False for a in assets}
        carry_size: dict[CryptoAsset, float] = {a: 0.0 for a in assets}

        # Process funding rates chronologically
        all_rates = funding_rates.copy()
        all_rates = all_rates.sort_values("timestamp")
        all_rates["date"] = pd.to_datetime(all_rates["timestamp"]).dt.date
        all_rates = all_rates[
            (all_rates["date"] >= start_date) & (all_rates["date"] <= end_date)
        ]

        if all_rates.empty:
            return CarryBacktestResult(
                start_date=start_date,
                end_date=end_date,
                initial_capital=self.initial_capital,
                final_value=self.initial_capital,
                funding_payments=[],
            )

        # Group by funding period (each row is one 8h period)
        for _, row in all_rates.iterrows():
            symbol = row["symbol"]
            rate = float(row["funding_rate"])
            ts = row["timestamp"]

            # Find which asset this is
            asset_id = None
            for a in assets:
                if ASSET_REGISTRY[a].perp_symbol == symbol:
                    asset_id = a
                    break
            if asset_id is None:
                continue

            total_funding_periods += 1

            # Entry: rate above threshold and not already in carry
            if not in_carry[asset_id] and rate >= self.entry_rate:
                # Allocate capital evenly across assets
                alloc = capital * self.position_pct / len(assets)
                entry_fee = alloc * (self.spot_fee_pct + self.futures_fee_pct)
                carry_size[asset_id] = alloc - entry_fee
                capital -= entry_fee  # Only pay fees, position is market-neutral
                in_carry[asset_id] = True

            # Collect/pay funding if in carry
            if in_carry[asset_id] and carry_size[asset_id] > 0:
                # Funding payment: short perp collects positive funding
                payment = carry_size[asset_id] * rate
                capital += payment
                funding_payments.append(FundingPayment(
                    timestamp=pd.Timestamp(ts),
                    symbol=symbol,
                    rate=rate,
                    payment=payment,
                    position_size=carry_size[asset_id],
                ))

            # Exit: rate below exit threshold
            if in_carry[asset_id] and rate <= self.exit_rate:
                exit_fee = carry_size[asset_id] * (self.spot_fee_pct + self.futures_fee_pct)
                capital -= exit_fee
                carry_size[asset_id] = 0.0
                in_carry[asset_id] = False

            equity_curve.append(capital)

        # Close any remaining positions
        for asset_id in assets:
            if in_carry[asset_id] and carry_size[asset_id] > 0:
                exit_fee = carry_size[asset_id] * (self.spot_fee_pct + self.futures_fee_pct)
                capital -= exit_fee

        equity_curve.append(capital)

        # Benchmark
        benchmark_final = self._calc_benchmark(prices, start_date, end_date)

        result = CarryBacktestResult(
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_value=capital,
            funding_payments=funding_payments,
            num_carry_periods=len(funding_payments),
            pct_time_in_carry=len(funding_payments) / max(total_funding_periods, 1),
            benchmark_final=benchmark_final,
        )
        result.calculate_metrics(equity_curve)
        return result

    def _calc_benchmark(self, prices: pd.DataFrame, start_date: date, end_date: date) -> float | None:
        btc_prices = prices[prices["symbol"] == "BTC/USDT"].copy()
        if btc_prices.empty:
            return None
        btc_prices["date"] = pd.to_datetime(btc_prices["timestamp"]).dt.date
        start_p = btc_prices[btc_prices["date"] <= start_date].sort_values("timestamp")
        end_p = btc_prices[btc_prices["date"] <= end_date].sort_values("timestamp")
        if start_p.empty or end_p.empty:
            return None
        sp = float(start_p.iloc[-1]["close"])
        ep = float(end_p.iloc[-1]["close"])
        shares = (self.initial_capital * 0.999) / sp
        return shares * ep
