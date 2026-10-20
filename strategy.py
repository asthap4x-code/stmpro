
# strategy.py
# STM Pro Bot — Seven Rule-Based Strategies
# Signal analysis only. No automatic trading.

import math
import numpy as np
import pandas as pd


MIN_CANDLES = 220
MIN_PAYOUT_PERCENT = 80.0
DOJI_BODY_RATIO = 0.10
LARGE_WICK_RATIO = 0.60


def normalize_candle(candle):
    """Normalize common PyQuotex OHLC candle dictionaries."""
    try:
        if not isinstance(candle, dict):
            return None

        ts = candle.get("timestamp", candle.get("time", candle.get("from")))
        o = candle.get("open", candle.get("Open"))
        h = candle.get("high", candle.get("max", candle.get("High")))
        l = candle.get("low", candle.get("min", candle.get("Low")))
        c = candle.get("close", candle.get("Close"))

        if None in (ts, o, h, l, c):
            return None

        ts = int(float(ts))
        o, h, l, c = map(float, (o, h, l, c))

        if not all(math.isfinite(v) for v in (o, h, l, c)):
            return None
        if min(o, h, l, c) <= 0:
            return None
        if h < max(o, c, l) or l > min(o, c, h):
            return None

        return {
            "timestamp": ts,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        }
    except (TypeError, ValueError, OverflowError):
        return None


def candle_color(candle):
    c = normalize_candle(candle)
    if not c:
        return "DOJI"
    if c["close"] > c["open"]:
        return "GREEN"
    if c["close"] < c["open"]:
        return "RED"
    return "DOJI"


def _make_frame(candles):
    rows = [normalize_candle(c) for c in candles]
    rows = [c for c in rows if c is not None]

    if not rows:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close"]
        )

    df = pd.DataFrame(rows)
    df = df.drop_duplicates("timestamp", keep="last")
    return df.sort_values("timestamp").reset_index(drop=True)


