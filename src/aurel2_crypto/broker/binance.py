"""Binance exchange broker via ccxt."""

import asyncio
from typing import Optional

import ccxt
import structlog

from aurel2_crypto.broker.base import (
    AccountSummary,
    BaseBroker,
    BrokerOrder,
    BrokerPosition,
    FundingRateInfo,
    OrderResult,
)

logger = structlog.get_logger()


class BinanceBroker(BaseBroker):
    """Binance exchange broker using ccxt.

    Supports both spot and USDT-margined futures (perpetuals).
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._spot: ccxt.binance | None = None
        self._futures: ccxt.binanceusdm | None = None
        self._connected = False

    async def connect(self) -> bool:
        try:
            spot_config = {
                "apiKey": self._api_key,
                "secret": self._api_secret,
                "enableRateLimit": True,
            }
            futures_config = {
                "apiKey": self._api_key,
                "secret": self._api_secret,
                "enableRateLimit": True,
            }

            self._spot = ccxt.binance(spot_config)

            if self._testnet:
                # Binance demo trading uses demo-api.binance.com
                # Remap all API URLs from api.binance.com to demo-api.binance.com
                for key in list(self._spot.urls["api"].keys()):
                    url = self._spot.urls["api"][key]
                    if "api.binance.com" in url:
                        self._spot.urls["api"][key] = url.replace(
                            "api.binance.com", "demo-api.binance.com"
                        )
                # Demo API doesn't support /sapi/ endpoints
                self._spot.has["fetchCurrencies"] = False
                self._spot.has["fetchMarginMarkets"] = False
                self._spot.options["fetchMarkets"] = {"types": ["spot"]}

            if self._testnet:
                # Demo API: manually load spot markets (skip sapi/margin endpoints)
                info = await self._run_sync(
                    lambda: self._spot.publicGetExchangeInfo()
                )
                # Manually populate markets dict so ccxt doesn't call load_markets
                self._spot.markets = {}
                self._spot.markets_by_id = {}
                for s in info.get("symbols", []):
                    sym = s["baseAsset"] + "/" + s["quoteAsset"]
                    market = {
                        "id": s["symbol"], "symbol": sym,
                        "base": s["baseAsset"], "quote": s["quoteAsset"],
                        "baseId": s["baseAsset"], "quoteId": s["quoteAsset"],
                        "active": s["status"] == "TRADING", "type": "spot",
                        "spot": True, "margin": False, "swap": False,
                        "future": False, "option": False,
                        "info": s,
                    }
                    self._spot.markets[sym] = market
                    self._spot.markets_by_id[s["symbol"]] = market

                # Verify auth with account endpoint
                await self._run_sync(
                    lambda: self._spot.privateGetAccount({"timestamp": int(__import__("time").time() * 1000)})
                )

                # Demo is spot-only — no futures
                self._futures = None
                logger.warning("binance_demo_no_futures", msg="Demo API is spot-only. Carry strategy disabled.")
            else:
                self._futures = ccxt.binanceusdm(futures_config)
                await asyncio.get_event_loop().run_in_executor(
                    None, self._futures.load_markets
                )

            self._connected = True
            logger.info(
                "binance_connected",
                testnet=self._testnet,
                spot_markets=len(self._spot.markets),
                futures_markets=len(self._futures.markets) if self._futures else 0,
            )
            return True
        except Exception as e:
            logger.error("binance_connection_failed", error=str(e))
            self._connected = False
            return False

    async def disconnect(self) -> None:
        self._connected = False
        self._spot = None
        self._futures = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _run_sync(self, func, *args):
        """Run a synchronous ccxt call in executor."""
        return asyncio.get_event_loop().run_in_executor(None, func, *args)

    async def get_account_summary(self) -> AccountSummary:
        self._ensure_connected()

        spot_balance = await self._run_sync(self._spot.fetch_balance)
        spot_total = float(spot_balance.get("total", {}).get("USDT", 0))

        futures_total = 0.0
        futures_free = 0.0
        if self._futures:
            futures_balance = await self._run_sync(self._futures.fetch_balance)
            futures_total = float(futures_balance.get("total", {}).get("USDT", 0))
            futures_free = float(futures_balance.get("free", {}).get("USDT", 0))

        return AccountSummary(
            total_value=spot_total + futures_total,
            cash_balance=spot_total + futures_free,
            unrealized_pnl=0.0,
            margin_used=futures_total - futures_free,
        )

    async def get_positions(self) -> list[BrokerPosition]:
        self._ensure_connected()
        positions = []

        # Spot positions
        balance = await self._run_sync(self._spot.fetch_balance)
        for currency, amount in balance.get("total", {}).items():
            if float(amount) > 0 and currency != "USDT":
                symbol = f"{currency}/USDT"
                price = await self.get_market_price(symbol)
                if price:
                    positions.append(BrokerPosition(
                        symbol=symbol,
                        shares=float(amount),
                        avg_cost=0.0,  # Spot doesn't track avg cost
                        market_price=price,
                        market_value=float(amount) * price,
                        unrealized_pnl=0.0,
                        side="long",
                    ))

        # Futures positions (only if futures is available)
        if not self._futures:
            return positions
        futures_positions = await self._run_sync(self._futures.fetch_positions)
        for pos in futures_positions:
            contracts = float(pos.get("contracts", 0))
            if contracts > 0:
                positions.append(BrokerPosition(
                    symbol=pos["symbol"],
                    shares=contracts,
                    avg_cost=float(pos.get("entryPrice", 0)),
                    market_price=float(pos.get("markPrice", 0)),
                    market_value=float(pos.get("notional", 0)),
                    unrealized_pnl=float(pos.get("unrealizedPnl", 0)),
                    side=pos.get("side", "long"),
                    leverage=float(pos.get("leverage", 1)),
                ))

        return positions

    async def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        positions = await self.get_positions()
        for pos in positions:
            if pos.symbol == symbol:
                return pos
        return None

    async def place_order(self, order: BrokerOrder) -> OrderResult:
        self._ensure_connected()

        is_futures = ":USDT" in order.symbol
        exchange = self._futures if is_futures else self._spot
        side = "buy" if order.action == "BUY" else "sell"

        try:
            params = {}
            if is_futures and order.side == "short":
                params["positionSide"] = "SHORT"

            result = await self._run_sync(
                exchange.create_order,
                order.symbol,
                order.order_type,
                side,
                order.quantity,
                order.limit_price,
                params,
            )

            filled = float(result.get("filled", 0))
            avg_price = float(result.get("average", 0) or 0)
            fee_cost = 0.0
            if result.get("fee"):
                fee_cost = float(result["fee"].get("cost", 0))

            status = "FILLED" if filled >= order.quantity * 0.99 else "PARTIAL"
            if result.get("status") == "rejected":
                status = "REJECTED"

            return OrderResult(
                order_id=str(result.get("id", "")),
                symbol=order.symbol,
                action=order.action,
                quantity=order.quantity,
                filled_quantity=filled,
                avg_fill_price=avg_price,
                status=status,
                fee=fee_cost,
            )
        except Exception as e:
            logger.error("order_failed", symbol=order.symbol, error=str(e))
            return OrderResult(
                order_id="",
                symbol=order.symbol,
                action=order.action,
                quantity=order.quantity,
                filled_quantity=0,
                avg_fill_price=0,
                status="REJECTED",
                message=str(e),
            )

    async def get_market_price(self, symbol: str) -> Optional[float]:
        self._ensure_connected()
        try:
            is_futures = ":USDT" in symbol and self._futures is not None
            exchange = self._futures if is_futures else self._spot

            if self._testnet:
                # Demo: use direct ticker endpoint to avoid market type issues
                pair_id = symbol.replace("/", "")
                result = await self._run_sync(
                    lambda: self._spot.publicGetTickerPrice({"symbol": pair_id})
                )
                return float(result.get("price", 0))

            ticker = await self._run_sync(exchange.fetch_ticker, symbol)
            return float(ticker.get("last", 0))
        except Exception as e:
            logger.error("price_fetch_failed", symbol=symbol, error=str(e))
            return None

    async def get_funding_rate(self, symbol: str) -> Optional[FundingRateInfo]:
        self._ensure_connected()
        if not self._futures:
            return None
        try:
            result = await self._run_sync(
                self._futures.fetch_funding_rate, symbol
            )
            return FundingRateInfo(
                symbol=symbol,
                rate=float(result.get("fundingRate", 0)),
                next_funding_time=result.get("fundingDatetime"),
            )
        except Exception as e:
            logger.error("funding_rate_failed", symbol=symbol, error=str(e))
            return None

    async def get_funding_rate_history(
        self, symbol: str, since: int | None = None, limit: int = 500,
    ) -> list[dict]:
        """Fetch historical funding rates."""
        self._ensure_connected()
        if not self._futures:
            return []
        try:
            result = await self._run_sync(
                self._futures.fetch_funding_rate_history,
                symbol, since, limit,
            )
            return result
        except Exception as e:
            logger.error("funding_history_failed", symbol=symbol, error=str(e))
            return []

    def _ensure_connected(self):
        if not self._connected:
            raise RuntimeError("Not connected to Binance. Call connect() first.")
