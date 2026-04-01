"""Binance historical data provider via ccxt."""

from datetime import datetime, timedelta

import ccxt
import pandas as pd
import structlog

logger = structlog.get_logger()


class BinanceDataProvider:
    """Fetches historical OHLCV and funding rate data from Binance."""

    def __init__(self):
        self._exchange = ccxt.binance({"enableRateLimit": True})
        self._futures = ccxt.binanceusdm({"enableRateLimit": True})
        self._loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            self._exchange.load_markets()
            self._futures.load_markets()
            self._loaded = True

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Fetch OHLCV candle data.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            timeframe: Candle interval ("1h", "4h", "1d", "1w")
            start_date: Start datetime (fetches from this point)
            end_date: End datetime
            limit: Max candles per request (Binance max: 1000)

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        self._ensure_loaded()

        since = int(start_date.timestamp() * 1000) if start_date else None
        all_candles = []

        while True:
            candles = self._exchange.fetch_ohlcv(
                symbol, timeframe, since=since, limit=limit,
            )
            if not candles:
                break

            all_candles.extend(candles)

            if end_date:
                end_ms = int(end_date.timestamp() * 1000)
                if candles[-1][0] >= end_ms:
                    break

            if len(candles) < limit:
                break

            since = candles[-1][0] + 1

        if not all_candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df["symbol"] = symbol

        if end_date:
            df = df[df["timestamp"] <= pd.Timestamp(end_date)]

        return df.reset_index(drop=True)

    def get_multi_ohlcv(
        self,
        symbols: list[str],
        timeframe: str = "1d",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV for multiple symbols and combine."""
        frames = []
        for symbol in symbols:
            logger.info("fetching_ohlcv", symbol=symbol)
            df = self.get_ohlcv(symbol, timeframe, start_date, end_date)
            if not df.empty:
                frames.append(df)

        if not frames:
            return pd.DataFrame()

        return pd.concat(frames, ignore_index=True)

    def get_funding_rates(
        self,
        symbol: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Fetch historical funding rates for a perpetual contract.

        Args:
            symbol: Perp symbol (e.g., "BTC/USDT:USDT")
            start_date: Start datetime
            end_date: End datetime

        Returns:
            DataFrame with columns: timestamp, symbol, funding_rate
        """
        self._ensure_loaded()

        since = int(start_date.timestamp() * 1000) if start_date else None
        all_rates = []

        while True:
            rates = self._futures.fetch_funding_rate_history(
                symbol, since=since, limit=limit,
            )
            if not rates:
                break

            all_rates.extend(rates)

            if len(rates) < limit:
                break

            last_ts = rates[-1].get("timestamp", 0)
            since = last_ts + 1

            if end_date:
                end_ms = int(end_date.timestamp() * 1000)
                if last_ts >= end_ms:
                    break

        if not all_rates:
            return pd.DataFrame(columns=["timestamp", "symbol", "funding_rate"])

        df = pd.DataFrame([
            {
                "timestamp": pd.Timestamp(r["datetime"]),
                "symbol": symbol,
                "funding_rate": float(r.get("fundingRate", 0)),
            }
            for r in all_rates
        ])

        if end_date:
            df = df[df["timestamp"] <= pd.Timestamp(end_date)]

        return df.reset_index(drop=True)

    def get_multi_funding_rates(
        self,
        symbols: list[str],
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch funding rates for multiple perp symbols."""
        frames = []
        for symbol in symbols:
            logger.info("fetching_funding_rates", symbol=symbol)
            df = self.get_funding_rates(symbol, start_date, end_date)
            if not df.empty:
                frames.append(df)

        if not frames:
            return pd.DataFrame()

        return pd.concat(frames, ignore_index=True)
