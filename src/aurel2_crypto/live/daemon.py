"""Live trading daemon for aurel2-crypto.

Runs 24/7, executing two independent strategies:
1. Carry: checks funding rates every 8 hours
2. Momentum: rebalances weekly (Mondays)

Unlike Aurel2 (market-hours only), this daemon runs continuously.
"""

import asyncio
import json
import signal
import time
from datetime import datetime, timedelta, date
from pathlib import Path

import structlog

from aurel2_crypto.broker.binance import BinanceBroker
from aurel2_crypto.config.settings import Settings
from aurel2_crypto.core.assets import ASSET_REGISTRY, CARRY_ASSETS, MOMENTUM_ASSETS, get_all_symbols
from aurel2_crypto.core.models import CryptoAsset, SignalAction
from aurel2_crypto.data.providers.binance import BinanceDataProvider
from aurel2_crypto.data.momentum import calculate_momentum_scores, rank_by_momentum
from aurel2_crypto.notifications.ntfy import NtfyNotifier
from aurel2_crypto.strategies.funding_carry import FundingCarryStrategy
from aurel2_crypto.strategies.momentum import ShortTermMomentumStrategy

logger = structlog.get_logger()

DATA_DIR = Path.home() / ".aurel2-crypto"
HEARTBEAT_FILE = DATA_DIR / "heartbeat.json"
TRADE_JOURNAL_FILE = DATA_DIR / "trade_journal.json"


