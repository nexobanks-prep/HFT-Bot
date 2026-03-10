# Bookmark – HFT Trading Bot

A high-frequency scalping bot for **XAUUSD** (gold) and indices (**NAS100** / **NQ1**) with a high win-ratio strategy and a strict **5 % maximum drawdown** limit.

---

## Features

| Feature | Detail |
|---|---|
| **Instruments** | XAUUSD, NAS100, NQ1 (configurable) |
| **Strategy** | EMA crossover (8/21) + RSI(7) + VWAP + Bollinger Bands mean-reversion |
| **Max drawdown** | 5 % from equity peak – trading halts automatically |
| **Daily loss limit** | 2 % of start-of-day equity |
| **Position sizing** | ATR-based stop-loss + fixed fractional sizing (0.5 % risk/trade) |
| **Trailing stop** | ATR-trailing once trade is in profit |
| **Broker** | MetaTrader 5 (live/demo) or built-in back-test simulator |

---

## Project structure

```
Bookmark/
├── config/
│   └── settings.py          # All tunable parameters (instruments, risk, strategy)
├── src/
│   ├── indicators.py        # EMA, SMA, RSI, ATR, Bollinger Bands, VWAP
│   ├── strategy.py          # HFT scalping signal engine
│   ├── risk_manager.py      # Drawdown guard, position sizing, trailing stops
│   ├── broker.py            # MT5 adapter + back-test simulator
│   └── bot.py               # Main event loop (entry point)
├── tests/
│   ├── test_indicators.py
│   ├── test_risk_manager.py
│   ├── test_strategy.py
│   └── test_bot.py
└── requirements.txt
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
# For live trading (Windows only):
pip install MetaTrader5
```

### 2. Configure

Edit `config/settings.py` or set environment variables:

| Variable | Description |
|---|---|
| `BOT_MODE` | `backtest` (default), `demo`, or `live` |
| `MT5_LOGIN` | MT5 account number |
| `MT5_PASSWORD` | MT5 password |
| `MT5_SERVER` | MT5 broker server name |

### 3. Run

```bash
# Back-test / paper-trade (no broker connection needed)
python -m src.bot

# Live / demo (set env vars first)
BOT_MODE=demo MT5_LOGIN=12345 MT5_PASSWORD=secret MT5_SERVER=BrokerXYZ-Demo python -m src.bot
```

### 4. Run tests

```bash
pytest tests/ -v
```

---

## Strategy overview

The bot uses a **multi-condition scoring system** (0–1).  A trade fires only when the score
≥ `min_signal_score` (default 0.65).

**BUY conditions** (each scores 1/5):
1. Fast EMA > Slow EMA (micro-uptrend)
2. EMA bullish cross
3. RSI crosses up through oversold (30)
4. Close > session VWAP
5. Close ≤ lower Bollinger Band × 1.002 (mean-reversion entry)

**SELL conditions** are the mirror image.

### Risk management

* **Stop-loss** = entry ± ATR × 1.5
* **Take-profit** = entry ± ATR × 2.5  → risk:reward ≈ 1 : 1.67
* **Position size** is calculated so that hitting the stop-loss loses exactly 0.5 % of equity
* **Drawdown guard**: trading halts permanently (until manual restart) when peak-to-trough drawdown hits 5 %
* **Daily loss limit**: trading pauses for the day when intra-day loss hits 2 %

---

## Disclaimer

This software is provided for educational and research purposes only.
Trading financial instruments carries significant risk and you can lose more than your initial investment.
**Always test in a demo environment before going live.**  Past performance is not indicative of future results.
