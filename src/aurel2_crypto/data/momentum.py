"""Momentum calculations for crypto assets."""

from datetime import date, timedelta

import pandas as pd

from aurel2_crypto.core.assets import ASSET_REGISTRY
from aurel2_crypto.core.models import CryptoAsset, MomentumScore


def calculate_momentum(
    prices: pd.DataFrame,
    symbol: str,
    calc_date: date,
    lookback_days: int = 28,
) -> float | None:
    """Calculate price momentum over a lookback period.

    Args:
        prices: DataFrame with 'timestamp', 'close', 'symbol' columns
        symbol: Trading pair (e.g., "BTC/USDT")
        calc_date: Date to calculate momentum for
        lookback_days: Number of days to look back (default: 28 = ~4 weeks)

    Returns:
        Momentum as a decimal (e.g., 0.15 = 15% gain), or None if insufficient data.
    """
    asset_prices = prices[prices["symbol"] == symbol].copy()
    if asset_prices.empty:
        return None

    asset_prices = asset_prices.sort_values("timestamp")
    asset_prices["date"] = pd.to_datetime(asset_prices["timestamp"]).dt.date

    calc_ts = pd.Timestamp(calc_date)
    lookback_ts = calc_ts - pd.Timedelta(days=lookback_days)

    current = asset_prices[asset_prices["date"] <= calc_date]
    past = asset_prices[asset_prices["date"] <= lookback_ts.date()]

    if current.empty or past.empty:
        return None

    current_price = float(current.iloc[-1]["close"])
    past_price = float(past.iloc[-1]["close"])

    if past_price <= 0:
        return None

    return (current_price - past_price) / past_price


def calculate_momentum_scores(
    prices: pd.DataFrame,
    asset_ids: list[CryptoAsset],
    calc_date: date,
    lookback_days: int = 28,
) -> dict[CryptoAsset, MomentumScore]:
    """Calculate momentum scores for multiple assets.

    Returns:
        Dict mapping CryptoAsset to MomentumScore, only for assets with valid data.
    """
    scores = {}

    for asset_id in asset_ids:
        asset = ASSET_REGISTRY[asset_id]
        momentum = calculate_momentum(prices, asset.symbol, calc_date, lookback_days)

        if momentum is not None:
            asset_prices = prices[prices["symbol"] == asset.symbol].copy()
            asset_prices["date"] = pd.to_datetime(asset_prices["timestamp"]).dt.date

            current = asset_prices[asset_prices["date"] <= calc_date]
            lookback_date = calc_date - timedelta(days=lookback_days)
            past = asset_prices[asset_prices["date"] <= lookback_date]

            scores[asset_id] = MomentumScore(
                asset=asset,
                date=calc_date,
                momentum=momentum,
                price=float(current.iloc[-1]["close"]),
                price_lookback=float(past.iloc[-1]["close"]),
                lookback_days=lookback_days,
            )

    return scores


def rank_by_momentum(
    scores: dict[CryptoAsset, MomentumScore],
) -> list[tuple[CryptoAsset, MomentumScore]]:
    """Rank assets by momentum, highest first."""
    return sorted(scores.items(), key=lambda x: x[1].momentum, reverse=True)
