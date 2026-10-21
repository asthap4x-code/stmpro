
"""
STM PRO BOT — AI Strategy
Five rule-based technical setups.

Note:
This is a rule-based strategy engine, not a trained AI model.
Input candles: list of dicts with open, high, low, close, time.
"""

def ema(values, period):
    if len(values) < period:
        return []
    alpha = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for value in values[period:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None

    changes = [values[i] - values[i - 1]
               for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period
        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def stochastic(candles, period=14):
    recent = candles[-period:]
    if len(recent) < period:
        return None

    highest = max(c["high"] for c in recent)
    lowest = min(c["low"] for c in recent)

    if highest == lowest:
        return 50.0

    return (
        (candles[-1]["close"] - lowest)
        / (highest - lowest)
    ) * 100


def candle_pattern(candles):
    """Return bullish/bearish candle pattern, if found."""
    if len(candles) < 2:
        return None

    current = candles[-1]
    previous = candles[-2]

    o, h, low, close = (
        current["open"], current["high"],
        current["low"], current["close"]
    )

    body = abs(close - o)
    full_range = max(h - low, 1e-12)
    upper_wick = h - max(o, close)
    lower_wick = min(o, close) - low

    bullish_engulfing = (
        previous["close"] < previous["open"]
        and close > o
        and o <= previous["close"]
        and close >= previous["open"]
    )

    bearish_engulfing = (
        previous["close"] > previous["open"]
        and close < o
        and o >= previous["close"]
        and close <= previous["open"]
    )

    if bullish_engulfing:
        return "CALL", "Bullish engulfing"

    if bearish_engulfing:
        return "PUT", "Bearish engulfing"

    if lower_wick > body * 2 and lower_wick / full_range > 0.45:
        if close > o:
            return "CALL", "Bullish pin bar"

    if upper_wick > body * 2 and upper_wick / full_range > 0.45:
        if close < o:
            return "PUT", "Bearish pin bar"

    return None


def support_resistance(candles, period=30):
    recent = candles[-period:]
    if len(recent) < period:
        return None, None

    resistance = max(c["high"] for c in recent[:-1])
    support = min(c["low"] for c in recent[:-1])

    return support, resistance


def analyze_ai(candles):
    """
    Returns:
        None, or {
            "direction": "CALL" / "PUT",
            "score": float,
            "reason": str
        }

    The score is a rule score, NOT a probability of winning.
    """
    if len(candles) < 60:
        return None

    closes = [c["close"] for c in candles]
    current = candles[-1]
    previous = candles[-2]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)

    if not e9 or not e21 or not e50:
        return None

    rsi_value = rsi(closes)
    stoch_value = stochastic(candles)

    if rsi_value is None or stoch_value is None:
        return None

    call_score = 0.0
    put_score = 0.0
    call_reasons = []
    put_reasons = []

    # 1. EMA trend alignment
    if e9[-1] > e21[-1] > e50[-1]:
        call_score += 2
        call_reasons.append("EMA bullish trend")
    elif e9[-1] < e21[-1] < e50[-1]:
        put_score += 2
        put_reasons.append("EMA bearish trend")

    # 2. RSI momentum
    if 52 <= rsi_value <= 68:
        call_score += 1
        call_reasons.append("RSI bullish momentum")
    elif 32 <= rsi_value <= 48:
        put_score += 1
        put_reasons.append("RSI bearish momentum")

    # 3. Stochastic turn
    if stoch_value < 20 and current["close"] > previous["close"]:
        call_score += 1
        call_reasons.append("Stochastic oversold turn")
    elif stoch_value > 80 and current["close"] < previous["close"]:
        put_score += 1
        put_reasons.append("Stochastic overbought turn")

    # 4. Engulfing / pin-bar pattern
    pattern = candle_pattern(candles)
    if pattern:
        direction, description = pattern
        if direction == "CALL":
            call_score += 1.5
            call_reasons.append(description)
        else:
            put_score += 1.5
            put_reasons.append(description)

    # 5. Support/resistance rejection
    support, resistance = support_resistance(candles)
    if support is not None:
        candle_range = max(current["high"] - current["low"], 1e-12)
        lower_wick = min(current["open"], current["close"]) - current["low"]
        upper_wick = current["high"] - max(current["open"], current["close"])

        if (
            current["low"] <= support + candle_range * 0.15
            and current["close"] > current["open"]
            and lower_wick > candle_range * 0.25
        ):
            call_score += 1.5
            call_reasons.append("Support rejection")

        if (
            current["high"] >= resistance - candle_range * 0.15
            and current["close"] < current["open"]
            and upper_wick > candle_range * 0.25
        ):
            put_score += 1.5
            put_reasons.append("Resistance rejection")

    # Avoid extreme candle spikes, which can create late entries.
    recent_ranges = [
        c["high"] - c["low"] for c in candles[-20:]
    ]
    average_range = sum(recent_ranges) / len(recent_ranges)
    last_range = current["high"] - current["low"]

    if average_range <= 0 or last_range > average_range * 2.5:
        return None

    # Flexible threshold: signals are possible with 2+ points.
    # Conflicting directions are rejected.
    if call_score >= 2 and call_score > put_score:
        return {
            "direction": "CALL",
            "score": call_score,
            "reason": "; ".join(call_reasons)
        }

    if put_score >= 2 and put_score > call_score:
        return {
            "direction": "PUT",
            "score": put_score,
            "reason": "; ".join(put_reasons)
        }

    return None
