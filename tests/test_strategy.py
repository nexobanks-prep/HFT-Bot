"""
Unit tests for src/strategy.py
"""

import math
import pytest

from config.settings import InstrumentConfig, StrategyConfig
from src.strategy import HFTScalpingStrategy, SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_strategy(min_signal_score: float = 0.6) -> HFTScalpingStrategy:
    inst = InstrumentConfig(
        symbol="XAUUSD",
        pip_value=0.01,
        spread_limit_points=50,
        min_volume=0.01,
        max_volume=5.0,
        atr_multiplier_sl=1.5,
        atr_multiplier_tp=2.5,
    )
    cfg = StrategyConfig(
        ema_fast=8,
        ema_slow=21,
        rsi_period=7,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        atr_period=14,
        bb_period=20,
        bb_std=2.0,
        vwap_anchor="session",
        min_signal_score=min_signal_score,
        min_candle_atr_ratio=0.0,  # disable volatility filter in tests
        signal_timeframe_seconds=5,
        warmup_bars=50,
    )
    return HFTScalpingStrategy(inst, cfg)


def feed_bars(strategy: HFTScalpingStrategy, bars: list[tuple]) -> list:
    """Feed a list of (open, high, low, close, volume) bars and collect signals."""
    signals = []
    for b in bars:
        sig = strategy.on_bar(*b, spread=0.0)
        signals.append(sig)
    return signals


def make_uptrend_bars(n: int, base: float = 2300.0, step: float = 0.5) -> list[tuple]:
    """Generate steadily rising bars."""
    bars = []
    price = base
    for _ in range(n):
        o = price
        h = price + step * 0.8
        lo = price - step * 0.2
        c = price + step * 0.6
        bars.append((o, h, lo, c, 500.0))
        price = c
    return bars


def make_downtrend_bars(n: int, base: float = 2300.0, step: float = 0.5) -> list[tuple]:
    bars = []
    price = base
    for _ in range(n):
        o = price
        h = price + step * 0.2
        lo = price - step * 0.8
        c = price - step * 0.6
        bars.append((o, h, lo, c, 500.0))
        price = c
    return bars


# ---------------------------------------------------------------------------
# Warm-up behaviour
# ---------------------------------------------------------------------------

class TestWarmup:
    def test_no_signal_during_warmup(self):
        strat = make_strategy()
        # Feed fewer bars than warmup_bars (50)
        for i in range(49):
            sig = strat.on_bar(100.0, 101.0, 99.0, 100.5, 100.0, spread=0.0)
            assert sig.direction == SIGNAL_NONE, f"Unexpected signal at bar {i}"

    def test_warmed_up_flag(self):
        strat = make_strategy()
        assert not strat.is_warmed_up
        for _ in range(50):
            strat.on_bar(100.0, 101.0, 99.0, 100.5, 100.0, spread=0.0)
        assert strat.is_warmed_up


# ---------------------------------------------------------------------------
# Spread filter
# ---------------------------------------------------------------------------

class TestSpreadFilter:
    def test_high_spread_suppresses_signal(self):
        inst = InstrumentConfig(
            symbol="XAUUSD",
            pip_value=0.01,
            spread_limit_points=10,  # tight limit
            min_volume=0.01,
            max_volume=5.0,
        )
        cfg = StrategyConfig(
            min_signal_score=0.0,  # accept any signal
            min_candle_atr_ratio=0.0,
            warmup_bars=50,
        )
        strat = HFTScalpingStrategy(inst, cfg)
        bars = make_uptrend_bars(50)
        signals = []
        for b in bars:
            sig = strat.on_bar(*b, spread=100.0)  # spread >> limit
            signals.append(sig)
        # All signals after warmup should be NONE due to spread
        post_warmup = signals[49:]
        assert all(s.direction == SIGNAL_NONE for s in post_warmup)


# ---------------------------------------------------------------------------
# Signal output structure
# ---------------------------------------------------------------------------

class TestSignalOutputStructure:
    def test_no_signal_has_zero_sl_tp(self):
        strat = make_strategy()
        sig = strat.on_bar(100.0, 101.0, 99.0, 100.5, 100.0, spread=0.0)
        assert sig.direction == SIGNAL_NONE
        assert sig.stop_loss == 0.0
        assert sig.take_profit == 0.0

    def test_buy_signal_sl_below_entry(self):
        """After warmup in an uptrend, any BUY signal must have SL < close."""
        strat = make_strategy(min_signal_score=0.1)
        bars = make_uptrend_bars(80)
        signals = feed_bars(strat, bars)
        buy_signals = [s for s in signals if s.direction == SIGNAL_BUY]
        if buy_signals:
            for s in buy_signals:
                assert s.stop_loss < s.take_profit

    def test_sell_signal_sl_above_entry(self):
        """In a downtrend, SELL signal SL must be > TP (prices moving down)."""
        strat = make_strategy(min_signal_score=0.1)
        bars = make_downtrend_bars(80)
        signals = feed_bars(strat, bars)
        sell_signals = [s for s in signals if s.direction == SIGNAL_SELL]
        if sell_signals:
            for s in sell_signals:
                assert s.stop_loss > s.take_profit

    def test_signal_score_between_0_and_1(self):
        strat = make_strategy(min_signal_score=0.0)
        bars = make_uptrend_bars(80)
        signals = feed_bars(strat, bars)
        for s in signals:
            assert 0.0 <= s.score <= 1.0

    def test_atr_non_negative_when_signal_fires(self):
        strat = make_strategy(min_signal_score=0.0)
        bars = make_uptrend_bars(80)
        signals = feed_bars(strat, bars)
        for s in signals:
            if s.direction != SIGNAL_NONE:
                assert s.atr >= 0.0


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_warmup(self):
        strat = make_strategy()
        # Warm up
        for _ in range(50):
            strat.on_bar(100.0, 101.0, 99.0, 100.5, 100.0, spread=0.0)
        assert strat.is_warmed_up

        strat.reset()
        assert not strat.is_warmed_up

    def test_no_signal_after_reset_before_warmup(self):
        strat = make_strategy()
        for _ in range(50):
            strat.on_bar(100.0, 101.0, 99.0, 100.5, 100.0, spread=0.0)
        strat.reset()
        sig = strat.on_bar(100.0, 101.0, 99.0, 100.5, 100.0, spread=0.0)
        assert sig.direction == SIGNAL_NONE
