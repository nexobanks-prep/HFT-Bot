"""
Technical indicators used by the HFT strategy.

All functions accept plain Python lists or numpy arrays of floats and return
numpy arrays so they can be composed without external TA libraries.  The
implementations are intentionally transparent and testable.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _to_array(data: Sequence[float]) -> np.ndarray:
    return np.asarray(data, dtype=float)


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------

def ema(prices: Sequence[float], period: int) -> np.ndarray:
    """Exponential Moving Average.

    Returns an array of the same length as *prices*.
    The first ``period - 1`` values are ``NaN`` (insufficient history).
    """
    arr = _to_array(prices)
    if len(arr) < period:
        return np.full(len(arr), np.nan)

    result = np.full(len(arr), np.nan)
    k = 2.0 / (period + 1)
    # Seed with simple mean of first `period` values
    result[period - 1] = float(np.mean(arr[:period]))
    for i in range(period, len(arr)):
        result[i] = arr[i] * k + result[i - 1] * (1.0 - k)
    return result


def sma(prices: Sequence[float], period: int) -> np.ndarray:
    """Simple Moving Average."""
    arr = _to_array(prices)
    result = np.full(len(arr), np.nan)
    for i in range(period - 1, len(arr)):
        result[i] = float(np.mean(arr[i - period + 1 : i + 1]))
    return result


# ---------------------------------------------------------------------------
# Momentum / oscillators
# ---------------------------------------------------------------------------

def rsi(prices: Sequence[float], period: int = 14) -> np.ndarray:
    """Relative Strength Index (Wilder's smoothing).

    Returns values in [0, 100]; ``NaN`` for the first ``period`` bars.
    """
    arr = _to_array(prices)
    n = len(arr)
    result = np.full(n, np.nan)
    if n <= period:
        return result

    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0.0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return result


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------

def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> np.ndarray:
    """Average True Range (Wilder's smoothing)."""
    h = _to_array(highs)
    lo = _to_array(lows)
    c = _to_array(closes)
    n = len(c)
    result = np.full(n, np.nan)
    if n < 2:
        return result

    tr = np.empty(n)
    tr[0] = h[0] - lo[0]
    for i in range(1, n):
        tr[i] = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))

    if n < period:
        return result

    result[period - 1] = float(np.mean(tr[:period]))
    for i in range(period, n):
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
    return result


def bollinger_bands(
    prices: Sequence[float],
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bollinger Bands.

    Returns ``(upper, middle, lower)`` arrays.
    """
    arr = _to_array(prices)
    n = len(arr)
    middle = sma(arr, period)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        std = float(np.std(arr[i - period + 1 : i + 1], ddof=0))
        upper[i] = middle[i] + num_std * std
        lower[i] = middle[i] - num_std * std
    return upper, middle, lower


# ---------------------------------------------------------------------------
# Volume-weighted average price
# ---------------------------------------------------------------------------

def vwap(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
) -> np.ndarray:
    """Session VWAP (cumulative from bar 0 to current bar).

    Reset the input arrays at the start of each session to get a
    session-anchored VWAP.
    """
    h = _to_array(highs)
    lo = _to_array(lows)
    c = _to_array(closes)
    v = _to_array(volumes)

    typical = (h + lo + c) / 3.0
    cum_tp_vol = np.cumsum(typical * v)
    cum_vol = np.cumsum(v)
    result = np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)
    return result


# ---------------------------------------------------------------------------
# Convenience: latest-bar values
# ---------------------------------------------------------------------------

def latest(arr: np.ndarray) -> float:
    """Return the last non-NaN value (or NaN if all are NaN)."""
    valid = arr[~np.isnan(arr)]
    return float(valid[-1]) if len(valid) > 0 else math.nan


def prev(arr: np.ndarray, offset: int = 1) -> float:
    """Return the value ``offset`` bars before the last non-NaN value."""
    valid = arr[~np.isnan(arr)]
    idx = len(valid) - 1 - offset
    return float(valid[idx]) if idx >= 0 else math.nan
