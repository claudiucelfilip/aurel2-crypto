"""Base broker interface for crypto exchanges."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import structlog

logger = structlog.get_logger()


@dataclass
class BrokerPosition:
    """A position held at the exchange."""
    symbol: str
    shares: float
    avg_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float = 0.0
    side: str = "long"  # "long" or "short"
    leverage: float = 1.0


@dataclass
class BrokerOrder:
    """An order to be placed."""
    symbol: str
    action: str  # "BUY" or "SELL"
    quantity: float
    order_type: str = "market"
    limit_price: Optional[float] = None
    side: str = "long"  # "long" or "short" (for futures)


@dataclass
class OrderResult:
    """Result of an order execution."""
    order_id: str
    symbol: str
    action: str
    quantity: float
    filled_quantity: float
    avg_fill_price: float
    status: str  # "FILLED", "PARTIAL", "REJECTED", "PENDING"
    fee: float = 0.0
    message: str = ""


@dataclass
class AccountSummary:
    """Exchange account summary."""
    total_value: float
    cash_balance: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    margin_used: float = 0.0


@dataclass
class FundingRateInfo:
    """Current funding rate info for a perp."""
    symbol: str
    rate: float
    next_funding_time: Optional[str] = None


class BaseBroker(ABC):
    """Abstract base class for crypto exchange integrations."""

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the exchange. Returns True if successful."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the exchange."""
        ...

    @abstractmethod
    async def get_account_summary(self) -> AccountSummary:
        """Get account summary."""
        ...

    @abstractmethod
    async def get_positions(self) -> list[BrokerPosition]:
        """Get all current positions (spot + futures)."""
        ...

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        """Get a specific position by symbol."""
        ...

    @abstractmethod
    async def place_order(self, order: BrokerOrder) -> OrderResult:
        """Place an order. Returns the result."""
        ...

    @abstractmethod
    async def get_market_price(self, symbol: str) -> Optional[float]:
        """Get current market price for a symbol."""
        ...

    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> Optional[FundingRateInfo]:
        """Get current funding rate for a perpetual contract."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to the exchange."""
        ...

    async def execute_switch(
        self,
        sell_symbol: str,
        buy_symbol: str,
        sell_quantity: Optional[float] = None,
    ) -> tuple[OrderResult, OrderResult]:
        """Execute a position switch: sell one asset and buy another.

        Crypto settles instantly, so no settlement guard needed.
        """
        if sell_quantity is None:
            position = await self.get_position(sell_symbol)
            if position is None:
                raise ValueError(f"No position found for {sell_symbol}")
            sell_quantity = position.shares

        sell_order = BrokerOrder(
            symbol=sell_symbol, action="SELL", quantity=sell_quantity,
        )
        sell_result = await self.place_order(sell_order)

        if sell_result.status != "FILLED":
            logger.error("sell_order_not_filled", result=sell_result)
            raise RuntimeError(f"Sell order not filled: {sell_result.message}")

        proceeds = sell_result.filled_quantity * sell_result.avg_fill_price
        fee_reserve = proceeds * 0.001  # Reserve 0.1% for buy fees
        buy_funds = proceeds - fee_reserve

        buy_price = await self.get_market_price(buy_symbol)
        if buy_price is None:
            raise RuntimeError(f"Could not get price for {buy_symbol}")

        buy_quantity = buy_funds / buy_price

        buy_order = BrokerOrder(
            symbol=buy_symbol, action="BUY", quantity=buy_quantity,
        )
        buy_result = await self.place_order(buy_order)

        return sell_result, buy_result
