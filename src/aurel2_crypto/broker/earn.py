"""Binance Simple Earn integration for idle USDT yield.

When the momentum strategy goes to cash (USDT), we subscribe to
Binance Flexible Earn to collect interest. When momentum signals
a buy, we redeem instantly and deploy.

Not available on demo API — only works in live mode.
"""

import time

import structlog

logger = structlog.get_logger()


class BinanceEarn:
    """Manage USDT Flexible Earn subscriptions."""

    def __init__(self, spot_exchange):
        """Args: spot_exchange is the ccxt.binance instance."""
        self._exchange = spot_exchange
        self._subscribed = False
        self._product_id = None

    async def subscribe_usdt(self, amount: float) -> bool:
        """Subscribe idle USDT to Flexible Earn.

        Returns True if successful.
        """
        if not self._exchange:
            return False

        try:
            # Find USDT flexible product
            if not self._product_id:
                products = await self._run(
                    lambda: self._exchange.sapiGetSimpleEarnFlexibleList({
                        "asset": "USDT",
                        "timestamp": int(time.time() * 1000),
                    })
                )
                rows = products.get("rows", [])
                if rows:
                    self._product_id = rows[0].get("productId")
                    apr = rows[0].get("latestAnnualPercentageRate", "?")
                    logger.info("earn_product_found", product_id=self._product_id, apr=apr)

            if not self._product_id:
                logger.warning("earn_no_usdt_product")
                return False

            result = await self._run(
                lambda: self._exchange.sapiPostSimpleEarnFlexibleSubscribe({
                    "productId": self._product_id,
                    "amount": str(amount),
                    "timestamp": int(time.time() * 1000),
                })
            )

            success = result.get("success", False)
            if success:
                self._subscribed = True
                logger.info("earn_subscribed", amount=amount, product_id=self._product_id)
            else:
                logger.warning("earn_subscribe_failed", result=result)
            return success

        except Exception as e:
            logger.error("earn_subscribe_error", error=str(e))
            return False

    async def redeem_all(self) -> bool:
        """Redeem all USDT from Flexible Earn (instant).

        Call this before placing a buy order.
        Returns True if successful or nothing to redeem.
        """
        if not self._exchange or not self._subscribed:
            return True

        try:
            if not self._product_id:
                return True

            result = await self._run(
                lambda: self._exchange.sapiPostSimpleEarnFlexibleRedeem({
                    "productId": self._product_id,
                    "redeemAll": True,
                    "timestamp": int(time.time() * 1000),
                })
            )

            success = result.get("success", False)
            if success:
                self._subscribed = False
                logger.info("earn_redeemed")
            else:
                logger.warning("earn_redeem_failed", result=result)
            return success

        except Exception as e:
            logger.error("earn_redeem_error", error=str(e))
            return False

    async def get_position(self) -> float:
        """Get current USDT amount in Flexible Earn."""
        if not self._exchange:
            return 0.0

        try:
            result = await self._run(
                lambda: self._exchange.sapiGetSimpleEarnFlexiblePosition({
                    "asset": "USDT",
                    "timestamp": int(time.time() * 1000),
                })
            )
            rows = result.get("rows", [])
            total = sum(float(r.get("totalAmount", 0)) for r in rows)
            return total
        except Exception:
            return 0.0

    async def _run(self, func):
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(None, func)
