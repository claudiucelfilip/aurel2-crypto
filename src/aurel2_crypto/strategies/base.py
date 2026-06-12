"""Base classes for crypto trading strategies."""

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import pandas as pd

from aurel2_crypto.core.models import CryptoAsset, StrategySignal


class BaseStrategy(ABC):
    """Abstract base class for all crypto trading strategies.

    Subclasses must define a `name` class attribute and implement
    the abstract methods.
    """

    name: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        has_name = "name" in cls.__dict__ and isinstance(cls.__dict__["name"], str)
        if not has_name:
            if not getattr(cls, "__abstractmethods__", set()):
                raise TypeError(
                    f"Strategy class {cls.__name__} must define a 'name' class attribute"
                )

    @abstractmethod
    def generate_signal(
        self,
        prices: pd.DataFrame,
        calc_date: date,
        current_holding: CryptoAsset | None,
        context: dict[str, Any] | None = None,
    ) -> StrategySignal:
        """Generate a trading signal based on market data.

        Args:
            prices: DataFrame with columns: timestamp, open, high, low, close, volume, symbol
            calc_date: The date to generate the signal for.
            current_holding: Currently held asset, or None.
            context: Optional extra data (funding rates, etc.)

        Returns:
            A StrategySignal with the recommended action.
        """
        ...

    @abstractmethod
    def get_rebalance_dates(
        self,
        start_date: date,
        end_date: date,
    ) -> list[date]:
        """Get dates when this strategy should be evaluated.

        Returns:
            List of dates for signal generation.
        """
        ...
