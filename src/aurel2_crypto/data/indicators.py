"""Technical indicators for crypto trading."""

from datetime import date

import numpy as np
import pandas as pd


def calculate_rsi(
    prices: pd.DataFrame,
    symbol: str,
    period: int = 14,
    calc_date: date | None = None,
) -> float | None:
    """Calculate RSI for a symbol.

    Args:
        prices: DataFrame with 'timestamp', 'close', 'symbol' columns
        symbol: Trading pair
        period: RSI period (default 14)
        calc_date: Date to calculate RSI for (uses latest if None)

    Returns:
        RSI value (0-100), or None if insufficient data.
    """
    asset_prices = prices[prices["symbol"] == symbol].copy()
    if len(asset_prices) < period + 1:
        return None

    asset_prices = asset_prices.sort_values("timestamp")

    if calc_date:
        asset_prices["date"] = pd.to_datetime(asset_prices["timestamp"]).dt.date
        asset_prices = asset_prices[asset_prices["date"] <= calc_date]

    closes = asset_prices["close"].values
    deltas = np.diff(closes)

    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_zscore(
    series: pd.Series,
    window: int = 60,
) -> float | None:
    """Calculate z-score of the latest value in a rolling window.

    Used for pairs trading: measures how far the current ratio
    deviates from its rolling mean.

    Args:
        series: Price ratio series
        window: Rolling window size (default 60 days)

    Returns:
        Z-score, or None if insufficient data.
    """
    if len(series) < window:
        return None

    rolling_mean = series.rolling(window).mean()
    rolling_std = series.rolling(window).std()

    if rolling_std.iloc[-1] == 0:
        return 0.0

    return float((series.iloc[-1] - rolling_mean.iloc[-1]) / rolling_std.iloc[-1])


def calculate_price_ratio(
    prices: pd.DataFrame,
    symbol_a: str,
    symbol_b: str,
) -> pd.Series:
    """Calculate price ratio between two assets (A/B).

    Used for pairs trading between BTC and ETH.
    """
    a = prices[prices["symbol"] == symbol_a].copy()
    b = prices[prices["symbol"] == symbol_b].copy()

    a["date"] = pd.to_datetime(a["timestamp"]).dt.date
    b["date"] = pd.to_datetime(b["timestamp"]).dt.date

    a = a.drop_duplicates("date", keep="last").set_index("date")["close"]
    b = b.drop_duplicates("date", keep="last").set_index("date")["close"]

    common = a.index.intersection(b.index)
    return a.loc[common] / b.loc[common]
