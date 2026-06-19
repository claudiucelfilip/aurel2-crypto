"""Configuration settings for Aurel2-Crypto."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Binance credentials
    binance_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("AUREL2_CRYPTO_BINANCE_API_KEY", "BINANCE_API_KEY"),
    )
    binance_api_secret: str = Field(
        default="",
        validation_alias=AliasChoices("AUREL2_CRYPTO_BINANCE_API_SECRET", "BINANCE_API_SECRET"),
    )
    binance_testnet: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUREL2_CRYPTO_BINANCE_TESTNET", "BINANCE_TESTNET"),
    )

    # Trading
    trading_mode: str = Field(
        default="paper",
        validation_alias=AliasChoices("AUREL2_CRYPTO_TRADING_MODE", "TRADING_MODE"),
    )  # "paper" or "live"
    initial_capital: float = Field(
        default=10000.0,
        validation_alias=AliasChoices("AUREL2_CRYPTO_INITIAL_CAPITAL", "INITIAL_CAPITAL"),
    )

    # Strategy allocation
    carry_allocation: float = Field(
        default=0.0,
        validation_alias=AliasChoices("AUREL2_CRYPTO_CARRY_ALLOCATION", "CARRY_ALLOCATION"),
    )
    momentum_allocation: float = Field(
        default=1.0,
        validation_alias=AliasChoices("AUREL2_CRYPTO_MOMENTUM_ALLOCATION", "MOMENTUM_ALLOCATION"),
    )

    # Momentum strategy
    momentum_lookback_days: int = Field(
        default=28,
        validation_alias=AliasChoices("AUREL2_CRYPTO_MOMENTUM_LOOKBACK_DAYS", "MOMENTUM_LOOKBACK_DAYS"),
    )
    momentum_rebalance_days: int = Field(
        default=7,
        validation_alias=AliasChoices("AUREL2_CRYPTO_MOMENTUM_REBALANCE_DAYS", "MOMENTUM_REBALANCE_DAYS"),
    )
    trailing_stop_pct: float = Field(
        default=0.10,
        validation_alias=AliasChoices("AUREL2_CRYPTO_TRAILING_STOP_PCT", "TRAILING_STOP_PCT"),
    )  # 10% trailing stop-loss

    # Carry strategy
    carry_min_funding_rate: float = Field(
        default=0.0001,
        validation_alias=AliasChoices("AUREL2_CRYPTO_CARRY_MIN_FUNDING_RATE", "CARRY_MIN_FUNDING_RATE"),
    )  # 0.01% per 8h = ~10% annualized
    carry_max_position_pct: float = Field(
        default=0.20,
        validation_alias=AliasChoices("AUREL2_CRYPTO_CARRY_MAX_POSITION_PCT", "CARRY_MAX_POSITION_PCT"),
    )  # Max 20% of portfolio per carry position

    # Pairs trading
    pairs_zscore_entry: float = Field(
        default=2.0,
        validation_alias=AliasChoices("AUREL2_CRYPTO_PAIRS_ZSCORE_ENTRY", "PAIRS_ZSCORE_ENTRY"),
    )
    pairs_zscore_exit: float = Field(
        default=0.5,
        validation_alias=AliasChoices("AUREL2_CRYPTO_PAIRS_ZSCORE_EXIT", "PAIRS_ZSCORE_EXIT"),
    )
    pairs_lookback_days: int = Field(
        default=60,
        validation_alias=AliasChoices("AUREL2_CRYPTO_PAIRS_LOOKBACK_DAYS", "PAIRS_LOOKBACK_DAYS"),
    )

    # Notifications
    ntfy_topic: str = Field(
        default="",
        validation_alias=AliasChoices("AUREL2_CRYPTO_NTFY_TOPIC", "NTFY_TOPIC"),
    )

    model_config = {"env_prefix": "AUREL2_CRYPTO_"}
