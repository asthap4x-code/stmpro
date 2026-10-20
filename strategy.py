
"""
STM Pro Bot — strategy.py

Includes:
1. Original trend, swing, support/resistance, engulfing,
   wick-rejection and momentum strategy.
2. New 3-4 candle continuation setup.
3. Candle quality filters for doji, weak bodies,
   excessive wicks, unusually large candles and volatility.

Note:
No strategy guarantees a particular win rate.
"""

from statistics import median


# =========================================================
# CANDLE HELPERS
# =========================================================

def number(candle, *keys):
    """Read a numeric value from common candle field names."""
    if not isinstance(candle, dict):
        return None

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

    timestamp = number(
        candle, "time", "timestamp", "at", "from"
    )
    open_price = number(candle, "open", "Open")
    high = number(candle, "high", "max", "High")
    low = number(candle, "low", "min", "Low")
    close = number(candle, "close", "Close")

    if None in (
        timestamp,
        open_price,
        high,
        low,
        close,
    ):
        return None

    # Convert millisecond timestamps to seconds.
    if timestamp > 10_000_000_000:
        timestamp /= 1000

    if high < low:
        return None

    if high < max(open_price, close):
        return None

    if low > min(open_price, close):
        return None

    return {
        "time": int(timestamp),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    }


def candle_color(candle):
    """Return GREEN, RED or DOJI."""
    if candle["close"] > candle["open"]:
        return "GREEN"

    if candle["close"] < candle["open"]:
        return "RED"

    return "DOJI"


def candle_body(candle):
    return abs(candle["close"] - candle["open"])


def candle_range(candle):
    return max(candle["high"] - candle["low"], 0.0)


def upper_wick(candle):
    return candle["high"] - max(
        candle["open"], candle["close"]
    )


def lower_wick(candle):
    return min(
        candle["open"], candle["close"]
    ) - candle["low"]


def result(
    direction,
    score,
    support,
    resistance,
    candle,
    reasons,
    strategy_name,
):
    """Keep a consistent result format for main.py."""
    return {
        "direction": direction,
        "score": round(float(score), 2),
        "confidence": min(
            82,
            int(55 + abs(float(score)) * 7),
        ),
        "support": support,
        "resistance": resistance,
        "candle": candle,
        "reasons": reasons,
        "strategy": strategy_name,
    }


# =========================================================
# QUALITY FILTERS
# =========================================================

def candle_is_good(candle, recent_candles):
    """
    Reject candles with:
    - Tiny bodies / doji-like candles
    - Excessive wick rejection
    - Unusually large ranges
    """
    body = candle_body(candle)
    total_range = candle_range(candle)

    if total_range <= 0:
        return False, "Zero-range candle"

    # Require a meaningful body.
    body_ratio = body / total_range

    if body_ratio < 0.35:
        return False, "Weak body or doji-like candle"

    # Reject candles dominated by long wicks.
    upper_ratio = upper_wick(candle) / total_range
    lower_ratio = lower_wick(candle) / total_range

    if max(upper_ratio, lower_ratio) > 0.45:
        return False, "Excessive candle wick"

    # Reject a candle much larger than typical recent candles.
    ranges = [
        candle_range(item)
        for item in recent_candles
        if candle_range(item) > 0
    ]

    if len(ranges) >= 5:
        typical_range = median(ranges)

        if typical_range > 0 and total_range > typical_range * 2.2:
            return False, "Unusually large candle"

    return True, "Candle quality passed"


def volatility_is_acceptable(candles):
    """
    Reject extreme volatility where recent ranges expand sharply.
    This is a simple heuristic, not a statistical volatility model.
    """
    recent = candles[-10:]

    ranges = [
        candle_range(candle)
        for candle in recent
        if candle_range(candle) > 0
    ]

    if len(ranges) < 7:
        return False, "Not enough valid ranges"

    typical_range = median(ranges)

    if typical_range <= 0:
        return False, "Invalid typical range"

    latest_range = candle_range(recent[-1])

    if latest_range > typical_range * 2.2:
        return False, "Latest candle volatility is too high"

    # Reject a cluster of persistently oversized candles.
    oversized = sum(
        1 for value in ranges
        if value > typical_range * 1.8
    )

    if oversized >= 4:
        return False, "Recent volatility is elevated"

    return True, "Volatility filter passed"


