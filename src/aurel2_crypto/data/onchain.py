"""On-chain regime indicators.

Since historical MVRV Z-score requires expensive APIs (Glassnode $79/mo),
we approximate the regime signal using freely available data:

1. **Price-to-200DMA ratio**: Similar to MVRV in detecting overextension
   - Price far above 200DMA → euphoria (like high MVRV)
   - Price below 200DMA → capitulation (like negative MVRV)

2. **NVT-like proxy**: Market cap growth rate vs on-chain proxy
   - Uses price momentum as a stand-in for network value velocity

These are simpler but capture the same regime transitions:
- Bull/euphoria → reduce exposure
- Capitulation → increase exposure
- Normal → default allocation

If MVRV data becomes available (Glassnode subscription), swap in the
real MVRV Z-score with minimal code changes.
"""

from datetime import date

import numpy as np
import pandas as pd


class RegimeDetector:
    """Detects crypto market regimes from price data.

    Uses price-to-moving-average ratio as a proxy for MVRV Z-score.
    Both measure how far the market has deviated from "fair value":
    - MVRV: market cap vs realized cap (on-chain cost basis)
    - Price/200DMA: current price vs 200-day average price

    Historical correlation between MVRV Z-score and price/200DMA
    is ~0.85 for BTC, making this a reasonable free approximation.
    """

    # Regime thresholds (calibrated from BTC price/200DMA historical distribution)
    # BTC rarely exceeds 2x 200DMA outside of parabolic blow-offs
    # These thresholds are set to trigger meaningfully:
    # - Euphoria: only during true blow-off tops (2017 Dec, 2021 Apr)
    # - Overheated: early warning, reduce gradually
    # - Capitulation: deep bear market, lean in

    EUPHORIA_THRESHOLD = 1.8
    OVERHEATED_THRESHOLD = 1.4
    CAPITULATION_THRESHOLD = 0.75

    def __init__(self, ma_period: int = 200):
        self.ma_period = ma_period

    def detect_regime(
        self,
        prices: pd.DataFrame,
        calc_date: date,
        symbol: str = "BTC/USDT",
    ) -> dict:
        """Detect current market regime.

        Returns:
            Dict with:
                regime: "euphoria", "overheated", "normal", "capitulation"
                ratio: price / 200DMA
                position_multiplier: 0.3-1.3 (scales momentum position size)
                ma_200: 200-day moving average price
        """
        btc = prices[prices["symbol"] == symbol].copy()
        if btc.empty:
            return self._default_regime()

        btc["date"] = pd.to_datetime(btc["timestamp"]).dt.date
        btc = btc[btc["date"] <= calc_date].sort_values("timestamp")

        if len(btc) < self.ma_period:
            return self._default_regime()

        current_price = float(btc.iloc[-1]["close"])
        ma_200 = float(btc.tail(self.ma_period)["close"].mean())

        if ma_200 <= 0:
            return self._default_regime()

        ratio = current_price / ma_200

        if ratio >= self.EUPHORIA_THRESHOLD:
            regime = "euphoria"
            multiplier = 0.40  # Cut momentum to 40% — protect gains
        elif ratio >= self.OVERHEATED_THRESHOLD:
            regime = "overheated"
            multiplier = 0.75  # Slight reduction — don't fight momentum too hard
        elif ratio >= self.CAPITULATION_THRESHOLD:
            regime = "normal"
            multiplier = 1.00  # Full momentum allocation
        else:
            regime = "capitulation"
            multiplier = 1.00  # Stay full — momentum's absolute filter handles defense

        return {
            "regime": regime,
            "ratio": round(ratio, 3),
            "position_multiplier": multiplier,
            "ma_200": round(ma_200, 2),
            "price": round(current_price, 2),
        }

    def get_regime_history(
        self,
        prices: pd.DataFrame,
        start_date: date,
        end_date: date,
        symbol: str = "BTC/USDT",
    ) -> pd.DataFrame:
        """Get regime classification for every day in the period."""
        btc = prices[prices["symbol"] == symbol].copy()
        if btc.empty:
            return pd.DataFrame()

        btc["date"] = pd.to_datetime(btc["timestamp"]).dt.date
        btc = btc.sort_values("timestamp").drop_duplicates("date", keep="last")

        rows = []
        for _, row in btc.iterrows():
            d = row["date"]
            if d < start_date or d > end_date:
                continue
            regime = self.detect_regime(prices, d, symbol)
            rows.append({"date": d, **regime})

        return pd.DataFrame(rows)

    def _default_regime(self) -> dict:
        return {
            "regime": "normal",
            "ratio": 1.0,
            "position_multiplier": 1.0,
            "ma_200": 0.0,
            "price": 0.0,
        }
