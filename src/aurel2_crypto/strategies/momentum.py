"""Short-term momentum strategy for crypto.

Based on research findings:
- 2-4 week lookback is optimal for crypto (vs 12 months for equities)
- Weekly rebalancing (vs monthly for equities)
- Absolute momentum filter: go to cash when all assets trend down
- Relative momentum: pick the strongest performer

References:
- Time-series momentum in crypto: Sharpe ~1.5 with 28-day lookback
- Risk-managed TSMOM: Sharpe 1.42 (ScienceDirect 2025)
"""

from datetime import date, timedelta

import pandas as pd

from aurel2_crypto.core.assets import MOMENTUM_ASSETS, ASSET_REGISTRY
from aurel2_crypto.core.models import CryptoAsset, SignalAction, StrategySignal
from aurel2_crypto.data.momentum import calculate_momentum_scores, rank_by_momentum
from aurel2_crypto.strategies.base import BaseStrategy


class ShortTermMomentumStrategy(BaseStrategy):
    """Short-term momentum: absolute filter + relative ranking.

    1. Calculate momentum for all 5 crypto assets over lookback period
    2. Absolute momentum check: if ALL assets have negative momentum → go to USDT
    3. Relative momentum: buy the asset with highest momentum
    4. Switch threshold: only switch if new winner beats current by threshold
    """

    name = "momentum"

    def __init__(
        self,
        lookback_days: int = 28,
        rebalance_days: int = 7,
        switch_threshold: float = 0.03,
        assets: list[CryptoAsset] | None = None,
    ):
        self.lookback_days = lookback_days
        self.rebalance_days = rebalance_days
        self.switch_threshold = switch_threshold
        self.assets = assets or MOMENTUM_ASSETS

    def generate_signal(
        self,
        prices: pd.DataFrame,
        calc_date: date,
        current_holding: CryptoAsset | None,
        context: dict | None = None,
    ) -> StrategySignal:
        scores = calculate_momentum_scores(
            prices, self.assets, calc_date, self.lookback_days,
        )

        if not scores:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.HOLD,
                asset_id=current_holding,
                confidence=0.0,
                reasoning="Insufficient data for momentum calculation",
            )

        ranked = rank_by_momentum(scores)
        best_id, best_score = ranked[0]
        all_negative = all(s.momentum <= 0 for s in scores.values())

        # Absolute momentum filter: all negative → go to cash
        if all_negative:
            if current_holding and current_holding != CryptoAsset.USDT:
                return StrategySignal(
                    strategy_name=self.name,
                    date=calc_date,
                    action=SignalAction.SELL,
                    asset_id=CryptoAsset.USDT,
                    confidence=0.9,
                    reasoning=(
                        f"All assets have negative momentum → go to USDT. "
                        f"Best: {best_id.value} at {best_score.momentum:+.1%}"
                    ),
                    metadata={"scores": {k.value: v.momentum for k, v in scores.items()}},
                )
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.HOLD,
                asset_id=CryptoAsset.USDT,
                confidence=0.8,
                reasoning="All negative momentum, staying in USDT",
                metadata={"scores": {k.value: v.momentum for k, v in scores.items()}},
            )

        # No current holding → buy the best
        if current_holding is None or current_holding == CryptoAsset.USDT:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.BUY,
                asset_id=best_id,
                confidence=min(0.5 + best_score.momentum, 1.0),
                reasoning=(
                    f"Enter {best_id.value}: momentum {best_score.momentum:+.1%} "
                    f"over {self.lookback_days}d"
                ),
                metadata={"scores": {k.value: v.momentum for k, v in scores.items()}},
            )

        # Currently holding something — check if we should switch
        current_score = scores.get(current_holding)
        if current_score is None:
            # Current holding not in scores (data gap), switch to best
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.BUY,
                asset_id=best_id,
                confidence=0.6,
                reasoning=f"Current holding {current_holding.value} has no data, switching to {best_id.value}",
                metadata={"scores": {k.value: v.momentum for k, v in scores.items()}},
            )

        # Current holding has negative momentum → exit
        if current_score.momentum < 0:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.BUY,
                asset_id=best_id,
                confidence=0.8,
                reasoning=(
                    f"Current {current_holding.value} momentum is negative "
                    f"({current_score.momentum:+.1%}), switching to {best_id.value} "
                    f"({best_score.momentum:+.1%})"
                ),
                metadata={"scores": {k.value: v.momentum for k, v in scores.items()}},
            )

        # Best beats current by threshold → switch
        momentum_advantage = best_score.momentum - current_score.momentum
        if best_id != current_holding and momentum_advantage > self.switch_threshold:
            return StrategySignal(
                strategy_name=self.name,
                date=calc_date,
                action=SignalAction.BUY,
                asset_id=best_id,
                confidence=min(0.5 + momentum_advantage, 1.0),
                reasoning=(
                    f"Switch {current_holding.value} ({current_score.momentum:+.1%}) → "
                    f"{best_id.value} ({best_score.momentum:+.1%}), "
                    f"advantage: {momentum_advantage:+.1%} > {self.switch_threshold:.0%} threshold"
                ),
                metadata={"scores": {k.value: v.momentum for k, v in scores.items()}},
            )

        # Hold current position
        return StrategySignal(
            strategy_name=self.name,
            date=calc_date,
            action=SignalAction.HOLD,
            asset_id=current_holding,
            confidence=0.7,
            reasoning=(
                f"Hold {current_holding.value} ({current_score.momentum:+.1%}). "
                f"Best: {best_id.value} ({best_score.momentum:+.1%}), "
                f"advantage {momentum_advantage:+.1%} < {self.switch_threshold:.0%}"
            ),
            metadata={"scores": {k.value: v.momentum for k, v in scores.items()}},
        )

    def get_rebalance_dates(self, start_date: date, end_date: date) -> list[date]:
        """Generate weekly rebalance dates (every Monday)."""
        dates = []
        current = start_date
        # Advance to first Monday
        while current.weekday() != 0:
            current += timedelta(days=1)
        while current <= end_date:
            dates.append(current)
            current += timedelta(days=self.rebalance_days)
        return dates
