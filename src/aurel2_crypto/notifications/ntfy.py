"""Ntfy notification service for Aurel2-Crypto."""

import time
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

_last_notification_time: dict[str, float] = {}


class NtfyNotifier:
    """Send push notifications via ntfy.sh."""

    def __init__(self, topic: str, server: str = "https://ntfy.sh", rate_limit_seconds: int = 3600):
        self.topic = topic
        self.server = server.rstrip("/")
        self.rate_limit_seconds = rate_limit_seconds

    def send(
        self,
        message: str,
        title: Optional[str] = None,
        priority: Optional[str] = None,
        tags: Optional[list[str]] = None,
        category: Optional[str] = None,
    ) -> bool:
        if category and self.rate_limit_seconds > 0:
            now = time.time()
            if now - _last_notification_time.get(category, 0) < self.rate_limit_seconds:
                return False

        url = f"{self.server}/{self.topic}"
        headers: dict[str, str] = {}
        if title:
            headers["Title"] = title
        if priority:
            headers["Priority"] = priority
        if tags:
            headers["Tags"] = ",".join(tags)

        try:
            response = httpx.post(url, content=message, headers=headers)
            if response.status_code == 200:
                if category:
                    _last_notification_time[category] = time.time()
                logger.info("notification_sent", topic=self.topic, title=title)
                return True
            else:
                logger.warning("notification_failed", status=response.status_code)
                return False
        except Exception as e:
            logger.error("notification_error", error=str(e))
            return False

    def send_trade(self, action: str, symbol: str, price: float, reasoning: str) -> bool:
        tag = "chart_with_upwards_trend" if action == "BUY" else "chart_with_downwards_trend"
        return self.send(
            message=f"Action: {action} {symbol}\nPrice: ${price:,.2f}\n\n{reasoning}",
            title=f"Crypto: {action} {symbol} @ ${price:,.2f}",
            tags=[tag, "money_bag"],
        )

    def send_funding(self, symbol: str, rate: float, payment: float) -> bool:
        return self.send(
            message=f"Symbol: {symbol}\nRate: {rate:.4%}/8h\nPayment: ${payment:+,.2f}",
            title=f"Crypto: Funding collected on {symbol}",
            tags=["dollar"],
            category="funding",
        )

    def send_error(self, error: str) -> bool:
        return self.send(
            message=error,
            title="Crypto: Error",
            priority="high",
            tags=["warning"],
            category="error",
        )
