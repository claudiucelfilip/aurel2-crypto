"""Configuration settings for Aurel2-Crypto."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Binance credentials
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True

    # Trading
    trading_mode: str = "paper"  # "paper" or "live"
    initial_capital: float = 10000.0

    # Strategy allocation (must sum to 1.0)
    carry_allocation: float = 0.10
    momentum_allocation: float = 0.90

    # Momentum strategy
    momentum_lookback_days: int = 28
    momentum_rebalance_days: int = 7

    # Carry strategy
    carry_min_funding_rate: float = 0.0001  # 0.01% per 8h = ~10% annualized
    carry_max_position_pct: float = 0.20    # Max 20% of portfolio per carry position

    # Pairs trading
    pairs_zscore_entry: float = 2.0
    pairs_zscore_exit: float = 0.5
    pairs_lookback_days: int = 60

    # Notifications
    ntfy_topic: str = ""

    model_config = {"env_prefix": "AUREL2_CRYPTO_"}