# =========================================================
# NEW: 3-4 CANDLE CONTINUATION STRATEGY
# =========================================================

def continuation_strategy(candles):
    """
    Setup A:
        Downtrend + 3-4 RED candles + final GREEN candle
        => PUT candidate.

    Setup B:
        Uptrend + 3-4 GREEN candles + final RED candle
        => CALL candidate.

    All candles must pass the quality checks.
    The latest candle is assumed to be closed by the caller.
    """
    if len(candles) < 25:
        return None

    recent = candles[-25:]
    latest = recent[-1]

    # Ensure the latest candle has a clear direction.
    latest_color = candle_color(latest)

    if latest_color == "DOJI":
        return None

    # Filter the latest candle and the recent volatility.
    quality_ok, _ = candle_is_good(
        latest,
        recent[-10:],
    )

    if not quality_ok:
        return None

    volatility_ok, _ = volatility_is_acceptable(recent)

    if not volatility_ok:
        return None

    closes = [item["close"] for item in recent]

    fast_ma = sum(closes[-5:]) / 5
    slow_ma = sum(closes[-15:]) / 15

    # Examine the 3-4 candles immediately before the latest candle.
    preceding = recent[:-1]

    run_color = None
    run_length = 0

    for candle in reversed(preceding):
        color = candle_color(candle)

        if color == "DOJI":
            break

        if run_color is None:
            run_color = color
            run_length = 1

        elif color == run_color:
            run_length += 1

        else:
            break

        if run_length >= 5:
            break

    if run_length not in (3, 4):
        return None

    run_candles = preceding[-run_length:]

    # All candles in the run must be reasonably clean.
    for candle in run_candles:
        good, _ = candle_is_good(
            candle,
            recent[-10:],
        )

        if not good:
            return None

    support = min(
        candle["low"] for candle in recent[-20:]
    )
    resistance = max(
        candle["high"] for candle in recent[-20:]
    )

    # Setup A: bearish trend, consecutive red candles,
    # then a green pullback candle => PUT candidate.
    if (
        run_color == "RED"
        and latest_color == "GREEN"
        and fast_ma < slow_ma
    ):
        return result(
            direction="PUT",
            score=-2.0,
            support=support,
            resistance=resistance,
            candle=latest,
            reasons=[
                f"Bearish trend: fast MA below slow MA",
                f"{run_length} consecutive red candles",
                "Green candle treated as a pullback",
                "Candle quality and volatility filters passed",
            ],
            strategy_name="3-4 Candle Continuation",
        )

    # Setup B: bullish trend, consecutive green candles,
    # then a red pullback candle => CALL candidate.
    if (
        run_color == "GREEN"
        and latest_color == "RED"
        and fast_ma > slow_ma
    ):
        return result(
            direction="CALL",
            score=2.0,
            support=support,
            resistance=resistance,
            candle=latest,
            reasons=[
                "Bullish trend: fast MA above slow MA",
                f"{run_length} consecutive green candles",
                "Red candle treated as a pullback",
                "Candle quality and volatility filters passed",
            ],
            strategy_name="3-4 Candle Continuation",
        )

    return None


# =========================================================
# ORIGINAL STRATEGY — PRESERVED
# =========================================================

