
"""STM Pro Bot — separate signal strategy module."""

def number(candle, *keys):
    for key in keys:
        try:
            value = candle.get(key)
            if value is not None:
                return float(value)
        except (AttributeError, TypeError, ValueError):
            pass
    return None


def normalize_candle(candle):
    """Convert common Quotex candle formats to one standard format."""
    if not isinstance(candle, dict):
        return None

    timestamp = number(candle, "time", "timestamp", "at", "from")
    open_price = number(candle, "open", "Open")
    high = number(candle, "high", "max", "High")
    low = number(candle, "low", "min", "Low")
    close = number(candle, "close", "Close")

    if None in (timestamp, open_price, high, low, close):
        return None

    if timestamp > 10_000_000_000:
        timestamp /= 1000

    return {
        "time": int(timestamp),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    }


def candle_color(candle):
    if candle["close"] > candle["open"]:
        return "GREEN"
    if candle["close"] < candle["open"]:
        return "RED"
    return "DOJI"


def analyse(candles):
    """
    Heuristic analysis:
    trend + swing structure + support/resistance
    + engulfing/wick patterns + momentum.

    Score is NOT a calibrated probability of winning.
    """
    if len(candles) < 25:
        return None

    candles = sorted(candles, key=lambda c: c["time"])
    recent = candles[-25:]
    closes = [c["close"] for c in recent]
    last, previous = recent[-1], recent[-2]

    score = 0.0
    reasons = []

    # 1. Short-term trend
    fast_ma = sum(closes[-5:]) / 5
    slow_ma = sum(closes[-15:]) / 15

    if fast_ma > slow_ma:
        score += 1.3
        reasons.append("Short-term bullish trend")
    elif fast_ma < slow_ma:
        score -= 1.3
        reasons.append("Short-term bearish trend")

    # 2. Recent swing structure (SMC-style proxy)
    highs = [c["high"] for c in recent[-8:]]
    lows = [c["low"] for c in recent[-8:]]

    if highs[-1] > max(highs[:-2]) and lows[-1] > min(lows[:-2]):
        score += 0.8
        reasons.append("Higher-high / higher-low structure")
    elif lows[-1] < min(lows[:-2]) and highs[-1] < max(highs[:-2]):
        score -= 0.8
        reasons.append("Lower-low / lower-high structure")

    # 3. Support and resistance
    window = recent[-20:]
    support = min(c["low"] for c in window)
    resistance = max(c["high"] for c in window)
    price_range = max(resistance - support, 1e-12)

    near_support = (last["close"] - support) / price_range < 0.15
    near_resistance = (resistance - last["close"]) / price_range < 0.15

    if near_support and last["close"] > last["open"]:
        score += 0.7
        reasons.append("Bullish support reaction")
    elif near_resistance and last["close"] < last["open"]:
        score -= 0.7
        reasons.append("Bearish resistance reaction")

    # 4. Engulfing patterns
    if (
        last["close"] > last["open"]
        and previous["close"] < previous["open"]
        and last["close"] >= previous["open"]
        and last["open"] <= previous["close"]
    ):
        score += 1.0
        reasons.append("Bullish engulfing")

    elif (
        last["close"] < last["open"]
        and previous["close"] > previous["open"]
        and last["close"] <= previous["open"]
        and last["open"] >= previous["close"]
    ):
        score -= 1.0
        reasons.append("Bearish engulfing")

    # 5. Wick rejection
    body = abs(last["close"] - last["open"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    upper_wick = last["high"] - max(last["open"], last["close"])

    if body > 0 and lower_wick > body * 1.8 and lower_wick > upper_wick:
        score += 0.5
        reasons.append("Lower-wick rejection")
    elif body > 0 and upper_wick > body * 1.8 and upper_wick > lower_wick:
        score -= 0.5
        reasons.append("Upper-wick rejection")

    # 6. Momentum
    changes = [
        closes[i] - closes[i - 1]
        for i in range(1, len(closes))
    ]

    positive = sum(x for x in changes[-10:] if x > 0)
    negative = abs(sum(x for x in changes[-10:] if x < 0))
    momentum = (positive - negative) / max(positive + negative, 1e-12)

    score += momentum * 0.7
    reasons.append(
        "Bullish momentum" if momentum > 0.2
        else "Bearish momentum" if momentum < -0.2
        else "Mixed momentum"
    )

    # Avoid weak, mixed setups
    if abs(score) < 0.8:
        direction = "WAIT"
    else:
        direction = "CALL" if score > 0 else "PUT"

    return {
        "direction": direction,
        "score": round(score, 2),
        "confidence": min(82, int(55 + abs(score) * 7)),
        "support": support,
        "resistance": resistance,
        "candle": last,
        "reasons": reasons,
    }
