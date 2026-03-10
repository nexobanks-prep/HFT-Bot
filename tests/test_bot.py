"""
Integration tests for src/bot.py – uses the BacktestAdapter so no live
connection is needed.
"""

import time
import pytest

from config.settings import INSTRUMENTS, STRATEGY
from src.bot import HFTBot, RunningBar
from src.broker import BacktestAdapter
from src.risk_manager import RiskManager


# ---------------------------------------------------------------------------
# RunningBar tests
# ---------------------------------------------------------------------------

class TestRunningBar:
    def test_bar_closes_after_timeframe(self):
        bar = RunningBar(symbol="XAUUSD", timeframe_seconds=5)
        t0 = 1000.0
        closed = bar.update(2300.0, 100, t0)
        assert not closed  # just opened

        closed = bar.update(2305.0, 50, t0 + 4.9)
        assert not closed

        closed = bar.update(2310.0, 50, t0 + 5.1)
        assert closed

    def test_bar_ohlcv_values(self):
        bar = RunningBar(symbol="XAUUSD", timeframe_seconds=10)
        t0 = 0.0
        bar.update(2300.0, 100, t0)
        bar.update(2310.0, 200, t0 + 1)
        bar.update(2295.0, 150, t0 + 2)
        bar.update(2308.0, 50, t0 + 3)

        assert bar.open == 2300.0
        assert abs(bar.high - 2310.0) < 1e-9
        assert abs(bar.low - 2295.0) < 1e-9
        assert abs(bar.close - 2308.0) < 1e-9
        assert abs(bar.volume - 500.0) < 1e-9

    def test_reset_reinitialises(self):
        bar = RunningBar(symbol="XAUUSD", timeframe_seconds=5)
        t0 = 0.0
        bar.update(2300.0, 100, t0)
        bar.reset(2400.0, 50, t0 + 6)
        assert bar.open == 2400.0
        assert bar.volume == 50


# ---------------------------------------------------------------------------
# HFTBot integration (back-test mode)
# ---------------------------------------------------------------------------

class TestHFTBotBacktest:
    def _make_bot(self, initial_equity: float = 10_000.0) -> tuple[HFTBot, BacktestAdapter]:
        broker = BacktestAdapter(initial_equity=initial_equity)
        risk_mgr = RiskManager(
            initial_equity=initial_equity,
            max_drawdown_pct=5.0,
            risk_per_trade_pct=0.5,
            max_open_positions=3,
            daily_loss_limit_pct=2.0,
            trailing_stop_enabled=True,
            trailing_stop_atr_multiplier=1.0,
            min_volume=0.01,
            max_volume=5.0,
        )
        bot = HFTBot(
            broker=broker,
            instruments=INSTRUMENTS,
            risk_manager=risk_mgr,
            strategy_cfg=STRATEGY,
        )
        return bot, broker

    def test_broker_connects(self):
        bot, broker = self._make_bot()
        assert broker.connect()
        broker.disconnect()

    def test_bot_initialises_all_instruments(self):
        bot, _ = self._make_bot()
        for inst in INSTRUMENTS:
            assert inst.symbol in bot._strategies
            assert inst.symbol in bot._bars

    def test_drawdown_halts_trading(self):
        bot, _ = self._make_bot(initial_equity=10_000.0)
        # Simulate 10% loss → should trigger 5% DD halt
        bot._risk.update_equity(8_500.0)
        assert bot._risk.state.trading_halted

    def test_equity_never_negative_in_simulation(self):
        """Run a few tick cycles and verify equity stays positive."""
        bot, broker = self._make_bot(initial_equity=10_000.0)
        broker.connect()
        # Simulate 20 tick cycles manually
        for _ in range(20):
            equity = broker.get_account_equity()
            bot._risk.update_equity(equity)
            for symbol, inst in bot._instruments.items():
                bot._process_symbol(symbol, inst)
        assert broker.get_account_equity() > 0
        broker.disconnect()

    def test_no_trade_when_halted(self):
        bot, broker = self._make_bot(initial_equity=10_000.0)
        broker.connect()
        # Force halt
        bot._risk.state.trading_halted = True
        bot._risk.state.halt_reason = "test halt"
        initial_positions = len(bot._risk.state.open_positions)
        # Run several ticks – no trades should be opened
        for _ in range(10):
            for symbol, inst in bot._instruments.items():
                bot._process_symbol(symbol, inst)
        assert len(bot._risk.state.open_positions) == initial_positions
        broker.disconnect()