def original_strategy(candles):
    """
    Original heuristic:
    trend + swing structure + support/resistance
    + engulfing/wick patterns + momentum.
    """
    if len(candles) < 25:
        return None

    candles = sorted(
        candles,
        key=lambda item: item["time"],
    )

    recent = candles[-25:]
    closes = [item["close"] for item in recent]

    last = recent[-1]
    previous = recent[-2]

    score = 0.0
    reasons = []

    # 1. Short-term trend.
    fast_ma = sum(closes[-5:]) / 5
    slow_ma = sum(closes[-15:]) / 15

    if fast_ma > slow_ma:
        score += 1.3
        reasons.append("Short-term bullish trend")

    elif fast_ma < slow_ma:
        score -= 1.3
        reasons.append("Short-term bearish trend")

    # 2. Recent swing structure.
    highs = [
        candle["high"]
        for candle in recent[-8:]
    ]

    lows = [
        candle["low"]
        for candle in recent[-8:]
    ]

    if (
        highs[-1] > max(highs[:-2])
        and lows[-1] > min(lows[:-2])
    ):
        score += 0.8
        reasons.append(
            "Higher-high / higher-low structure"
        )

    elif (
        lows[-1] < min(lows[:-2])
        and highs[-1] < max(highs[:-2])
    ):
        score -= 0.8
        reasons.append(
            "Lower-low / lower-high structure"
        )

    # 3. Support and resistance.
    window = recent[-20:]

    support = min(
        candle["low"] for candle in window
    )

    resistance = max(
        candle["high"] for candle in window
    )

    price_range = max(
        resistance - support,
        1e-12,
    )

    near_support = (
        last["close"] - support
    ) / price_range < 0.15

    near_resistance = (
        resistance - last["close"]
    ) / price_range < 0.15

    if near_support and last["close"] > last["open"]:
        score += 0.7
        reasons.append("Bullish support reaction")

    elif (
        near_resistance
        and last["close"] < last["open"]
    ):
        score -= 0.7
        reasons.append("Bearish resistance reaction")

    # 4. Engulfing patterns.
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

    # 5. Wick rejection.
    body = candle_body(last)
    lower = lower_wick(last)
    upper = upper_wick(last)

    if (
        body > 0
        and lower > body * 1.8
        and lower > upper
    ):
        score += 0.5
        reasons.append("Lower-wick rejection")

    elif (
        body > 0
        and upper > body * 1.8
        and upper > lower
    ):
        score -= 0.5
        reasons.append("Upper-wick rejection")

    # 6. Momentum.
    changes = [
        closes[index] - closes[index - 1]
        for index in range(1, len(closes))
    ]

    positive = sum(
        value for value in changes[-10:]
        if value > 0
    )

    negative = abs(sum(
        value for value in changes[-10:]
        if value < 0
    ))

    momentum = (
        (positive - negative)
        / max(positive + negative, 1e-12)
    )

    score += momentum * 0.7

    reasons.append(
        "Bullish momentum"
        if momentum > 0.2
        else "Bearish momentum"
        if momentum < -0.2
        else "Mixed momentum"
    )

    direction = (
        "WAIT"
        if abs(score) < 0.8
        else "CALL"
        if score > 0
        else "PUT"
    )

    return result(
        direction=direction,
        score=score,
        support=support,
        resistance=resistance,
        candle=last,
        reasons=reasons,
        strategy_name="Original Scoring Strategy",
    )


# =========================================================
# PUBLIC FUNCTION — USED BY main.py
# =========================================================

def analyse(candles):
    """
    First checks the new continuation setup.
    If it does not trigger, uses the original strategy.

    Both return the same result structure expected by main.py.
    """
    if not isinstance(candles, (list, tuple)):
        return None

    normalized = []

    for raw in candles:
        candle = normalize_candle(raw)

        if candle is not None:
            normalized.append(candle)

    # Sort and remove duplicate timestamps.
    by_time = {
        candle["time"]: candle
        for candle in normalized
    }

    normalized = sorted(
        by_time.values(),
        key=lambda item: item["time"],
    )

    if len(normalized) < 25:
        return None

    # New setup has priority.
    new_result = continuation_strategy(normalized)

    if new_result is not None:
        return new_result

    # Preserve the original strategy as a fallback.
    return original_strategy(normalized)
