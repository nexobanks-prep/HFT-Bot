"""
Central configuration for the HFT trading bot.

Supports XAUUSD (gold) and index instruments (NAS100 / NQ1).
Tweak the values here before going live – never hard-code secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


# ---------------------------------------------------------------------------
# Instrument definitions
# ---------------------------------------------------------------------------

@dataclass
class InstrumentConfig:
    """Per-instrument parameters."""

    symbol: str
    """Broker symbol name, e.g. 'XAUUSD' or 'NAS100'."""

    pip_value: float
    """Value of one pip/tick in quote currency (used for P&L estimates)."""

    spread_limit_points: float
    """Maximum tolerated spread (in points) before a trade is skipped."""

    min_volume: float
    """Minimum lot/contract size allowed by the broker."""

    max_volume: float
    """Maximum lot/contract size per single order."""

    atr_multiplier_sl: float = 1.5
    """Stop-loss distance = ATR * this multiplier."""

    atr_multiplier_tp: float = 2.5
    """Take-profit distance = ATR * this multiplier (TP/SL ≥ 1.5 for high WR)."""


INSTRUMENTS: List[InstrumentConfig] = [
    InstrumentConfig(
        symbol="XAUUSD",
        pip_value=0.01,
        spread_limit_points=30,   # ~30 pips max spread
        min_volume=0.01,
        max_volume=5.0,
        atr_multiplier_sl=1.5,
        atr_multiplier_tp=2.5,
    ),
    InstrumentConfig(
        symbol="NAS100",
        pip_value=1.0,
        spread_limit_points=5,    # index points
        min_volume=0.01,
        max_volume=5.0,
        atr_multiplier_sl=1.5,
        atr_multiplier_tp=2.5,
    ),
    InstrumentConfig(
        symbol="NQ1",
        pip_value=5.0,            # NQ futures: $5 per 0.25-pt tick = $20/pt
        spread_limit_points=2,
        min_volume=1,
        max_volume=20,
        atr_multiplier_sl=1.5,
        atr_multiplier_tp=2.5,
    ),
]


# ---------------------------------------------------------------------------
# Risk & money-management parameters
# ---------------------------------------------------------------------------

@dataclass
class RiskConfig:
    """Global risk-management settings."""

    max_drawdown_pct: float = 5.0
    """Maximum allowed portfolio drawdown in percent before trading halts."""

    risk_per_trade_pct: float = 0.5
    """Percentage of current equity risked on a single trade (0.5 %)."""

    max_open_positions: int = 3
    """Hard cap on simultaneously open positions across all instruments."""

    daily_loss_limit_pct: float = 2.0
    """Intra-day loss limit (% of start-of-day equity). Bot pauses when hit."""

    trailing_stop_enabled: bool = True
    """Activate trailing stop once position is in profit by 1× ATR."""

    trailing_stop_atr_multiplier: float = 1.0
    """Trail by this many ATRs behind the best price reached."""


RISK = RiskConfig()


# ---------------------------------------------------------------------------
# Strategy / indicator parameters
# ---------------------------------------------------------------------------

@dataclass
class StrategyConfig:
    """HFT scalping strategy hyper-parameters."""

    # EMA settings
    ema_fast: int = 8
    ema_slow: int = 21

    # RSI settings
    rsi_period: int = 7
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0

    # ATR (volatility)
    atr_period: int = 14

    # Bollinger Bands
    bb_period: int = 20
    bb_std: float = 2.0

    # VWAP anchor: "session" (reset each session) or "daily"
    vwap_anchor: str = "session"

    # Minimum signal strength (0–1) required to fire an order
    min_signal_score: float = 0.65

    # Minimum candle range as a multiple of ATR to avoid ranging markets
    min_candle_atr_ratio: float = 0.3

    # Timeframe for strategy signals (in seconds; 5 = M1 equivalent for tick data)
    signal_timeframe_seconds: int = 5

    # Number of bars used for warm-up before trading starts
    warmup_bars: int = 50


STRATEGY = StrategyConfig()


# ---------------------------------------------------------------------------
# Broker / connection settings  (read from env vars for safety)
# ---------------------------------------------------------------------------

@dataclass
class BrokerConfig:
    """Broker connection parameters (MT5 or simulated back-test mode)."""

    mode: str = os.getenv("BOT_MODE", "backtest")
    """'live', 'demo', or 'backtest'."""

    mt5_login: int = int(os.getenv("MT5_LOGIN", "0"))
    mt5_password: str = os.getenv("MT5_PASSWORD", "")
    mt5_server: str = os.getenv("MT5_SERVER", "")

    magic_number: int = 20240310
    """EA magic number – used to distinguish bot orders from manual ones."""

    order_comment: str = "HFT_BookmarkBot"


BROKER = BrokerConfig()