def _indicators(df):
    d = df.copy()
    o, h, l, c = d["open"], d["high"], d["low"], d["close"]

    d["ema20"] = c.ewm(span=20, adjust=False).mean()
    d["ema50"] = c.ewm(span=50, adjust=False).mean()
    d["ema100"] = c.ewm(span=100, adjust=False).mean()
    d["ema200"] = c.ewm(span=200, adjust=False).mean()

    previous_close = c.shift(1)
    tr = pd.concat(
        [h - l, (h - previous_close).abs(), (l - previous_close).abs()],
        axis=1,
    ).max(axis=1)

    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d["atr_median50"] = d["atr"].rolling(50).median()

    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = up_move.where(
        (up_move > down_move) & (up_move > 0), 0.0
    )
    minus_dm = down_move.where(
        (down_move > up_move) & (down_move > 0), 0.0
    )

    atr_safe = d["atr"].replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_safe
    dx = (
        100 * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )
    d["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean()

    delta = c.diff()
    gains = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    losses = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gains / losses.replace(0, np.nan)
    d["rsi"] = 100 - 100 / (1 + rs)

    d["bb_mid"] = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    d["bb_upper"] = d["bb_mid"] + 2 * sd
    d["bb_lower"] = d["bb_mid"] - 2 * sd
    d["bb_width"] = (
        (d["bb_upper"] - d["bb_lower"])
        / d["bb_mid"].replace(0, np.nan)
    )

    macd_fast = c.ewm(span=12, adjust=False).mean()
    macd_slow = c.ewm(span=26, adjust=False).mean()
    d["macd"] = macd_fast - macd_slow
    d["macd_signal"] = d["macd"].ewm(span=9, adjust=False).mean()
    d["macd_hist"] = d["macd"] - d["macd_signal"]

    lowest = l.rolling(14).min()
    highest = h.rolling(14).max()
    d["stoch_k"] = (
        100 * (c - lowest) / (highest - lowest).replace(0, np.nan)
    )
    d["stoch_d"] = d["stoch_k"].rolling(3).mean()

    d["body"] = (c - o).abs()
    d["range"] = (h - l).replace(0, np.nan)
    d["body_ratio"] = d["body"] / d["range"]
    d["upper_wick"] = (
        h - pd.concat([o, c], axis=1).max(axis=1)
    ) / d["range"]
    d["lower_wick"] = (
        pd.concat([o, c], axis=1).min(axis=1) - l
    ) / d["range"]

    return d


def market_filter(candles, payout_percent, candle_seconds=60):
    """
    Common filter for every strategy.
    Input must contain closed candles only.
    """
    if payout_percent is None:
        return False, "Live payout unavailable"

    try:
        payout = float(payout_percent)
    except (TypeError, ValueError):
        return False, "Invalid payout"

    if not math.isfinite(payout) or payout < MIN_PAYOUT_PERCENT:
        return False, "Payout below 80%"

    df = _make_frame(candles)
    if len(df) < 25:
        return False, "Not enough closed candles"

    # Inspect gaps only in recent candles.
    recent = df.tail(5)
    timestamps = recent["timestamp"].tolist()
    for a, b in zip(timestamps, timestamps[1:]):
        if b - a > candle_seconds * 1.5:
            return False, "Recent candle gap"

    # Three consecutive doji candles, counted from the latest closed candle.
    doji_count = 0
    for c in reversed(df.tail(3).to_dict("records")):
        r = c["high"] - c["low"]
        body = abs(c["close"] - c["open"])
        if r > 0 and body / r <= DOJI_BODY_RATIO:
            doji_count += 1
        else:
            break

    if doji_count >= 3:
        return False, "3 consecutive doji candles"

    # Large wick on at least 3 of the latest 4 candles.
    recent4 = df.tail(4)
    large_wicks = 0
    ranges = []

    for c in recent4.to_dict("records"):
        r = c["high"] - c["low"]
        if r <= 0:
            continue
        uw = c["high"] - max(c["open"], c["close"])
        lw = min(c["open"], c["close"]) - c["low"]
        if max(uw / r, lw / r) >= LARGE_WICK_RATIO:
            large_wicks += 1
        ranges.append(r)

    if large_wicks >= 3:
        return False, "Large wicks in recent candles"

    # Abnormally fast movement: compare last 4 ranges with prior 20.
    baseline_rows = df.iloc[-24:-4]
    baseline_ranges = (
        baseline_rows["high"] - baseline_rows["low"]
    ).tolist()
    baseline_ranges = [r for r in baseline_ranges if r > 0]

    if baseline_ranges and ranges:
        baseline = float(np.median(baseline_ranges))
        if baseline > 0:
            if max(ranges) > 2.5 * baseline:
                return False, "Abnormally fast movement"
            if sum(r > 2 * baseline for r in ranges) >= 2:
                return False, "Repeated fast movement"

    return True, "OK"


def _swing_levels(d, left=5, right=5):
    """Only expose swing levels after all right-side candles are closed."""
    highs, lows = [], []
    high_values = d["high"].to_numpy()
    low_values = d["low"].to_numpy()

    for i in range(left, len(d) - right):
        if high_values[i] >= max(high_values[i-left:i+right+1]):
            highs.append((i + right, high_values[i]))
        if low_values[i] <= min(low_values[i-left:i+right+1]):
            lows.append((i + right, low_values[i]))

    last_index = len(d) - 1
    return (
        [price for confirmed_at, price in highs if confirmed_at <= last_index],
        [price for confirmed_at, price in lows if confirmed_at <= last_index],
    )


def _get_signals(d):
    i = len(d) - 1
    x = d.iloc[i]
    p = d.iloc[i - 1]
    found = []

    def add(name, direction, reason):
        found.append({
            "strategy": name,
            "direction": direction,
            "reason": reason,
        })

    # 1. EMA Trend Pullback
    up = (
        x.ema20 > x.ema50 > x.ema200
        and x.ema50 > d.iloc[i-5].ema50
        and x.adx > 22
    )
    down = (
        x.ema20 < x.ema50 < x.ema200
        and x.ema50 < d.iloc[i-5].ema50
        and x.adx > 22
    )
    start = max(0, i - 2)
    touched = (
        d["low"].iloc[start:i+1]
        <= d["ema20"].iloc[start:i+1]
    ).any()

    if (
        up and touched and x.close > x.open
        and x.close > p.high and x.close > x.ema20
        and x.body_ratio >= 0.50
        and abs(x.ema20 - x.ema50) >= 0.3 * x.atr
        and abs(x.close - x.ema20) <= 1.5 * x.atr
    ):
        add("EMA Trend Pullback", "CALL", "Uptrend pullback")
    elif (
        down and touched and x.close < x.open
        and x.close < p.low and x.close < x.ema20
        and x.body_ratio >= 0.50
        and abs(x.ema20 - x.ema50) >= 0.3 * x.atr
        and abs(x.close - x.ema20) <= 1.5 * x.atr
    ):
        add("EMA Trend Pullback", "PUT", "Downtrend pullback")

    # 2. RSI Reversal in range market
    range_market = (
        x.adx < 20
        and abs(x.ema50 - d.iloc[i-10].ema50) < 0.5 * x.atr
    )
    last5rsi = d["rsi"].iloc[max(0, i-4):i+1]
    if (
        range_market and not (last5rsi < 30).all()
        and p.rsi < 30 <= x.rsi and x.close > x.open
    ):
        add("RSI Reversal", "CALL", "RSI crossed up from oversold")
    elif (
        range_market and not (last5rsi > 70).all()
        and p.rsi > 70 >= x.rsi and x.close < x.open
    ):
        add("RSI Reversal", "PUT", "RSI crossed down from overbought")

    # 3. Bollinger Band Re-entry
    if (
        p.close < p.bb_lower and x.close > x.bb_lower
        and x.lower_wick >= 0.40
        and x.close > (x.high + x.low) / 2
        and x.adx < 25
    ):
        add("Bollinger Re-entry", "CALL", "Lower-band re-entry")
    elif (
        p.close > p.bb_upper and x.close < x.bb_upper
        and x.upper_wick >= 0.40
        and x.close < (x.high + x.low) / 2
        and x.adx < 25
    ):
        add("Bollinger Re-entry", "PUT", "Upper-band re-entry")

    # 4. Confirmed Support/Resistance + Engulfing
    swing_highs, swing_lows = _swing_levels(d)
    support = any(abs(x.low - level) <= 0.3 * x.atr for level in swing_lows)
    resistance = any(
        abs(x.high - level) <= 0.3 * x.atr for level in swing_highs
    )
    bullish_engulf = (
        x.close > x.open and p.close < p.open
        and x.open <= p.close and x.close >= p.open
    )
    bearish_engulf = (
        x.close < x.open and p.close > p.open
        and x.open >= p.close and x.close <= p.open
    )

    if (
        support and bullish_engulf and x.close > x.ema100
        and x.body >= 0.8 * x.atr
    ):
        add("S/R Engulfing", "CALL", "Bullish engulfing near support")
    elif (
        resistance and bearish_engulf and x.close < x.ema100
        and x.body >= 0.8 * x.atr
    ):
        add("S/R Engulfing", "PUT", "Bearish engulfing near resistance")

    # 5. MACD Cross + Trend Filter
    cross_up = p.macd_hist <= 0 < x.macd_hist
    cross_down = p.macd_hist >= 0 > x.macd_hist
    hist = d["macd_hist"].tail(11)
    prev_hist = d["macd_hist"].shift(1).tail(11)
    cross_count = int((hist * prev_hist < 0).sum())

    if (
        cross_up and x.close > x.ema100 and x.adx > 20
        and x.macd > 0 and abs(x.macd_hist) >= 0.05 * x.atr
        and cross_count < 3
    ):
        add("MACD Cross", "CALL", "Bullish MACD cross")
    elif (
        cross_down and x.close < x.ema100 and x.adx > 20
        and x.macd < 0 and abs(x.macd_hist) >= 0.05 * x.atr
        and cross_count < 3
    ):
        add("MACD Cross", "PUT", "Bearish MACD cross")

    # 6. Squeeze Breakout
    percentile = d["bb_width"].rolling(100).quantile(0.15)
    squeezed = (
        (d["bb_width"] <= percentile).tail(5).sum() >= 5
    )
    don_high = d["high"].rolling(20).max().shift(1).iloc[i]
    don_low = d["low"].rolling(20).min().shift(1).iloc[i]

    if (
        squeezed and x.close > don_high
        and x.body_ratio >= 0.60 and x.range >= 1.2 * x.atr
        and x.upper_wick <= 0.40
    ):
        add("Squeeze Breakout", "CALL", "Upside squeeze breakout")
    elif (
        squeezed and x.close < don_low
        and x.body_ratio >= 0.60 and x.range >= 1.2 * x.atr
        and x.lower_wick <= 0.40
    ):
        add("Squeeze Breakout", "PUT", "Downside squeeze breakout")

    # 7. M5 trend + M1 stochastic trigger.
    # Only use completed M5 bars; never use future data.
    m1 = d.copy()
    m1["dt"] = pd.to_datetime(m1["timestamp"], unit="s", utc=True)
    m1 = m1.set_index("dt")

    m5 = m1.resample("5min", label="right", closed="right").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }).dropna()

    m5["ema50"] = m5["close"].ewm(span=50, adjust=False).mean()
    m5["slope"] = m5["ema50"].diff()

    # The M5 candle must close no later than the latest closed M1 candle.
    latest_m1_close = m1.index[-1] + pd.Timedelta(minutes=1)
    m5 = m5[m5.index <= latest_m1_close]

    if len(m5) >= 51:
        mt = m5.iloc[-1]
        m5_up = mt.close > mt.ema50 and mt.slope > 0
        m5_down = mt.close < mt.ema50 and mt.slope < 0

        last10k = d["stoch_k"].tail(10)
        stuck_low = (last10k < 20).all()
        stuck_high = (last10k > 80).all()

        if (
            m5_up and x.adx > 20 and not stuck_low
            and p.stoch_k <= p.stoch_d
            and x.stoch_k > x.stoch_d
            and p.stoch_k < 20 and x.close > x.open
        ):
            add("MTF Stochastic Pullback", "CALL", "M5 uptrend + M1 trigger")
        elif (
            m5_down and x.adx > 20 and not stuck_high
            and p.stoch_k >= p.stoch_d
            and x.stoch_k < x.stoch_d
            and p.stoch_k > 80 and x.close < x.open
        ):
            add("MTF Stochastic Pullback", "PUT", "M5 downtrend + M1 trigger")

    return found


