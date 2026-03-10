"""
Risk Manager – enforces drawdown limits, position sizing, and daily loss caps.

Key rules
---------
* Maximum drawdown from equity peak: configurable (default 5 %).
* Risk per trade: configurable (default 0.5 % of current equity).
* Daily loss limit: configurable (default 2 % of start-of-day equity).
* Hard cap on simultaneous open positions.
* Trailing stop management once a position is in profit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """Represents a single open trade."""

    ticket: int
    symbol: str
    direction: str          # "buy" | "sell"
    entry_price: float
    volume: float
    stop_loss: float
    take_profit: float
    atr_at_entry: float     # ATR value when the trade was opened (for trailing)
    open_pnl: float = 0.0
    best_price_reached: Optional[float] = None  # tracks trailing-stop high-water mark


@dataclass
class RiskState:
    """Mutable state tracked by the risk manager across a session."""

    peak_equity: float
    start_of_day_equity: float
    current_equity: float
    open_positions: Dict[int, Position] = field(default_factory=dict)
    daily_realized_pnl: float = 0.0
    trading_halted: bool = False
    halt_reason: str = ""


# ---------------------------------------------------------------------------
# Risk Manager
# ---------------------------------------------------------------------------

class RiskManager:
    """
    Enforces risk rules and computes position sizes.

    Parameters
    ----------
    initial_equity : float
        Account balance at bot startup.
    max_drawdown_pct : float
        Maximum drawdown from equity peak (%).
    risk_per_trade_pct : float
        Equity fraction risked per trade (%).
    max_open_positions : int
        Hard cap on simultaneous open positions.
    daily_loss_limit_pct : float
        Intra-day loss limit (% of start-of-day equity).
    trailing_stop_enabled : bool
        Whether to apply trailing stops.
    trailing_stop_atr_multiplier : float
        Trail distance as a multiple of ATR at entry.
    min_volume : float
        Minimum order volume (lot/contract size).
    max_volume : float
        Maximum order volume (lot/contract size).
    """

    def __init__(
        self,
        initial_equity: float,
        max_drawdown_pct: float = 5.0,
        risk_per_trade_pct: float = 0.5,
        max_open_positions: int = 3,
        daily_loss_limit_pct: float = 2.0,
        trailing_stop_enabled: bool = True,
        trailing_stop_atr_multiplier: float = 1.0,
        min_volume: float = 0.01,
        max_volume: float = 5.0,
    ) -> None:
        if initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if not (0 < max_drawdown_pct <= 100):
            raise ValueError("max_drawdown_pct must be in (0, 100]")
        if not (0 < risk_per_trade_pct <= 100):
            raise ValueError("risk_per_trade_pct must be in (0, 100]")

        self._max_dd_pct = max_drawdown_pct
        self._risk_pct = risk_per_trade_pct / 100.0
        self._max_positions = max_open_positions
        self._daily_limit_pct = daily_loss_limit_pct / 100.0
        self._trailing_enabled = trailing_stop_enabled
        self._trailing_atr_mult = trailing_stop_atr_multiplier
        self._min_vol = min_volume
        self._max_vol = max_volume

        self.state = RiskState(
            peak_equity=initial_equity,
            start_of_day_equity=initial_equity,
            current_equity=initial_equity,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_equity(self, new_equity: float) -> None:
        """Called after each tick / bar with the latest account equity."""
        self.state.current_equity = new_equity
        if new_equity > self.state.peak_equity:
            self.state.peak_equity = new_equity
        self._check_drawdown()

    def new_day(self, equity: float) -> None:
        """Reset daily tracking at the start of each trading day."""
        self.state.start_of_day_equity = equity
        self.state.daily_realized_pnl = 0.0
        if self.state.trading_halted and "daily" in self.state.halt_reason.lower():
            # Lift daily halt at the start of a new day
            self.state.trading_halted = False
            self.state.halt_reason = ""
            logger.info("Daily loss limit reset – trading resumed.")

    def can_open_trade(self) -> bool:
        """Return True if a new trade is allowed under current risk rules."""
        if self.state.trading_halted:
            logger.warning("Trading halted: %s", self.state.halt_reason)
            return False
        if len(self.state.open_positions) >= self._max_positions:
            logger.warning(
                "Max open positions (%d) reached.", self._max_positions
            )
            return False
        return True

    def position_size(
        self,
        entry_price: float,
        stop_loss_price: float,
        pip_value: float,
    ) -> float:
        """
        Calculate the volume (lots/contracts) for a trade so that the
        monetary risk equals ``risk_per_trade_pct`` of current equity.

        Parameters
        ----------
        entry_price : float
            Intended entry price.
        stop_loss_price : float
            Stop-loss price for this trade.
        pip_value : float
            Monetary value of one pip/point in account currency.

        Returns
        -------
        float
            Clipped to [min_volume, max_volume].
        """
        sl_distance = abs(entry_price - stop_loss_price)
        if sl_distance <= 0 or pip_value <= 0:
            return self._min_vol

        risk_amount = self.state.current_equity * self._risk_pct
        # volume = risk_amount / (sl_distance_in_pips * pip_value_per_lot)
        # For simplicity, pip_value here is per lot per point.
        volume = risk_amount / (sl_distance * pip_value)
        volume = max(self._min_vol, min(self._max_vol, volume))
        # Round to 2 decimal places (broker standard)
        return round(volume, 2)

    def register_open_position(self, position: Position) -> None:
        """Record a newly opened position."""
        self.state.open_positions[position.ticket] = position
        logger.info(
            "Position opened: ticket=%d %s %s @ %.5f vol=%.2f SL=%.5f TP=%.5f",
            position.ticket,
            position.symbol,
            position.direction,
            position.entry_price,
            position.volume,
            position.stop_loss,
            position.take_profit,
        )

    def close_position(self, ticket: int, close_price: float) -> float:
        """
        Mark a position as closed, record P&L, and return the realized P&L.
        """
        pos = self.state.open_positions.pop(ticket, None)
        if pos is None:
            logger.warning("close_position called for unknown ticket %d", ticket)
            return 0.0

        if pos.direction == "buy":
            pnl = (close_price - pos.entry_price) * pos.volume
        else:
            pnl = (pos.entry_price - close_price) * pos.volume

        self.state.daily_realized_pnl += pnl
        logger.info(
            "Position closed: ticket=%d pnl=%.2f daily_pnl=%.2f",
            ticket, pnl, self.state.daily_realized_pnl,
        )
        self._check_daily_loss()
        return pnl

    def get_trailing_stop(
        self, position: Position, current_price: float
    ) -> Optional[float]:
        """
        Compute the new trailing stop level for *position* given *current_price*.

        Returns the updated stop price, or ``None`` if no update is needed.
        """
        if not self._trailing_enabled:
            return None

        trail_dist = position.atr_at_entry * self._trailing_atr_mult

        if position.direction == "buy":
            high_water = position.best_price_reached or position.entry_price
            if current_price > high_water:
                position.best_price_reached = current_price
                new_sl = current_price - trail_dist
                if new_sl > position.stop_loss:
                    return new_sl
        else:  # sell
            low_water = position.best_price_reached or position.entry_price
            if current_price < low_water:
                position.best_price_reached = current_price
                new_sl = current_price + trail_dist
                if new_sl < position.stop_loss:
                    return new_sl
        return None

    def current_drawdown_pct(self) -> float:
        """Return the current drawdown from peak equity as a percentage."""
        if self.state.peak_equity <= 0:
            return 0.0
        return (
            (self.state.peak_equity - self.state.current_equity)
            / self.state.peak_equity
            * 100.0
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_drawdown(self) -> None:
        dd = self.current_drawdown_pct()
        if dd >= self._max_dd_pct and not self.state.trading_halted:
            self.state.trading_halted = True
            self.state.halt_reason = (
                f"Max drawdown reached: {dd:.2f}% >= {self._max_dd_pct}%"
            )
            logger.error("TRADING HALTED – %s", self.state.halt_reason)

    def _check_daily_loss(self) -> None:
        if self.state.start_of_day_equity <= 0:
            return
        daily_loss_pct = (
            -self.state.daily_realized_pnl / self.state.start_of_day_equity
        )
        if daily_loss_pct >= self._daily_limit_pct and not self.state.trading_halted:
            self.state.trading_halted = True
            self.state.halt_reason = (
                f"Daily loss limit reached: {daily_loss_pct*100:.2f}% "
                f">= {self._daily_limit_pct*100:.2f}%"
            )
            logger.error("TRADING HALTED – %s", self.state.halt_reason)
