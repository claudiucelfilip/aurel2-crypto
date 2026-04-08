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
from aurel2_crypto.broker.base import BrokerOrder
from aurel2_crypto.broker.earn import BinanceEarn
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
EQUITY_CURVE_FILE = DATA_DIR / "equity_curve.json"
RUN_STATE_FILE = DATA_DIR / "run_state.json"

# Equity snapshot interval (don't bloat the file — every 15 min is plenty)
EQUITY_SNAPSHOT_INTERVAL_SEC = 900


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
        self._high_water_mark: float = 0.0
        self._trailing_stop_pct = settings.trailing_stop_pct
        self._earn: BinanceEarn | None = None  # Initialized after broker connects

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load_state()

    def _load_state(self):
        """Restore state from trade journal and heartbeat on restart."""
        # Try heartbeat first (has current holding + HWM)
        if HEARTBEAT_FILE.exists():
            try:
                hb = json.loads(HEARTBEAT_FILE.read_text())
                holding = hb.get("momentum_holding")
                if holding and holding != "usdt":
                    for ca in CryptoAsset:
                        if ca.value == holding:
                            self._current_momentum_holding = ca
                            break
                self._high_water_mark = hb.get("high_water_mark", 0.0)
                logger.info(
                    "state_restored",
                    holding=self._current_momentum_holding.value if self._current_momentum_holding else None,
                    hwm=self._high_water_mark,
                )
            except Exception as e:
                logger.warning("state_restore_failed", error=str(e))

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

        # Initialize Flexible Earn (live only — demo API doesn't support /sapi/)
        if not self.settings.binance_testnet and self.broker._spot:
            self._earn = BinanceEarn(self.broker._spot)
            logger.info("earn_initialized")

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

                # Trailing stop: check every cycle (every 5 min)
                await self._check_trailing_stop()

                # Carry: check every 8 hours
                if self._should_check_carry(now):
                    await self._run_carry_check(now)
                    self._last_carry_check = now

                # Daily filter: exit if all coins negative (Tue-Sun)
                if self._should_check_daily_filter(now):
                    await self._run_daily_filter(now)

                # Momentum: full rebalance on Mondays
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
        if today.weekday() != 0:  # Monday only for full rebalance
            return False
        if self._last_momentum_check == today:
            return False
        return now.hour >= 0  # Any time on Monday

    def _should_check_daily_filter(self, now: datetime) -> bool:
        """Daily absolute momentum check — exit if all coins negative."""
        if not self._current_momentum_holding:
            return False
        if self._current_momentum_holding == CryptoAsset.USDT:
            return False
        # Run once per day (not on Mondays — momentum check handles that)
        today = now.date()
        if today.weekday() == 0:
            return False
        if self._last_momentum_check == today:
            return False
        return now.hour >= 1  # After 01:00 UTC

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

    async def _check_trailing_stop(self):
        """Check trailing stop-loss every cycle (every 5 min)."""
        if self._trailing_stop_pct <= 0:
            return
        if not self._current_momentum_holding:
            return
        if self._current_momentum_holding == CryptoAsset.USDT:
            return

        asset = ASSET_REGISTRY[self._current_momentum_holding]
        price = await self.broker.get_market_price(asset.symbol)
        if not price:
            return

        # Update high water mark
        if price > self._high_water_mark:
            self._high_water_mark = price

        if self._high_water_mark <= 0:
            return

        drawdown = (self._high_water_mark - price) / self._high_water_mark
        if drawdown >= self._trailing_stop_pct:
            logger.warning(
                "trailing_stop_triggered",
                symbol=asset.symbol,
                price=price,
                hwm=self._high_water_mark,
                drawdown=f"{drawdown:.1%}",
                threshold=f"{self._trailing_stop_pct:.0%}",
            )

            # Execute sell
            reasoning = (
                f"STOP-LOSS: {asset.symbol} dropped {drawdown:.1%} from peak "
                f"${self._high_water_mark:,.2f} → ${price:,.2f} "
                f"(threshold: {self._trailing_stop_pct:.0%})"
            )
            self._record_trade("stop_loss", asset.symbol, price, reasoning)

            if self.notifier:
                self.notifier.send(
                    message=reasoning,
                    title=f"Crypto: STOP-LOSS {asset.symbol}",
                    priority="high",
                    tags=["rotating_light", "chart_with_downwards_trend"],
                )

            # Execute sell
            await self._sell_position(asset.symbol, "stop_loss", reasoning)
            self._current_momentum_holding = CryptoAsset.USDT
            self._high_water_mark = 0.0

    async def _run_daily_filter(self, now: datetime):
        """Daily absolute momentum check — exit to USDT if all coins trending down."""
        logger.info("daily_filter_check")

        prices = self.data_provider.get_multi_ohlcv(
            symbols=get_all_symbols(),
            timeframe="1d",
            start_date=datetime.utcnow() - timedelta(days=60),
            end_date=datetime.utcnow(),
        )
        if prices.empty:
            return

        scores = calculate_momentum_scores(
            prices, MOMENTUM_ASSETS, now.date(), self.settings.momentum_lookback_days,
        )
        if not scores:
            return

        all_negative = all(s.momentum <= 0 for s in scores.values())
        if all_negative:
            reasoning = (
                f"DAILY EXIT: all momentum negative. "
                + ", ".join(f"{k.value}={v.momentum:+.1%}" for k, v in scores.items())
            )
            logger.warning("daily_filter_triggered", reasoning=reasoning)

            await self._sell_position(
                ASSET_REGISTRY[self._current_momentum_holding].symbol,
                "daily_exit", reasoning,
            )
            self._current_momentum_holding = CryptoAsset.USDT
            self._high_water_mark = 0.0
        else:
            logger.info("daily_filter_ok", holding=self._current_momentum_holding.value)

        self._last_momentum_check = now.date()

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
            # Sell current holding
            if self._current_momentum_holding and self._current_momentum_holding != CryptoAsset.USDT:
                await self._sell_position(
                    ASSET_REGISTRY[self._current_momentum_holding].symbol,
                    "momentum_sell", signal.reasoning,
                )

            # Buy new target
            if target != CryptoAsset.USDT:
                buy_result = await self._buy_with_available(
                    ASSET_REGISTRY[target].symbol,
                    "momentum_buy", signal.reasoning,
                )
                if buy_result and buy_result.avg_fill_price > 0:
                    self._high_water_mark = buy_result.avg_fill_price
                else:
                    self._high_water_mark = 0.0
            else:
                self._high_water_mark = 0.0

            self._current_momentum_holding = target

    async def _sell_position(self, symbol: str, action: str, reasoning: str):
        """Sell entire position of a symbol."""
        position = await self.broker.get_position(symbol)
        if not position or position.shares <= 0:
            logger.warning("sell_no_position", symbol=symbol)
            return None

        order = BrokerOrder(symbol=symbol, action="SELL", quantity=position.shares)
        result = await self.broker.place_order(order)

        logger.info(
            "order_executed", action="SELL", symbol=symbol,
            status=result.status, filled=result.filled_quantity,
            price=result.avg_fill_price,
        )
        self._record_trade(
            action, symbol, result.avg_fill_price,
            f"{reasoning} | Sold {result.filled_quantity} @ ${result.avg_fill_price:,.2f}",
        )
        if self.notifier and result.status == "FILLED":
            self.notifier.send_trade("SELL", symbol, result.avg_fill_price, reasoning)

        # Subscribe idle USDT to Flexible Earn
        if self._earn and result.status == "FILLED":
            summary = await self.broker.get_account_summary()
            if summary.cash_balance > 10:
                await self._earn.subscribe_usdt(summary.cash_balance - 1)  # Keep $1 buffer

        return result

    async def _buy_with_available(self, symbol: str, action: str, reasoning: str):
        """Buy as much as possible of a symbol with available USDT."""
        # Redeem from Flexible Earn first
        if self._earn:
            await self._earn.redeem_all()

        summary = await self.broker.get_account_summary()
        available = summary.cash_balance * 0.995  # Reserve 0.5% for fees

        price = await self.broker.get_market_price(symbol)
        if not price or price <= 0:
            logger.error("buy_no_price", symbol=symbol)
            return None

        quantity = available / price
        if quantity <= 0:
            logger.warning("buy_insufficient_funds", symbol=symbol, available=available)
            return None

        order = BrokerOrder(symbol=symbol, action="BUY", quantity=quantity)
        result = await self.broker.place_order(order)

        logger.info(
            "order_executed", action="BUY", symbol=symbol,
            status=result.status, filled=result.filled_quantity,
            price=result.avg_fill_price,
        )
        self._record_trade(
            action, symbol, result.avg_fill_price,
            f"{reasoning} | Bought {result.filled_quantity} @ ${result.avg_fill_price:,.2f}",
        )
        if self.notifier and result.status == "FILLED":
            self.notifier.send_trade("BUY", symbol, result.avg_fill_price, reasoning)
        return result

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

    async def _compute_equity(self) -> float | None:
        """Compute total account equity (USDT cash + market value of positions)."""
        try:
            summary = await self.broker.get_account_summary()
            total = float(summary.total_value or 0)
            # get_account_summary already includes spot USDT + futures total,
            # but spot non-USDT holdings (e.g. ETH) need to be priced in.
            positions = await self.broker.get_positions()
            for p in positions:
                # Only spot positions (futures already counted in futures_total)
                if ":" not in p.symbol and p.market_value:
                    total += float(p.market_value)
            return total
        except Exception as e:
            logger.warning("equity_compute_failed", error=str(e))
            return None

    def _append_equity_snapshot(self, equity: float):
        """Append an equity snapshot to the curve, downsampled to once per interval."""
        now = time.time()
        curve = []
        if EQUITY_CURVE_FILE.exists():
            try:
                curve = json.loads(EQUITY_CURVE_FILE.read_text())
            except Exception:
                curve = []

        if curve:
            last_ts = curve[-1].get("timestamp", 0)
            if now - last_ts < EQUITY_SNAPSHOT_INTERVAL_SEC:
                return  # Too soon, skip

        curve.append({
            "timestamp": now,
            "iso": datetime.utcnow().isoformat(),
            "equity": round(equity, 2),
            "holding": self._current_momentum_holding.value if self._current_momentum_holding else None,
        })

        # Cap at ~1 year of 15-min snapshots (~35k points). Trim to keep file sane.
        if len(curve) > 40000:
            curve = curve[-40000:]

        try:
            EQUITY_CURVE_FILE.write_text(json.dumps(curve))
        except Exception as e:
            logger.error("equity_curve_write_error", error=str(e))

        # Initialize run state on first snapshot
        if not RUN_STATE_FILE.exists():
            try:
                RUN_STATE_FILE.write_text(json.dumps({
                    "started_at": datetime.utcnow().isoformat(),
                    "started_timestamp": now,
                    "starting_equity": round(equity, 2),
                    "testnet": self.settings.binance_testnet,
                }, indent=2))
            except Exception as e:
                logger.error("run_state_write_error", error=str(e))

    async def _heartbeat_writer(self):
        """Write heartbeat every 60 seconds."""
        while self._running:
            equity = await self._compute_equity()

            heartbeat = {
                "timestamp": time.time(),
                "connected": self.broker.is_connected,
                "testnet": self.settings.binance_testnet,
                "error_count": self._error_count,
                "last_carry_check": self._last_carry_check.isoformat() if self._last_carry_check else None,
                "last_momentum_check": self._last_momentum_check.isoformat() if self._last_momentum_check else None,
                "momentum_holding": self._current_momentum_holding.value if self._current_momentum_holding else None,
                "high_water_mark": self._high_water_mark,
                "trailing_stop_pct": self._trailing_stop_pct,
                "earn_active": self._earn is not None and self._earn._subscribed,
                "carry_positions": {k.value: v for k, v in self._carry_positions.items()},
                "equity": equity,
            }
            try:
                HEARTBEAT_FILE.write_text(json.dumps(heartbeat, indent=2))
            except Exception as e:
                logger.error("heartbeat_write_error", error=str(e))

            if equity is not None and equity > 0:
                self._append_equity_snapshot(equity)

            await asyncio.sleep(60)

    def _shutdown(self):
        logger.info("shutdown_requested")
        self._running = False
