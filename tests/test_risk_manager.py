"""
Unit tests for src/risk_manager.py
"""

import pytest
from src.risk_manager import Position, RiskManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_rm(**kwargs) -> RiskManager:
    defaults = dict(
        initial_equity=10_000.0,
        max_drawdown_pct=5.0,
        risk_per_trade_pct=0.5,
        max_open_positions=3,
        daily_loss_limit_pct=2.0,
        trailing_stop_enabled=True,
        trailing_stop_atr_multiplier=1.0,
        min_volume=0.01,
        max_volume=5.0,
    )
    defaults.update(kwargs)
    return RiskManager(**defaults)


def make_position(ticket=1, symbol="XAUUSD", direction="buy",
                  entry=2300.0, sl=2290.0, tp=2320.0, vol=0.1, atr=10.0) -> Position:
    return Position(
        ticket=ticket,
        symbol=symbol,
        direction=direction,
        entry_price=entry,
        volume=vol,
        stop_loss=sl,
        take_profit=tp,
        atr_at_entry=atr,
    )


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestInit:
    def test_valid(self):
        rm = make_rm()
        assert rm.state.current_equity == 10_000.0
        assert rm.state.peak_equity == 10_000.0

    def test_zero_equity_raises(self):
        with pytest.raises(ValueError, match="initial_equity"):
            RiskManager(initial_equity=0)

    def test_invalid_drawdown_raises(self):
        with pytest.raises(ValueError, match="max_drawdown_pct"):
            RiskManager(initial_equity=1000, max_drawdown_pct=0)

    def test_invalid_risk_pct_raises(self):
        with pytest.raises(ValueError, match="risk_per_trade_pct"):
            RiskManager(initial_equity=1000, risk_per_trade_pct=0)


# ---------------------------------------------------------------------------
# Drawdown enforcement
# ---------------------------------------------------------------------------

class TestDrawdown:
    def test_no_halt_before_limit(self):
        rm = make_rm(initial_equity=10_000.0, max_drawdown_pct=5.0)
        # 4.9 % drawdown – should NOT halt
        rm.update_equity(9_510.0)
        assert not rm.state.trading_halted

    def test_halt_at_limit(self):
        rm = make_rm(initial_equity=10_000.0, max_drawdown_pct=5.0)
        # 5 % drawdown – MUST halt
        rm.update_equity(9_500.0)
        assert rm.state.trading_halted

    def test_halt_beyond_limit(self):
        rm = make_rm(initial_equity=10_000.0, max_drawdown_pct=5.0)
        rm.update_equity(8_000.0)
        assert rm.state.trading_halted

    def test_peak_updates_on_new_high(self):
        rm = make_rm(initial_equity=10_000.0)
        rm.update_equity(11_000.0)
        assert rm.state.peak_equity == 11_000.0

    def test_drawdown_pct_calculation(self):
        rm = make_rm(initial_equity=10_000.0)
        rm.update_equity(9_500.0)
        assert abs(rm.current_drawdown_pct() - 5.0) < 1e-6

    def test_can_open_trade_halted(self):
        rm = make_rm(initial_equity=10_000.0, max_drawdown_pct=5.0)
        rm.update_equity(9_000.0)  # 10% DD → halted
        assert not rm.can_open_trade()

    def test_can_open_trade_ok(self):
        rm = make_rm(initial_equity=10_000.0)
        assert rm.can_open_trade()


# ---------------------------------------------------------------------------
# Position size
# ---------------------------------------------------------------------------

class TestPositionSize:
    def test_basic_sizing(self):
        rm = make_rm(
            initial_equity=10_000.0,
            risk_per_trade_pct=1.0,  # risk $100
            min_volume=0.01,
            max_volume=5.0,
        )
        # SL distance = 10 pts, pip_value = 1.0 → volume = 100 / (10 * 1) = 10 → clipped to 5.0
        vol = rm.position_size(entry_price=100.0, stop_loss_price=90.0, pip_value=1.0)
        assert vol == 5.0  # clipped to max

    def test_minimum_volume_floor(self):
        rm = make_rm(
            initial_equity=10_000.0,
            risk_per_trade_pct=0.01,  # tiny risk → tiny volume
            min_volume=0.01,
            max_volume=5.0,
        )
        vol = rm.position_size(entry_price=2300.0, stop_loss_price=2200.0, pip_value=0.01)
        assert vol >= 0.01

    def test_zero_sl_distance_returns_min(self):
        rm = make_rm()
        vol = rm.position_size(entry_price=100.0, stop_loss_price=100.0, pip_value=1.0)
        assert vol == rm._min_vol


# ---------------------------------------------------------------------------
# Open / close positions
# ---------------------------------------------------------------------------

