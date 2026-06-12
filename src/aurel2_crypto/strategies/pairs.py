"""BTC/ETH pairs trading strategy.

Exploits the cointegration between BTC and ETH prices.
When the BTC/ETH ratio diverges from its rolling mean, bet on reversion.

Mechanism:
  1. Track BTC/ETH price ratio daily
  2. Calculate z-score of ratio vs rolling mean
  3. When z-score > entry: ratio is high → BTC overvalued relative to ETH
     → Short BTC perp, Long ETH perp (or spot)
  4. When z-score < -entry: ratio is low → ETH overvalued relative to BTC
     → Long BTC perp (or spot), Short ETH perp
  5. Close when z-score reverts to exit threshold

Based on research:
- Academic: 16.3% annualized return, Sharpe 2.45
- BTC-ETH cointegration is consistent across multiple studies
- Risk: cointegration breaks during altcoin seasons

References:
- Statistical Arbitrage Using Cointegration in Crypto (IJSRA, 2026)
- EUR Thesis on Pairs Trading in Cryptocurrency Markets
"""

from datetime import date, timedelta

import pandas as pd

from aurel2_crypto.core.models import CryptoAsset, SignalAction, StrategySignal
from aurel2_crypto.data.indicators import calculate_price_ratio, calculate_zscore
from aurel2_crypto.strategies.base import BaseStrategy


class PairsTradingStrategy(BaseStrategy):
    """BTC/ETH pairs trading based on ratio mean reversion.

    Positions:
    - z > entry_threshold: short BTC, long ETH (ratio will fall)
    - z < -entry_threshold: long BTC, short ETH (ratio will rise)
    - |z| < exit_threshold: close position (ratio reverted)
    """

    name = "pairs"

    def __init__(
        self,
        zscore_entry: float = 2.0,
        zscore_exit: float = 0.5,
        lookback_days: int = 60,
        max_hold_days: int = 30,
        asset_a: CryptoAsset = CryptoAsset.BTC,
        asset_b: CryptoAsset = CryptoAsset.ETH,
    ):
        self.zscore_entry = zscore_entry
        self.zscore_exit = zscore_exit
        self.lookback_days = lookback_days
        self.max_hold_days = max_hold_days
        self.asset_a = asset_a  # Numerator in ratio
        self.asset_b = asset_b  # Denominator in ratio

    def generate_signal(
        self,
        prices: pd.DataFrame,
        calc_date: date,
        current_holding: CryptoAsset | None,
        context: dict | None = None,
    ) -> StrategySignal:
        """Generate pairs trading signal.

        context should contain:
            'position_side': 'long_a_short_b' or 'short_a_long_b' or None
            'entry_date': date when position was opened (for max hold check)
        """
        from aurel2_crypto.core.assets import ASSET_REGISTRY

        symbol_a = ASSET_REGISTRY[self.asset_a].symbol
        symbol_b = ASSET_REGISTRY[self.asset_b].symbol

        ratio = calculate_price_ratio(prices, symbol_a, symbol_b)

        if len(ratio) < self.lookback_days:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.HOLD,
                asset_id=None,
                confidence=0.0,
                reasoning=f"Insufficient data: {len(ratio)} days, need {self.lookback_days}",
            )

        # Filter to data up to calc_date
        ratio = ratio[ratio.index <= calc_date]
        if len(ratio) < self.lookback_days:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.HOLD,
                asset_id=None,
                confidence=0.0,
                reasoning="Insufficient ratio data up to calc_date",
            )

        zscore = calculate_zscore(ratio, window=self.lookback_days)
        if zscore is None:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.HOLD,
                asset_id=None,
                confidence=0.0,
                reasoning="Could not calculate z-score",
            )

        current_ratio = float(ratio.iloc[-1])
        position_side = (context or {}).get("position_side")
        entry_date = (context or {}).get("entry_date")

        meta = {
            "zscore": round(zscore, 2),
            "ratio": round(current_ratio, 4),
            "position_side": position_side,
        }

        # Check max hold duration
        if position_side and entry_date:
            days_held = (calc_date - entry_date).days
            if days_held >= self.max_hold_days:
                return StrategySignal(
                    strategy_name=self.name,
                    date=calc_date,
                    action=SignalAction.COVER,
                    asset_id=None,
                    confidence=0.7,
                    reasoning=(
                        f"Max hold reached ({days_held}d). z={zscore:.2f}, "
                        f"ratio={current_ratio:.4f}. Force closing."
                    ),
                    metadata=meta,
                )

        # Currently in a position — check exit
        if position_side:
            if position_side == "long_a_short_b" and zscore >= -self.zscore_exit:
                return StrategySignal(
                    strategy_name=self.name,
                    date=calc_date,
                    action=SignalAction.COVER,
                    asset_id=None,
                    confidence=0.8,
                    reasoning=(
                        f"Close long {self.asset_a.value}/short {self.asset_b.value}: "
                        f"z={zscore:.2f} reverted above -{self.zscore_exit}"
                    ),
                    metadata=meta,
                )
            elif position_side == "short_a_long_b" and zscore <= self.zscore_exit:
                return StrategySignal(
                    strategy_name=self.name,
                    date=calc_date,
                    action=SignalAction.COVER,
                    asset_id=None,
                    confidence=0.8,
                    reasoning=(
                        f"Close short {self.asset_a.value}/long {self.asset_b.value}: "
                        f"z={zscore:.2f} reverted below {self.zscore_exit}"
                    ),
                    metadata=meta,
                )

            # Hold current position
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.HOLD,
                asset_id=None,
                confidence=0.5,
                reasoning=f"Hold {position_side}: z={zscore:.2f}",
                metadata=meta,
            )

        # No position — check entry signals
        if zscore > self.zscore_entry:
            # Ratio too high: BTC overvalued vs ETH
            # → Short BTC, Long ETH (bet ratio falls)
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.SHORT,
                asset_id=self.asset_a,  # Short BTC
                confidence=min(0.5 + abs(zscore) * 0.1, 1.0),
                reasoning=(
                    f"Ratio high: z={zscore:.2f} > {self.zscore_entry}. "
                    f"Short {self.asset_a.value}, long {self.asset_b.value}. "
                    f"Ratio={current_ratio:.4f}"
                ),
                metadata={**meta, "position_side": "short_a_long_b"},
            )

        if zscore < -self.zscore_entry:
            # Ratio too low: ETH overvalued vs BTC
            # → Long BTC, Short ETH (bet ratio rises)
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.BUY,
                asset_id=self.asset_a,  # Long BTC
                confidence=min(0.5 + abs(zscore) * 0.1, 1.0),
                reasoning=(
                    f"Ratio low: z={zscore:.2f} < -{self.zscore_entry}. "
                    f"Long {self.asset_a.value}, short {self.asset_b.value}. "
                    f"Ratio={current_ratio:.4f}"
                ),
                metadata={**meta, "position_side": "long_a_short_b"},
            )

        # No signal — z-score within neutral zone
        return StrategySignal(
            strategy_name=self.name,
            date=calc_date,
            action=SignalAction.HOLD,
            asset_id=None,
            confidence=0.3,
            reasoning=f"Neutral zone: z={zscore:.2f}, entry threshold ±{self.zscore_entry}",
            metadata=meta,
        )

    def get_rebalance_dates(self, start_date: date, end_date: date) -> list[date]:
        """Check daily for pairs signals."""
        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += timedelta(days=1)
        return dates
