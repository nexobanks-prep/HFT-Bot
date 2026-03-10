"""
Unit tests for src/indicators.py
"""

import math
import numpy as np
import pytest

from src.indicators import (
    ema,
    sma,
    rsi,
    atr,
    bollinger_bands,
    vwap,
    latest,
    prev,
)


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------

class TestEMA:
    def test_length_preserved(self):
        prices = list(range(1, 21))
        result = ema(prices, period=5)
        assert len(result) == 20

    def test_first_values_are_nan(self):
        prices = list(range(1, 21))
        result = ema(prices, period=5)
        assert all(math.isnan(result[i]) for i in range(4))

    def test_seed_equals_sma(self):
        prices = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        result = ema(prices, period=5)
        expected_seed = np.mean(prices[:5])
        assert abs(result[4] - expected_seed) < 1e-9

    def test_trending_series(self):
        prices = list(range(1, 31))
        result = ema(prices, period=5)
        # EMA of an upward-trending series should be monotonically increasing after warmup
        valid = result[~np.isnan(result)]
        assert all(valid[i] <= valid[i + 1] for i in range(len(valid) - 1))

    def test_insufficient_data_returns_nans(self):
        result = ema([1.0, 2.0], period=10)
        assert all(math.isnan(v) for v in result)


# ---------------------------------------------------------------------------
# SMA
# ---------------------------------------------------------------------------

class TestSMA:
    def test_basic(self):
        prices = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = sma(prices, period=3)
        assert math.isnan(result[0])
        assert math.isnan(result[1])
        assert abs(result[2] - 2.0) < 1e-9
        assert abs(result[3] - 3.0) < 1e-9
        assert abs(result[4] - 4.0) < 1e-9


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

class TestRSI:
    def test_length_preserved(self):
        prices = list(range(1, 31))
        result = rsi(prices, period=14)
        assert len(result) == 30

    def test_warmup_nans(self):
        prices = list(range(1, 31))
        result = rsi(prices, period=14)
        # First period+1 values should be NaN (we need period deltas + 1 price)
        assert all(math.isnan(result[i]) for i in range(15))

    def test_all_up_gives_high_rsi(self):
        prices = [100.0 + i for i in range(30)]
        result = rsi(prices, period=14)
        valid = result[~np.isnan(result)]
        assert all(v > 50 for v in valid), "All gains should give RSI > 50"

    def test_all_down_gives_low_rsi(self):
        prices = [100.0 - i for i in range(30)]
        result = rsi(prices, period=14)
        valid = result[~np.isnan(result)]
        assert all(v < 50 for v in valid), "All losses should give RSI < 50"

    def test_range_0_to_100(self):
        import random
        random.seed(42)
        prices = [100.0 + random.uniform(-2, 2) for _ in range(50)]
        result = rsi(prices, period=14)
        valid = result[~np.isnan(result)]
        assert all(0.0 <= v <= 100.0 for v in valid)


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

class TestATR:
    def test_length_preserved(self):
        n = 30
        highs = [100.0 + i * 0.1 for i in range(n)]
        lows = [99.0 + i * 0.1 for i in range(n)]
        closes = [99.5 + i * 0.1 for i in range(n)]
        result = atr(highs, lows, closes, period=14)
        assert len(result) == n

    def test_constant_candles_gives_constant_atr(self):
        n = 30
        highs = [101.0] * n
        lows = [99.0] * n
        closes = [100.0] * n
        result = atr(highs, lows, closes, period=14)
        valid = result[~np.isnan(result)]
        # TR = high - low = 2.0 always → ATR should converge to 2.0
        assert all(abs(v - 2.0) < 1e-6 for v in valid)

    def test_non_negative(self):
        import random
        random.seed(7)
        closes = [100.0 + random.uniform(-1, 1) for _ in range(40)]
        highs = [c + random.uniform(0, 0.5) for c in closes]
        lows = [c - random.uniform(0, 0.5) for c in closes]
        result = atr(highs, lows, closes, period=14)
        valid = result[~np.isnan(result)]
        assert all(v >= 0 for v in valid)


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

class TestBollingerBands:
    def test_upper_gt_middle_gt_lower(self):
        prices = [100.0 + i * 0.1 for i in range(50)]
        upper, mid, lower = bollinger_bands(prices, period=20, num_std=2.0)
        valid_idx = [i for i in range(len(prices)) if not math.isnan(upper[i])]
        for i in valid_idx:
            assert upper[i] >= mid[i] >= lower[i]

    def test_constant_series_has_zero_bands(self):
        prices = [100.0] * 30
        upper, mid, lower = bollinger_bands(prices, period=20, num_std=2.0)
        valid_idx = [i for i in range(len(prices)) if not math.isnan(upper[i])]
        for i in valid_idx:
            # std = 0 → upper == middle == lower
            assert abs(upper[i] - mid[i]) < 1e-9
            assert abs(lower[i] - mid[i]) < 1e-9


# ---------------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------------

class TestVWAP:
    def test_equal_prices_and_volumes(self):
        n = 10
        highs = [100.0] * n
        lows = [100.0] * n
        closes = [100.0] * n
        volumes = [1000.0] * n
        result = vwap(highs, lows, closes, volumes)
        assert all(abs(v - 100.0) < 1e-9 for v in result)

    def test_increasing_prices_vwap_increases(self):
        n = 20
        prices = [100.0 + i for i in range(n)]
        highs = prices
        lows = prices
        closes = prices
        volumes = [100.0] * n
        result = vwap(highs, lows, closes, volumes)
        assert all(result[i] <= result[i + 1] for i in range(n - 1))


# ---------------------------------------------------------------------------
# latest / prev helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_latest_skips_nans(self):
        arr = np.array([np.nan, np.nan, 1.0, 2.0, 3.0])
        assert latest(arr) == 3.0

    def test_latest_all_nan(self):
        arr = np.array([np.nan, np.nan])
        assert math.isnan(latest(arr))

    def test_prev(self):
        arr = np.array([np.nan, 1.0, 2.0, 3.0, 4.0])
        assert prev(arr, offset=1) == 3.0
        assert prev(arr, offset=2) == 2.0

    def test_prev_out_of_range(self):
        arr = np.array([1.0, 2.0])
        assert math.isnan(prev(arr, offset=10))