class CryptoDaemon:
    """24/7 crypto trading daemon."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.broker = BinanceBroker(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            testnet=settings.binance_testnet,
        )
        self.data_provider = BinanceDataProvider()
        self.momentum = ShortTermMomentumStrategy(
            lookback_days=settings.momentum_lookback_days,
            rebalance_days=settings.momentum_rebalance_days,
        )
        self.carry = FundingCarryStrategy(
            entry_rate=settings.carry_min_funding_rate,
        )
        self.notifier = NtfyNotifier(topic=settings.ntfy_topic) if settings.ntfy_topic else None

        self._running = False
        self._last_momentum_check: date | None = None
        self._last_carry_check: datetime | None = None
        self._error_count = 0
        self._current_momentum_holding: CryptoAsset | None = None
        self._carry_positions: dict[CryptoAsset, bool] = {a: False for a in CARRY_ASSETS}

        DATA_DIR.mkdir(parents=True, exist_ok=True)

    async def start(self):
        """Start the daemon."""
        self._running = True
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)

        logger.info("daemon_starting", testnet=self.settings.binance_testnet)

        connected = await self.broker.connect()
        if not connected:
            logger.error("broker_connection_failed")
            return

        if self.notifier:
            mode = "TESTNET" if self.settings.binance_testnet else "LIVE"
            self.notifier.send(
                message=f"Mode: {mode}\nStrategies: carry ({self.settings.carry_allocation:.0%}) + momentum ({self.settings.momentum_allocation:.0%})",
                title="Crypto Daemon Started",
                tags=["rocket"],
            )

        # Start heartbeat writer
        heartbeat_task = asyncio.create_task(self._heartbeat_writer())

        try:
            await self._run_loop()
        finally:
            heartbeat_task.cancel()
            await self.broker.disconnect()
            logger.info("daemon_stopped")

    async def _run_loop(self):
        """Main daemon loop — checks every 5 minutes."""
        while self._running:
            try:
                now = datetime.utcnow()

                # Carry: check every 8 hours
                if self._should_check_carry(now):
                    await self._run_carry_check(now)
                    self._last_carry_check = now

                # Momentum: check on Mondays
                if self._should_check_momentum(now):
                    await self._run_momentum_check(now)
                    self._last_momentum_check = now.date()

                self._error_count = 0

            except Exception as e:
                self._error_count += 1
                logger.error("check_cycle_error", error=str(e), count=self._error_count)
                if self.notifier and self._error_count <= 3:
                    self.notifier.send_error(f"Check cycle error #{self._error_count}: {e}")

            await asyncio.sleep(300)  # 5 minutes

    def _should_check_carry(self, now: datetime) -> bool:
        if self._last_carry_check is None:
            return True
        return (now - self._last_carry_check) >= timedelta(hours=8)

    def _should_check_momentum(self, now: datetime) -> bool:
        today = now.date()
        if today.weekday() != 0:  # Monday only
            return False
        if self._last_momentum_check == today:
            return False
        return now.hour >= 0  # Any time on Monday

    async def _run_carry_check(self, now: datetime):
        """Check funding rates and manage carry positions."""
        logger.info("carry_check_start")

        for asset_id in CARRY_ASSETS:
            asset = ASSET_REGISTRY[asset_id]
            if not asset.perp_symbol:
                continue

            rate_info = await self.broker.get_funding_rate(asset.perp_symbol)
            if not rate_info:
                continue

            rate = rate_info.rate
            in_carry = self._carry_positions[asset_id]
            annualized = rate * 3 * 365

            logger.info("funding_rate", symbol=asset.perp_symbol, rate=f"{rate:.4%}", annualized=f"{annualized:.1%}")

            if not in_carry and rate >= self.carry.entry_rate:
                # Open carry: buy spot + short perp
                logger.info("carry_entry", symbol=asset_id.value, rate=f"{rate:.4%}")
                # In testnet, just log the intent
                self._carry_positions[asset_id] = True
                self._record_trade("carry_entry", asset_id.value, rate, f"Funding rate {rate:.4%}/8h ({annualized:.1%} ann)")
                if self.notifier:
                    self.notifier.send_funding(asset.perp_symbol, rate, 0)

            elif in_carry and rate <= self.carry.exit_rate:
                logger.info("carry_exit", symbol=asset_id.value, rate=f"{rate:.4%}")
                self._carry_positions[asset_id] = False
                self._record_trade("carry_exit", asset_id.value, rate, f"Funding rate dropped to {rate:.4%}/8h")

    async def _run_momentum_check(self, now: datetime):
        """Run weekly momentum rebalance."""
        logger.info("momentum_check_start")

        # Fetch recent prices
        prices = self.data_provider.get_multi_ohlcv(
            symbols=get_all_symbols(),
            timeframe="1d",
            start_date=datetime.utcnow() - timedelta(days=60),
            end_date=datetime.utcnow(),
        )

        if prices.empty:
            logger.error("momentum_no_price_data")
            return

        signal = self.momentum.generate_signal(
            prices, now.date(), self._current_momentum_holding,
        )

        logger.info(
            "momentum_signal",
            action=signal.action.value,
            asset=signal.asset_id.value if signal.asset_id else None,
            confidence=signal.confidence,
            reasoning=signal.reasoning,
        )

        if signal.action == SignalAction.HOLD:
            return

        target = signal.asset_id
        if target and target != self._current_momentum_holding:
            # Execute the switch
            if self._current_momentum_holding and self._current_momentum_holding != CryptoAsset.USDT:
                old_asset = ASSET_REGISTRY[self._current_momentum_holding]
                price = await self.broker.get_market_price(old_asset.symbol)
                logger.info("momentum_sell", symbol=old_asset.symbol, price=price)
                self._record_trade("momentum_sell", old_asset.symbol, price or 0, signal.reasoning)
                if self.notifier and price:
                    self.notifier.send_trade("SELL", old_asset.symbol, price, signal.reasoning)

            if target != CryptoAsset.USDT:
                new_asset = ASSET_REGISTRY[target]
                price = await self.broker.get_market_price(new_asset.symbol)
                logger.info("momentum_buy", symbol=new_asset.symbol, price=price)
                self._record_trade("momentum_buy", new_asset.symbol, price or 0, signal.reasoning)
                if self.notifier and price:
                    self.notifier.send_trade("BUY", new_asset.symbol, price, signal.reasoning)

            self._current_momentum_holding = target

    def _record_trade(self, action: str, symbol: str, price: float, reasoning: str):
        """Append trade to journal."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": action,
            "symbol": symbol,
            "price": price,
            "reasoning": reasoning,
        }
        journal = []
        if TRADE_JOURNAL_FILE.exists():
            try:
                journal = json.loads(TRADE_JOURNAL_FILE.read_text())
            except Exception:
                pass
        journal.append(entry)
        TRADE_JOURNAL_FILE.write_text(json.dumps(journal, indent=2))

    async def _heartbeat_writer(self):
        """Write heartbeat every 60 seconds."""
        while self._running:
            heartbeat = {
                "timestamp": time.time(),
                "connected": self.broker.is_connected,
                "testnet": self.settings.binance_testnet,
                "error_count": self._error_count,
                "last_carry_check": self._last_carry_check.isoformat() if self._last_carry_check else None,
                "last_momentum_check": self._last_momentum_check.isoformat() if self._last_momentum_check else None,
                "momentum_holding": self._current_momentum_holding.value if self._current_momentum_holding else None,
                "carry_positions": {k.value: v for k, v in self._carry_positions.items()},
            }
            try:
                HEARTBEAT_FILE.write_text(json.dumps(heartbeat, indent=2))
            except Exception as e:
                logger.error("heartbeat_write_error", error=str(e))
            await asyncio.sleep(60)

    def _shutdown(self):
        logger.info("shutdown_requested")
        self._running = False
