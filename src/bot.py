"""
Main bot runner – orchestrates strategy, risk manager, and broker adapter.

Architecture
------------
For each instrument the bot maintains:
  * A ``HFTScalpingStrategy`` instance fed with OHLCV bars built from ticks.
  * A shared ``RiskManager`` that enforces the 5 % drawdown limit and
    computes position sizes.
  * A ``BrokerAdapter`` (MT5 or back-test) that executes orders.

The main loop:
  1. Fetch latest tick for each enabled symbol.
  2. Build/update the running bar (tick-to-bar aggregation).
  3. On bar close, call ``strategy.on_bar()`` for each symbol.
  4. If a signal fires AND risk allows, place an order.
  5. For all open positions, check trailing-stop updates.
  6. Update equity and check drawdown / daily limits.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config.settings import (
    BROKER,
    INSTRUMENTS,
    RISK,
    STRATEGY,
    InstrumentConfig,
)
from src.broker import BrokerAdapter, create_adapter
from src.risk_manager import Position, RiskManager
from src.strategy import HFTScalpingStrategy, SignalResult, SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bar accumulator (tick → OHLCV bar)
# ---------------------------------------------------------------------------

@dataclass
class RunningBar:
    """Accumulates ticks into a single OHLCV bar."""
    symbol: str
    timeframe_seconds: int
    open: float = 0.0
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    volume: float = 0.0
    bar_start: float = 0.0
    initialized: bool = False

    def update(self, price: float, volume: float, ts: float) -> bool:
        """
        Feed a new tick.  Returns True when the bar closes
        (i.e., ``timeframe_seconds`` have elapsed since bar open).
        """
        if not self.initialized:
            self.open = price
            self.high = price
            self.low = price
            self.bar_start = ts
            self.initialized = True

        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume

        if ts - self.bar_start >= self.timeframe_seconds:
            return True   # bar complete
        return False

    def reset(self, price: float, volume: float, ts: float) -> None:
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume = volume
        self.bar_start = ts
        self.initialized = True


# ---------------------------------------------------------------------------
# HFT Bot
# ---------------------------------------------------------------------------

class HFTBot:
    """
    High-frequency trading bot.

    Parameters
    ----------
    broker : BrokerAdapter
        Connected broker adapter.
    instruments : list[InstrumentConfig]
        Instruments to trade.
    risk_manager : RiskManager
        Shared risk manager.
    strategy_cfg : StrategyConfig
        Strategy hyper-parameters.
    tick_interval_seconds : float
        How often the main loop polls for new ticks.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        instruments: List[InstrumentConfig],
        risk_manager: RiskManager,
        strategy_cfg=STRATEGY,
        tick_interval_seconds: float = 0.5,
    ) -> None:
        self._broker = broker
        self._risk = risk_manager
        self._tick_interval = tick_interval_seconds

        # Per-instrument state
        self._strategies: Dict[str, HFTScalpingStrategy] = {}
        self._bars: Dict[str, RunningBar] = {}
        self._instruments: Dict[str, InstrumentConfig] = {}

        for inst in instruments:
            self._instruments[inst.symbol] = inst
            self._strategies[inst.symbol] = HFTScalpingStrategy(inst, strategy_cfg)
            self._bars[inst.symbol] = RunningBar(
                symbol=inst.symbol,
                timeframe_seconds=strategy_cfg.signal_timeframe_seconds,
            )

        self._running = False
        self._last_day: Optional[int] = None  # tracks calendar day for daily reset

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Connect to broker and start the main event loop (blocking)."""
        if not self._broker.connect():
            logger.error("Failed to connect to broker. Exiting.")
            sys.exit(1)

        # Capture Ctrl-C / SIGTERM for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        initial_equity = self._broker.get_account_equity()
        self._risk.update_equity(initial_equity)
        logger.info("Bot started. Initial equity: %.2f", initial_equity)

        self._running = True
        try:
            self._loop()
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while self._running:
            self._maybe_new_day()

            if self._risk.state.trading_halted:
                logger.warning(
                    "Trading halted (%s). Sleeping 60 s.",
                    self._risk.state.halt_reason,
                )
                time.sleep(60)
                continue

            for symbol, inst in self._instruments.items():
                self._process_symbol(symbol, inst)

            # Update equity and check drawdown after each tick cycle
            equity = self._broker.get_account_equity()
            self._risk.update_equity(equity)

            time.sleep(self._tick_interval)

    # ------------------------------------------------------------------
    # Per-symbol processing
    # ------------------------------------------------------------------

    def _process_symbol(self, symbol: str, inst: InstrumentConfig) -> None:
        tick = self._broker.get_tick(symbol)
        if tick is None:
            return

        mid = (tick.bid + tick.ask) / 2.0
        spread = tick.ask - tick.bid
        bar = self._bars[symbol]

        bar_closed = bar.update(mid, tick.volume, tick.time)

        if bar_closed:
            signal = self._strategies[symbol].on_bar(
                open_=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                spread=spread,
            )
            bar.reset(mid, tick.volume, tick.time)

            if signal.direction != SIGNAL_NONE:
                self._maybe_open_trade(symbol, inst, signal, tick.ask if signal.direction == SIGNAL_BUY else tick.bid)

        # Check trailing stops for open positions
        self._update_trailing_stops(symbol, mid)

    def _maybe_open_trade(
        self,
        symbol: str,
        inst: InstrumentConfig,
        signal: SignalResult,
        entry_price: float,
    ) -> None:
        if not self._risk.can_open_trade():
            return

        # Don't double-up on the same symbol
        existing = [
            p for p in self._risk.state.open_positions.values()
            if p.symbol == symbol
        ]
        if existing:
            return

        volume = self._risk.position_size(
            entry_price=entry_price,
            stop_loss_price=signal.stop_loss,
            pip_value=inst.pip_value,
        )

        result = self._broker.place_order(
            symbol=symbol,
            direction=signal.direction,
            volume=volume,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            magic=BROKER.magic_number,
            comment=BROKER.order_comment,
        )

        if result.success:
            pos = Position(
                ticket=result.ticket,
                symbol=symbol,
                direction=signal.direction,
                entry_price=result.price,
                volume=volume,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                atr_at_entry=signal.atr,
            )
            self._risk.register_open_position(pos)

    def _update_trailing_stops(self, symbol: str, current_price: float) -> None:
        for ticket, pos in list(self._risk.state.open_positions.items()):
            if pos.symbol != symbol:
                continue
            new_sl = self._risk.get_trailing_stop(pos, current_price)
            if new_sl is not None:
                if self._broker.modify_sl(ticket, new_sl):
                    pos.stop_loss = new_sl
                    logger.debug(
                        "Trailing stop updated: ticket=%d new_sl=%.5f",
                        ticket, new_sl,
                    )

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    def _maybe_new_day(self) -> None:
        import datetime
        today = datetime.date.today().toordinal()
        if self._last_day != today:
            self._last_day = today
            equity = self._broker.get_account_equity()
            self._risk.new_day(equity)
            # Reset VWAP-anchored indicators by resetting strategies
            for strat in self._strategies.values():
                strat.reset()
            logger.info("New trading day – strategies and daily P&L reset. Equity=%.2f", equity)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _shutdown(self) -> None:
        logger.info("Shutting down bot…")
        self._broker.disconnect()
        logger.info("Shutdown complete.")

    def _handle_signal(self, signum, frame) -> None:
        logger.info("Signal %d received – stopping bot.", signum)
        self._running = False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
    )

    broker = create_adapter(
        mode=BROKER.mode,
        login=BROKER.mt5_login,
        password=BROKER.mt5_password,
        server=BROKER.mt5_server,
    )

    initial_equity = 10_000.0  # will be replaced by live equity after connect
    risk_mgr = RiskManager(
        initial_equity=initial_equity,
        max_drawdown_pct=RISK.max_drawdown_pct,
        risk_per_trade_pct=RISK.risk_per_trade_pct,
        max_open_positions=RISK.max_open_positions,
        daily_loss_limit_pct=RISK.daily_loss_limit_pct,
        trailing_stop_enabled=RISK.trailing_stop_enabled,
        trailing_stop_atr_multiplier=RISK.trailing_stop_atr_multiplier,
    )

    bot = HFTBot(
        broker=broker,
        instruments=INSTRUMENTS,
        risk_manager=risk_mgr,
    )
    bot.start()


if __name__ == "__main__":
    main()
