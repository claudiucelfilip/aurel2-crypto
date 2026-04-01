"""Funding rate carry strategy.

Exploits the structural premium in perpetual futures funding rates.
When retail longs dominate, funding is positive — shorts collect payment.

Mechanism:
  1. Long spot (to stay market-neutral)
  2. Short perp (to collect funding)
  3. Net exposure: ~zero. Profit = funding payments collected.

Entry: funding rate > min threshold (default 0.01% per 8h ≈ 10% annualized)
Exit: funding rate flips negative or drops below exit threshold

Based on research:
- BIS Working Paper: carry reaching 40%+ p.a. at peaks
- Academic Sharpe ratio: 8.76 (Tether-denominated, full sample)
- Risk: basis blow-ups during liquidation cascades (FTX, LUNA)

References:
- BIS Working Paper No. 1087 ("Crypto Carry")
- CMU study on BTC funding rate carry
"""

from datetime import date, timedelta

import pandas as pd

from aurel2_crypto.core.assets import CARRY_ASSETS, ASSET_REGISTRY
from aurel2_crypto.core.models import CryptoAsset, SignalAction, StrategySignal
from aurel2_crypto.strategies.base import BaseStrategy


class FundingCarryStrategy(BaseStrategy):
    """Funding rate carry: long spot + short perp to collect funding.

    This strategy is market-neutral — profit comes from funding payments,
    not price direction. It runs on BTC and ETH (most liquid perps).

    Signal logic:
    - If funding rate > entry threshold → open carry (long spot + short perp)
    - If funding rate < exit threshold → close carry
    - Position sized by annualized rate (higher rate → larger position)
    """

    name = "funding_carry"

    def __init__(
        self,
        entry_rate: float = 0.0001,    # 0.01% per 8h ≈ 10% annualized
        exit_rate: float = -0.0001,     # Close when rate flips negative
        max_position_pct: float = 0.50, # Max 50% of carry allocation per asset
        assets: list[CryptoAsset] | None = None,
        check_interval_hours: int = 8,  # Aligned with Binance funding intervals
    ):
        self.entry_rate = entry_rate
        self.exit_rate = exit_rate
        self.max_position_pct = max_position_pct
        self.assets = assets or CARRY_ASSETS
        self.check_interval_hours = check_interval_hours

    def generate_signal(
        self,
        prices: pd.DataFrame,
        calc_date: date,
        current_holding: CryptoAsset | None,
        context: dict | None = None,
    ) -> StrategySignal:
        """Generate carry signal based on funding rates.

        Args:
            context: Must contain 'funding_rates' DataFrame with columns:
                     timestamp, symbol, funding_rate
        """
        if not context or "funding_rates" not in context:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.HOLD,
                asset_id=None,
                confidence=0.0,
                reasoning="No funding rate data available",
            )

        funding_rates = context["funding_rates"]
        rates_by_asset = self._get_latest_rates(funding_rates, calc_date)

        if not rates_by_asset:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.HOLD,
                asset_id=None,
                confidence=0.0,
                reasoning="No funding rate data for target assets",
            )

        # Find best carry opportunity
        best_asset = None
        best_rate = 0.0
        for asset_id, rate in rates_by_asset.items():
            if rate > best_rate:
                best_rate = rate
                best_asset = asset_id

        annualized = best_rate * 3 * 365  # 3 funding periods per day * 365 days
        meta = {
            "rates": {k.value: v for k, v in rates_by_asset.items()},
            "best_annualized": round(annualized * 100, 1),
        }

        # Entry: funding rate above threshold
        if best_asset and best_rate >= self.entry_rate:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.BUY,
                asset_id=best_asset,
                confidence=min(0.5 + annualized, 1.0),
                reasoning=(
                    f"Open carry on {best_asset.value}: funding rate {best_rate:.4%}/8h "
                    f"({annualized:.1%} annualized)"
                ),
                metadata=meta,
            )

        # Exit: all rates below exit threshold
        all_below_exit = all(r <= self.exit_rate for r in rates_by_asset.values())
        if all_below_exit and current_holding:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.SELL,
                asset_id=CryptoAsset.USDT,
                confidence=0.8,
                reasoning=(
                    f"Close carry: all funding rates below exit threshold "
                    f"({self.exit_rate:.4%}). Rates: {meta['rates']}"
                ),
                metadata=meta,
            )

        # Hold if we're in a carry and rates are still positive
        if current_holding and current_holding in rates_by_asset:
            current_rate = rates_by_asset[current_holding]
            if current_rate > self.exit_rate:
                return StrategySignal(
                    strategy_name=self.name,
                    date=calc_date,
                    action=SignalAction.HOLD,
                    asset_id=current_holding,
                    confidence=0.6,
                    reasoning=(
                        f"Hold carry on {current_holding.value}: "
                        f"rate {current_rate:.4%}/8h still above exit threshold"
                    ),
                    metadata=meta,
                )

        return StrategySignal(
            strategy_name=self.name,
            date=calc_date,
            action=SignalAction.HOLD,
            asset_id=current_holding,
            confidence=0.3,
            reasoning=f"No carry opportunity. Best rate: {best_rate:.4%}/8h",
            metadata=meta,
        )

    def _get_latest_rates(
        self, funding_rates: pd.DataFrame, calc_date: date,
    ) -> dict[CryptoAsset, float]:
        """Get the most recent funding rate for each carry asset."""
        rates = {}
        calc_ts = pd.Timestamp(calc_date)

        for asset_id in self.assets:
            asset = ASSET_REGISTRY[asset_id]
            if not asset.perp_symbol:
                continue

            symbol_rates = funding_rates[
                funding_rates["symbol"] == asset.perp_symbol
            ].copy()
            if symbol_rates.empty:
                continue

            # Get latest rate on or before calc_date
            available = symbol_rates[symbol_rates["timestamp"] <= calc_ts]
            if available.empty:
                continue

            latest = available.sort_values("timestamp").iloc[-1]
            rates[asset_id] = float(latest["funding_rate"])

        return rates

    def get_rebalance_dates(self, start_date: date, end_date: date) -> list[date]:
        """Check funding rates daily (funding settles every 8h but we check daily)."""
        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += timedelta(days=1)
        return dates