def _wait(reason):
    return {
        "direction": "WAIT",
        "score": 0,
        "confidence": 0,
        "support": None,
        "resistance": None,
        "candle": None,
        "reasons": [reason],
        "strategy": "Seven-Strategy Engine",
    }


def analyse(candles, candle_seconds=60, payout_percent=None):
    """
    Backward-compatible strategy interface.
    Pass closed candles only; do not pass an in-progress candle.
    """
    df = _make_frame(candles)

    if len(df) < MIN_CANDLES:
        return _wait(f"Need {MIN_CANDLES} candles")

    # NOTE: Do not remove the final row here. Caller must provide closed candles.
    # This preserves the latest closed candle for analysis.
    allowed, reason = market_filter(
        df.to_dict("records"),
        payout_percent=payout_percent,
        candle_seconds=candle_seconds,
    )
    if not allowed:
        return _wait(f"Market filtered: {reason}")

    d = _indicators(df)
    needed = [
        "ema20", "ema50", "ema100", "ema200", "atr",
        "adx", "rsi", "bb_upper", "bb_lower",
        "macd", "macd_hist", "stoch_k", "stoch_d",
        "body_ratio", "atr_median50",
    ]
    if d.iloc[-1][needed].isna().any():
        return _wait("Indicators not ready")

    # Apply the existing ATR dead-market and spike checks.
    last = d.iloc[-1]
    if last.atr < 0.7 * last.atr_median50:
        return _wait("Low volatility")
    if last["range"] > 2.5 * last.atr:
        return _wait("Signal candle spike")

    found = _get_signals(d)
    if not found:
        return _wait("No strategy setup")

    directions = {s["direction"] for s in found}
    if len(directions) != 1:
        return _wait("Conflicting strategy signals")

    direction = directions.pop()
    last = d.iloc[-1]

    return {
        "direction": direction,
        "score": len(found),
        # This is a display score, NOT a probability of winning.
        "confidence": min(95, 50 + 7 * len(found)),
        "support": float(d["low"].tail(20).min()),
        "resistance": float(d["high"].tail(20).max()),
        "candle": int(last["timestamp"]),
        "reasons": [s["reason"] for s in found],
        "strategy": ", ".join(s["strategy"] for s in found),
    }


def backtest(df, sig, expiry=1, payout=0.85):
    """
    Signal on row i close -> entry row i+1 open.
    Expiry 1 -> exit row i+1 close.
    Expiry 2 -> exit row i+2 close.
    """
    if expiry < 1:
        raise ValueError("expiry must be >= 1")
    if not 0 < payout <= 1:
        raise ValueError("payout must be between 0 and 1")

    data = df.reset_index(drop=True).copy()
    signal = pd.Series(sig).reset_index(drop=True)

    entry = data["open"].shift(-1)
    exit_price = data["close"].shift(-expiry)

    win = (
        ((signal == 1) & (exit_price > entry))
        | ((signal == -1) & (exit_price < entry))
    )
    tie = exit_price.eq(entry)
    valid = signal.ne(0) & ~tie & entry.notna() & exit_price.notna()
    n = int(valid.sum())

    if not n:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "expectancy": 0.0,
            "breakeven_win_rate": 1 / (1 + payout),
        }

    wr = float(win[valid].sum() / n)
    return {
        "trades": n,
        "win_rate": wr,
        "expectancy": wr * payout - (1 - wr),
        "breakeven_win_rate": 1 / (1 + payout),
    }
