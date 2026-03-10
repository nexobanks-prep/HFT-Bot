"""
HFT Scalping Strategy – high win-ratio signal generation.

Signal logic
------------
A **BUY** signal requires ALL of:
  1. Fast EMA > Slow EMA  (uptrend on micro-timeframe)
  2. RSI crosses up through oversold threshold (momentum reversal)
  3. Close > VWAP          (price is above fair value)
  4. Close inside or near lower Bollinger Band (mean-reversion entry)
  5. ATR > min_candle_atr_ratio × ATR (sufficient volatility)
  6. Spread ≤ spread_limit_points (liquidity ok)

A **SELL** signal is the mirror image.

The score (0–1) is the fraction of sub-conditions that are met; trades are
only taken when score ≥ min_signal_score (default 0.65).

Returns
-------
``SignalResult`` dataclass with direction, score, suggested SL and TP.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional, Sequence

from src.indicators import (
    atr as calc_atr,
    bollinger_bands,
    ema as calc_ema,
    latest,
    prev,
    rsi as calc_rsi,
    vwap as calc_vwap,
)
from config.settings import StrategyConfig, InstrumentConfig

logger = logging.getLogger(__name__)

SIGNAL_NONE = "none"
SIGNAL_BUY = "buy"
SIGNAL_SELL = "sell"


@dataclass
class SignalResult:
    """Output of the strategy for a single bar."""

    direction: str          # SIGNAL_BUY | SIGNAL_SELL | SIGNAL_NONE
    score: float            # 0.0 – 1.0
    stop_loss: float        # absolute price
    take_profit: float      # absolute price
    atr: float              # current ATR value (used by risk manager)
    reason: str = ""        # human-readable explanation


class HFTScalpingStrategy:
    """
    Stateful HFT scalping strategy.

    Feed price bars sequentially via ``on_bar()``.  The object accumulates
    rolling buffers so that indicators can be computed incrementally.

    Parameters
    ----------
    instrument : InstrumentConfig
        Instrument-specific parameters (spread limit, ATR multipliers …).
    strategy_cfg : StrategyConfig
        Indicator and signal hyper-parameters.
    """

    def __init__(
        self,
        instrument: InstrumentConfig,
        strategy_cfg: StrategyConfig,
    ) -> None:
        self._inst = instrument
        self._cfg = strategy_cfg

        # Rolling bar data buffers
        self._opens: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._closes: list[float] = []
        self._volumes: list[float] = []

        self._bar_count = 0
        self._warmed_up = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_warmed_up(self) -> bool:
        return self._warmed_up

    def on_bar(
        self,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        spread: float = 0.0,
    ) -> SignalResult:
        """
        Process a new completed bar and return a trading signal.

        Parameters
        ----------
        open_ / high / low / close : float
            OHLC prices for this bar.
        volume : float
            Tick / contract volume for this bar.
        spread : float
            Current bid-ask spread in points.

        Returns
        -------
        SignalResult
        """
        self._opens.append(open_)
        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)
        self._volumes.append(max(volume, 1e-9))  # guard against zero volume
        self._bar_count += 1

        if self._bar_count < self._cfg.warmup_bars:
            return self._no_signal("warming up")

        self._warmed_up = True
        return self._evaluate(close, spread)

    def reset(self) -> None:
        """Clear all internal buffers (e.g. at session boundary)."""
        self._opens.clear()
        self._highs.clear()
        self._lows.clear()
        self._closes.clear()
        self._volumes.clear()
        self._bar_count = 0
        self._warmed_up = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evaluate(self, current_close: float, spread: float) -> SignalResult:
        closes = self._closes
        highs = self._highs
        lows = self._lows
        volumes = self._volumes

        # ---- Compute indicators ----------------------------------------
        ema_fast_arr = calc_ema(closes, self._cfg.ema_fast)
        ema_slow_arr = calc_ema(closes, self._cfg.ema_slow)
        rsi_arr = calc_rsi(closes, self._cfg.rsi_period)
        atr_arr = calc_atr(highs, lows, closes, self._cfg.atr_period)
        bb_upper, _bb_mid, bb_lower = bollinger_bands(
            closes, self._cfg.bb_period, self._cfg.bb_std
        )
        vwap_arr = calc_vwap(highs, lows, closes, volumes)

        ema_f = latest(ema_fast_arr)
        ema_s = latest(ema_slow_arr)
        ema_f_prev = prev(ema_fast_arr)
        ema_s_prev = prev(ema_slow_arr)
        rsi_now = latest(rsi_arr)
        rsi_prev = prev(rsi_arr)
        atr_now = latest(atr_arr)
        bb_lo = latest(bb_lower)
        bb_hi = latest(bb_upper)
        vwap_now = latest(vwap_arr)

        # Guard: skip if any indicator is not yet ready
        if any(math.isnan(x) for x in [ema_f, ema_s, rsi_now, atr_now, bb_lo, bb_hi, vwap_now]):
            return self._no_signal("indicator not ready")

        # ---- Spread filter -----------------------------------------------
        if spread > self._inst.spread_limit_points:
            return self._no_signal(f"spread {spread} > limit {self._inst.spread_limit_points}")

        # ---- Volatility filter -------------------------------------------
        candle_range = self._highs[-1] - self._lows[-1]
        if atr_now > 0 and (candle_range / atr_now) < self._cfg.min_candle_atr_ratio:
            return self._no_signal("low volatility / ranging market")

        # ---- Sub-conditions (each worth 1/N of the score) ----------------
        # BUY sub-conditions
        buy_conditions = {
            "ema_uptrend": ema_f > ema_s,
            "ema_cross_up": ema_f > ema_s and ema_f_prev <= ema_s_prev,
            "rsi_oversold_cross": rsi_prev < self._cfg.rsi_oversold <= rsi_now,
            "price_above_vwap": current_close > vwap_now,
            "price_near_bb_lower": current_close <= bb_lo * 1.002,  # within 0.2 % of lower band
        }
        buy_score = sum(buy_conditions.values()) / len(buy_conditions)

        # SELL sub-conditions
        sell_conditions = {
            "ema_downtrend": ema_f < ema_s,
            "ema_cross_down": ema_f < ema_s and ema_f_prev >= ema_s_prev,
            "rsi_overbought_cross": rsi_prev > self._cfg.rsi_overbought >= rsi_now,
            "price_below_vwap": current_close < vwap_now,
            "price_near_bb_upper": current_close >= bb_hi * 0.998,
        }
        sell_score = sum(sell_conditions.values()) / len(sell_conditions)

        sl_dist = atr_now * self._inst.atr_multiplier_sl
        tp_dist = atr_now * self._inst.atr_multiplier_tp

        if buy_score >= self._cfg.min_signal_score and buy_score > sell_score:
            sl = current_close - sl_dist
            tp = current_close + tp_dist
            reason = ", ".join(k for k, v in buy_conditions.items() if v)
            logger.debug(
                "[%s] BUY signal score=%.2f SL=%.5f TP=%.5f (%s)",
                self._inst.symbol, buy_score, sl, tp, reason,
            )
            return SignalResult(
                direction=SIGNAL_BUY,
                score=buy_score,
                stop_loss=sl,
                take_profit=tp,
                atr=atr_now,
                reason=reason,
            )

        if sell_score >= self._cfg.min_signal_score and sell_score > buy_score:
            sl = current_close + sl_dist
            tp = current_close - tp_dist
            reason = ", ".join(k for k, v in sell_conditions.items() if v)
            logger.debug(
                "[%s] SELL signal score=%.2f SL=%.5f TP=%.5f (%s)",
                self._inst.symbol, sell_score, sl, tp, reason,
            )
            return SignalResult(
                direction=SIGNAL_SELL,
                score=sell_score,
                stop_loss=sl,
                take_profit=tp,
                atr=atr_now,
                reason=reason,
            )

        return self._no_signal(
            f"buy_score={buy_score:.2f} sell_score={sell_score:.2f} below threshold"
        )

    @staticmethod
    def _no_signal(reason: str = "") -> SignalResult:
        return SignalResult(
            direction=SIGNAL_NONE,
            score=0.0,
            stop_loss=0.0,
            take_profit=0.0,
            atr=0.0,
            reason=reason,
        )
