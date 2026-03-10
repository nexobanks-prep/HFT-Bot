"""
Broker adapter – thin abstraction layer over MetaTrader 5 (live/demo)
and a built-in back-test simulator.

Usage
-----
Instantiate the correct adapter depending on ``BOT_MODE`` and call the
uniform interface ``place_order``, ``close_order``, ``get_tick``, etc.

Live trading requires the ``MetaTrader5`` Python package which is only
available on Windows.  The back-test adapter works on any platform and
is used by the test suite.
"""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------

@dataclass
class Tick:
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float
    time: float  # Unix timestamp


@dataclass
class OrderResult:
    success: bool
    ticket: int
    price: float
    message: str = ""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BrokerAdapter(ABC):
    """Uniform interface all broker adapters must implement."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection / load data.  Returns True on success."""

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully close the connection."""

    @abstractmethod
    def get_tick(self, symbol: str) -> Optional[Tick]:
        """Return the latest tick for *symbol*, or None if unavailable."""

    @abstractmethod
    def get_account_equity(self) -> float:
        """Return current account equity in account currency."""

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        direction: str,
        volume: float,
        stop_loss: float,
        take_profit: float,
        magic: int,
        comment: str,
    ) -> OrderResult:
        """Send a market order.  *direction* is 'buy' or 'sell'."""

    @abstractmethod
    def modify_sl(self, ticket: int, new_sl: float) -> bool:
        """Modify the stop-loss of an existing position."""

    @abstractmethod
    def close_order(self, ticket: int, volume: float) -> OrderResult:
        """Close (part of) an open position by ticket."""


# ---------------------------------------------------------------------------
# Back-test / paper-trade adapter
# ---------------------------------------------------------------------------

class BacktestAdapter(BrokerAdapter):
    """
    In-memory simulated broker.

    Prices move randomly around a configurable seed price so the bot can
    be exercised without a live connection.  Intended for unit tests and
    dry-run back-tests.
    """

    def __init__(
        self,
        initial_equity: float = 10_000.0,
        seed_prices: Optional[dict[str, float]] = None,
    ) -> None:
        self._equity = initial_equity
        self._seed = seed_prices or {
            "XAUUSD": 2300.0,
            "NAS100": 18_000.0,
            "NQ1": 18_000.0,
        }
        self._prices: dict[str, float] = dict(self._seed)
        self._ticket_counter = 1000
        self._open_orders: dict[int, dict] = {}
        self._connected = False

    # -- BrokerAdapter interface --

    def connect(self) -> bool:
        self._connected = True
        logger.info("BacktestAdapter connected (paper-trade mode).")
        return True

    def disconnect(self) -> None:
        self._connected = False
        logger.info("BacktestAdapter disconnected.")

    def get_tick(self, symbol: str) -> Optional[Tick]:
        if symbol not in self._prices:
            return None
        # Simulate a small random walk
        drift = self._prices[symbol] * random.uniform(-0.0003, 0.0003)
        self._prices[symbol] = max(0.01, self._prices[symbol] + drift)
        mid = self._prices[symbol]
        spread = mid * 0.0001  # 1 pip spread simulation
        return Tick(
            symbol=symbol,
            bid=mid - spread / 2,
            ask=mid + spread / 2,
            last=mid,
            volume=random.uniform(50, 500),
            time=time.time(),
        )

    def get_account_equity(self) -> float:
        # Update equity based on floating P&L of open orders
        floating = sum(self._floating_pnl(o) for o in self._open_orders.values())
        return self._equity + floating

    def place_order(
        self,
        symbol: str,
        direction: str,
        volume: float,
        stop_loss: float,
        take_profit: float,
        magic: int,
        comment: str,
    ) -> OrderResult:
        tick = self.get_tick(symbol)
        if tick is None:
            return OrderResult(success=False, ticket=0, price=0.0, message="No tick data")

        price = tick.ask if direction == "buy" else tick.bid
        ticket = self._ticket_counter
        self._ticket_counter += 1
        self._open_orders[ticket] = {
            "symbol": symbol,
            "direction": direction,
            "volume": volume,
            "entry_price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
        logger.info(
            "BacktestAdapter: order placed ticket=%d %s %s @ %.5f vol=%.2f",
            ticket, symbol, direction, price, volume,
        )
        return OrderResult(success=True, ticket=ticket, price=price)

    def modify_sl(self, ticket: int, new_sl: float) -> bool:
        if ticket not in self._open_orders:
            return False
        self._open_orders[ticket]["stop_loss"] = new_sl
        return True

    def close_order(self, ticket: int, volume: float) -> OrderResult:
        order = self._open_orders.pop(ticket, None)
        if order is None:
            return OrderResult(success=False, ticket=ticket, price=0.0, message="Ticket not found")

        tick = self.get_tick(order["symbol"])
        close_price = (tick.bid if order["direction"] == "buy" else tick.ask) if tick else order["entry_price"]

        pnl = self._calc_pnl(order, close_price)
        self._equity += pnl
        logger.info(
            "BacktestAdapter: order closed ticket=%d pnl=%.2f equity=%.2f",
            ticket, pnl, self._equity,
        )
        return OrderResult(success=True, ticket=ticket, price=close_price)

    # -- Internal helpers --

    def _floating_pnl(self, order: dict) -> float:
        tick = self._prices.get(order["symbol"], order["entry_price"])
        close_price = tick
        return self._calc_pnl(order, close_price)

    @staticmethod
    def _calc_pnl(order: dict, close_price: float) -> float:
        if order["direction"] == "buy":
            return (close_price - order["entry_price"]) * order["volume"]
        return (order["entry_price"] - close_price) * order["volume"]


# ---------------------------------------------------------------------------
# MetaTrader 5 live adapter
# ---------------------------------------------------------------------------

class MT5Adapter(BrokerAdapter):
    """
    Live/demo broker adapter using the MetaTrader5 Python package.

    The ``MetaTrader5`` library is only available on Windows and must be
    installed separately (``pip install MetaTrader5``).  This class will
    raise ``ImportError`` if the library is not present.
    """

    def __init__(self, login: int, password: str, server: str) -> None:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "MetaTrader5 package is not installed. "
                "Run: pip install MetaTrader5  (Windows only)"
            ) from exc
        self._mt5 = mt5
        self._login = login
        self._password = password
        self._server = server

    def connect(self) -> bool:
        ok = self._mt5.initialize(
            login=self._login,
            password=self._password,
            server=self._server,
        )
        if not ok:
            logger.error("MT5 connect failed: %s", self._mt5.last_error())
        return ok

    def disconnect(self) -> None:
        self._mt5.shutdown()

    def get_tick(self, symbol: str) -> Optional[Tick]:
        t = self._mt5.symbol_info_tick(symbol)
        if t is None:
            return None
        return Tick(
            symbol=symbol,
            bid=t.bid,
            ask=t.ask,
            last=t.last,
            volume=float(t.volume),
            time=float(t.time),
        )

    def get_account_equity(self) -> float:
        info = self._mt5.account_info()
        return float(info.equity) if info else 0.0

    def place_order(
        self,
        symbol: str,
        direction: str,
        volume: float,
        stop_loss: float,
        take_profit: float,
        magic: int,
        comment: str,
    ) -> OrderResult:
        order_type = self._mt5.ORDER_TYPE_BUY if direction == "buy" else self._mt5.ORDER_TYPE_SELL
        tick = self.get_tick(symbol)
        if tick is None:
            return OrderResult(success=False, ticket=0, price=0.0, message="No tick")
        price = tick.ask if direction == "buy" else tick.bid

        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "magic": magic,
            "comment": comment,
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }
        result = self._mt5.order_send(request)
        if result is None or result.retcode != self._mt5.TRADE_RETCODE_DONE:
            msg = f"order_send failed: retcode={getattr(result, 'retcode', 'N/A')}"
            logger.error(msg)
            return OrderResult(success=False, ticket=0, price=price, message=msg)

        return OrderResult(success=True, ticket=result.order, price=result.price)

    def modify_sl(self, ticket: int, new_sl: float) -> bool:
        request = {
            "action": self._mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": new_sl,
        }
        result = self._mt5.order_send(request)
        return result is not None and result.retcode == self._mt5.TRADE_RETCODE_DONE

    def close_order(self, ticket: int, volume: float) -> OrderResult:
        pos = self._mt5.positions_get(ticket=ticket)
        if not pos:
            return OrderResult(success=False, ticket=ticket, price=0.0, message="Position not found")
        p = pos[0]
        close_type = (
            self._mt5.ORDER_TYPE_SELL if p.type == self._mt5.ORDER_TYPE_BUY
            else self._mt5.ORDER_TYPE_BUY
        )
        tick = self.get_tick(p.symbol)
        price = (tick.bid if p.type == self._mt5.ORDER_TYPE_BUY else tick.ask) if tick else 0.0
        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "magic": p.magic,
            "comment": "close",
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }
        result = self._mt5.order_send(request)
        if result is None or result.retcode != self._mt5.TRADE_RETCODE_DONE:
            msg = f"close_order failed: retcode={getattr(result, 'retcode', 'N/A')}"
            logger.error(msg)
            return OrderResult(success=False, ticket=ticket, price=price, message=msg)
        return OrderResult(success=True, ticket=ticket, price=result.price)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_adapter(mode: str, **kwargs) -> BrokerAdapter:
    """
    Factory function.

    Parameters
    ----------
    mode : str
        'backtest', 'demo', or 'live'.
    **kwargs
        Passed to the appropriate adapter constructor.
    """
    if mode == "backtest":
        return BacktestAdapter(**kwargs)
    if mode in ("live", "demo"):
        return MT5Adapter(
            login=kwargs["login"],
            password=kwargs["password"],
            server=kwargs["server"],
        )
    raise ValueError(f"Unknown broker mode: {mode!r}")