class TestPositionLifecycle:
    def test_register_and_close_buy(self):
        rm = make_rm(initial_equity=10_000.0)
        pos = make_position(ticket=42, direction="buy", entry=2300.0, vol=1.0)
        rm.register_open_position(pos)
        assert 42 in rm.state.open_positions

        pnl = rm.close_position(ticket=42, close_price=2310.0)
        assert abs(pnl - 10.0) < 1e-6  # (2310 - 2300) * 1.0
        assert 42 not in rm.state.open_positions

    def test_register_and_close_sell(self):
        rm = make_rm(initial_equity=10_000.0)
        pos = make_position(ticket=99, direction="sell", entry=2300.0, vol=1.0)
        rm.register_open_position(pos)

        pnl = rm.close_position(ticket=99, close_price=2290.0)
        assert abs(pnl - 10.0) < 1e-6  # (2300 - 2290) * 1.0

    def test_close_loss_updates_daily_pnl(self):
        rm = make_rm(initial_equity=10_000.0)
        pos = make_position(ticket=1, direction="buy", entry=2300.0, vol=1.0)
        rm.register_open_position(pos)
        rm.close_position(ticket=1, close_price=2280.0)  # -20 pnl
        assert rm.state.daily_realized_pnl < 0

    def test_max_positions_cap(self):
        rm = make_rm(initial_equity=10_000.0, max_open_positions=2)
        for i in range(2):
            pos = make_position(ticket=i)
            rm.register_open_position(pos)
        assert not rm.can_open_trade()

    def test_close_unknown_ticket_returns_zero(self):
        rm = make_rm()
        pnl = rm.close_position(ticket=9999, close_price=100.0)
        assert pnl == 0.0


# ---------------------------------------------------------------------------
# Daily loss limit
# ---------------------------------------------------------------------------

class TestDailyLossLimit:
    def test_daily_halt_on_loss(self):
        rm = make_rm(
            initial_equity=10_000.0,
            daily_loss_limit_pct=2.0,
        )
        pos = make_position(ticket=1, direction="buy", entry=2300.0, vol=1.0)
        rm.register_open_position(pos)
        # Loss of $250 = 2.5 % of $10 000 → exceeds 2% daily limit
        rm.close_position(ticket=1, close_price=2050.0)
        assert rm.state.trading_halted

    def test_new_day_lifts_daily_halt(self):
        rm = make_rm(initial_equity=10_000.0, daily_loss_limit_pct=1.0)
        pos = make_position(ticket=1, direction="buy", entry=2300.0, vol=1.0)
        rm.register_open_position(pos)
        rm.close_position(ticket=1, close_price=2150.0)  # big loss → halt
        assert rm.state.trading_halted

        rm.new_day(equity=10_000.0)
        assert not rm.state.trading_halted


# ---------------------------------------------------------------------------
# Trailing stop
# ---------------------------------------------------------------------------

class TestTrailingStop:
    def test_trailing_stop_moves_up_for_buy(self):
        rm = make_rm(trailing_stop_enabled=True, trailing_stop_atr_multiplier=1.0)
        pos = make_position(
            ticket=1, direction="buy", entry=2300.0, sl=2290.0, atr=10.0
        )
        rm.register_open_position(pos)

        # Price moves up: trail_dist = 10 → new_sl at 2320 - 10 = 2310
        new_sl = rm.get_trailing_stop(pos, current_price=2320.0)
        assert new_sl is not None
        assert abs(new_sl - 2310.0) < 1e-6

    def test_trailing_stop_moves_down_for_sell(self):
        rm = make_rm(trailing_stop_enabled=True, trailing_stop_atr_multiplier=1.0)
        pos = make_position(
            ticket=2, direction="sell", entry=2300.0, sl=2310.0, atr=10.0
        )
        rm.register_open_position(pos)

        new_sl = rm.get_trailing_stop(pos, current_price=2280.0)
        assert new_sl is not None
        assert abs(new_sl - 2290.0) < 1e-6

    def test_no_trailing_stop_when_disabled(self):
        rm = make_rm(trailing_stop_enabled=False)
        pos = make_position(ticket=3, direction="buy", entry=2300.0, sl=2290.0, atr=10.0)
        rm.register_open_position(pos)
        assert rm.get_trailing_stop(pos, current_price=2350.0) is None

    def test_trailing_stop_not_lowered_for_buy(self):
        """Trailing stop should never move against the trade direction."""
        rm = make_rm(trailing_stop_enabled=True, trailing_stop_atr_multiplier=1.0)
        pos = make_position(
            ticket=4, direction="buy", entry=2300.0, sl=2295.0, atr=10.0
        )
        # Price did not move above entry – no update expected
        result = rm.get_trailing_stop(pos, current_price=2299.0)
        assert result is None
