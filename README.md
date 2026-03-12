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
│   └── settings.py               # All tunable parameters (instruments, risk, strategy)
├── pinescripts/
│   ├── breaker_blocks.pine       # TradingView Pine Script v5 – Breaker Blocks indicator
│   └── supply_demand.pine        # TradingView Pine Script v5 – Supply & Demand Zones indicator
├── src/
│   ├── indicators.py             # EMA, SMA, RSI, ATR, Bollinger Bands, VWAP
│   ├── strategy.py               # HFT scalping signal engine
│   ├── risk_manager.py           # Drawdown guard, position sizing, trailing stops
│   ├── broker.py                 # MT5 adapter + back-test simulator
│   └── bot.py                    # Main event loop (entry point)
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

---

## Pine Script – Breaker Blocks indicator

`pinescripts/breaker_blocks.pine` is a fully open-source **TradingView Pine Script v5**
indicator that detects and draws **Breaker Blocks** using Smart Money Concepts (SMC).

### What is a Breaker Block?

A Breaker Block is a *failed* Order Block:

| Concept | Description |
|---|---|
| **Order Block (OB)** | The last opposing candle before a strong impulsive move that breaks a prior swing high/low |
| **Bullish Breaker** | A bearish OB that was "broken" upward (bullish BOS), then price revisits and closes back above the zone – former resistance turns into support |
| **Bearish Breaker** | A bullish OB that was "broken" downward (bearish BOS), then price revisits and closes back below the zone – former support turns into resistance |

### Detection logic

1. Swing highs / lows are identified with a configurable **Pivot Length** (default 10 bars).
2. A **Break of Structure (BOS)** is detected when the closing price crosses above a swing high (bullish BOS) or below a swing low (bearish BOS).
3. On each BOS the last opposing candle within a look-back window is stored as a pending **Order Block zone**.
4. When price closes back *through* the OB zone in the opposite direction the zone flips to a **Breaker Block** and a filled rectangle is drawn.
5. Boxes extend to the right until price **mitigates** (penetrates a configurable % of the zone height), at which point the box is closed.

### How to add the indicator in TradingView

1. Open TradingView and go to **Pine Editor** (`/` → *Open Pine Editor*).
2. Paste the contents of `pinescripts/breaker_blocks.pine`.
3. Click **Add to chart**.
4. Adjust the inputs in the *Settings* panel:

| Input | Default | Description |
|---|---|---|
| Pivot Length | 10 | Bars on each side to confirm a swing high/low |
| Order-Block Look-back | 5 | How many bars back to search for the OB candle |
| Mitigation Level (%) | 50 | Zone is invalidated when price penetrates this % of its height |
| Show Bullish / Bearish Breakers | true | Toggle each type |
| Extend Boxes to the Right | true | Keep extending until mitigated |
| Show Labels | true | Display zone labels on the chart |
| Colors | teal / red | Fully customisable fill and border colours |

---

## Pine Script – Supply & Demand Zones indicator

`pinescripts/supply_demand.pine` is a fully open-source **TradingView Pine Script v5**
indicator that detects and draws **Supply and Demand zones** using Smart Money Concepts (SMC).

### What are Supply and Demand Zones?

Supply and Demand zones are price areas where institutional order flow previously drove a
strong impulsive move away from a consolidation (base).  The base represents unfilled limit
orders that are likely to attract price on revisit.

| Zone Type | Pattern | Description |
|---|---|---|
| **Supply (Reversal)** | Rally-Base-Drop (RBD) | Price rallies into a base then drops impulsively — former support becomes resistance |
| **Demand (Reversal)** | Drop-Base-Rally (DBR) | Price drops into a base then rallies impulsively — former resistance becomes support |
| **Demand (Continuation)** | Rally-Base-Rally (RBR) | Price rallies, consolidates briefly, then continues higher — base is a continuation support level |
| **Supply (Continuation)** | Drop-Base-Drop (DBD) | Price drops, consolidates briefly, then continues lower — base is a continuation resistance level |

### Detection logic

1. The **ATR** (default period 14) is used to classify each candle:
   - **Impulse candle** – body ≥ `Impulse ATR Multiplier` × ATR (default 1.5×)
   - **Base candle** – body ≤ `Base ATR Multiplier` × ATR (default 0.5×)
2. A valid zone requires the sequence: **[impulse] → [1 – N base candles] → [impulse]**
   where N ≤ `Max Base Candles` (default 5).
3. The **zone** spans the combined high-to-low range of all base candles in the sequence.
4. The direction of the two impulse candles determines the zone type (RBD, DBR, RBR, DBD).
5. Zones extend to the right until price **mitigates** the zone (penetrates a configurable
   % of the zone height from the proximal edge).

### How to add the indicator in TradingView

1. Open TradingView and go to **Pine Editor** (`/` → *Open Pine Editor*).
2. Paste the contents of `pinescripts/supply_demand.pine`.
3. Click **Add to chart**.
4. Adjust the inputs in the *Settings* panel:

| Input | Default | Description |
|---|---|---|
| ATR Period | 14 | Period for Average True Range calculation |
| Impulse ATR Multiplier | 1.5 | Candle body must be ≥ this × ATR to be an impulse candle |
| Base ATR Multiplier | 0.5 | Candle body must be ≤ this × ATR to be a base (consolidation) candle |
| Max Base Candles | 5 | Maximum number of consecutive base candles allowed in a valid zone |
| Mitigation Level (%) | 50 | Zone is invalidated when price penetrates this % of its height from the proximal edge |
| Show Supply / Demand Zones | true | Toggle supply or demand zones independently |
| Show Reversal / Continuation Zones | true | Toggle RBD/DBR or RBR/DBD zones independently |
| Extend Boxes to the Right | true | Keep extending until mitigated |
| Show Labels | true | Display zone-type labels on the chart |
| Colors | red / green | Fully customisable fill and border colours |
