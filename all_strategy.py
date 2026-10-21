
"""
STM PRO BOT — All Strategy
Frequent rule-based setups for 1-minute signal scanning.

This module intentionally uses flexible scoring.
It does not guarantee a signal or a winning trade.
"""

from ai_strategy import (
    analyze_ai, ema, rsi, candle_pattern, support_resistance
)


def analyze_all(candles):
    if len(candles) < 60:
        return None

    ai_signal = analyze_ai(candles)

    closes = [c["close"] for c in candles]
    current = candles[-1]
    previous = candles[-2]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)

    if not e9 or not e21 or not e50:
        return ai_signal

    call_score = 0.0
    put_score = 0.0
    call_reasons = []
    put_reasons = []

    # 1. EMA trend continuation
    if e9[-1] > e21[-1] and closes[-1] > e50[-1]:
        call_score += 1.5
        call_reasons.append("Bullish trend continuation")

    elif e9[-1] < e21[-1] and closes[-1] < e50[-1]:
        put_score += 1.5
        put_reasons.append("Bearish trend continuation")

    # 2. EMA pullback and recovery
    recent_low = min(c["low"] for c in candles[-5:])
    recent_high = max(c["high"] for c in candles[-5:])

    if (
        e9[-1] > e21[-1]
        and recent_low <= e9[-1]
        and closes[-1] > e9[-1]
        and closes[-1] > previous["close"]
    ):
        call_score += 1.5
        call_reasons.append("EMA pullback recovery")

    if (
        e9[-1] < e21[-1]
        and recent_high >= e9[-1]
        and closes[-1] < e9[-1]
        and closes[-1] < previous["close"]
    ):
        put_score += 1.5
        put_reasons.append("EMA pullback rejection")

    # 3. RSI momentum continuation
    rsi_value = rsi(closes)
    if rsi_value is not None:
        if 50 < rsi_value < 70 and closes[-1] > previous["close"]:
            call_score += 1
            call_reasons.append("RSI momentum continuation")

        elif 30 < rsi_value < 50 and closes[-1] < previous["close"]:
            put_score += 1
            put_reasons.append("RSI bearish continuation")

    # 4. Candle pattern
    pattern = candle_pattern(candles)
    if pattern:
        direction, reason = pattern
        if direction == "CALL":
            call_score += 1.5
            call_reasons.append(reason)
        else:
            put_score += 1.5
            put_reasons.append(reason)

    # 5. Support/resistance breakout
    support, resistance = support_resistance(candles, 30)
    if support is not None:
        candle_range = max(current["high"] - current["low"], 1e-12)
        body_ratio = abs(current["close"] - current["open"]) / candle_range

        if (
            current["close"] > resistance
            and current["close"] > current["open"]
            and body_ratio > 0.45
        ):
            call_score += 1.5
            call_reasons.append("Resistance breakout")

        elif (
            current["close"] < support
            and current["close"] < current["open"]
            and body_ratio > 0.45
        ):
            put_score += 1.5
            put_reasons.append("Support breakdown")

        # 6. False breakout reversal
        if (
            current["high"] > resistance
            and current["close"] < resistance
            and current["close"] < current["open"]
        ):
            put_score += 1.5
            put_reasons.append("False breakout rejection")

        elif (
            current["low"] < support
            and current["close"] > support
            and current["close"] > current["open"]
        ):
            call_score += 1.5
            call_reasons.append("False breakdown rejection")

    # 7. Short range reversal
    recent = candles[-12:]
    range_high = max(c["high"] for c in recent)
    range_low = min(c["low"] for c in recent)
    total_range = max(range_high - range_low, 1e-12)

    if (
        current["low"] <= range_low + total_range * 0.12
        and current["close"] > current["open"]
    ):
        call_score += 0.75
        call_reasons.append("Range-low bounce")

    if (
        current["high"] >= range_high - total_range * 0.12
        and current["close"] < current["open"]
    ):
        put_score += 0.75
        put_reasons.append("Range-high rejection")

    # Include AI confluence as an extra vote, not as an automatic override.
    if ai_signal:
        if ai_signal["direction"] == "CALL":
            call_score += 0.5
            call_reasons.append("AI confluence")
        else:
            put_score += 0.5
            put_reasons.append("AI confluence")

    # Flexible threshold to find more setups.
    # If both sides score similarly, reject the conflict.
    if call_score >= 1.5 and call_score > put_score:
        return {
            "direction": "CALL",
            "score": call_score,
            "reason": "; ".join(call_reasons[:5])
        }

    if put_score >= 1.5 and put_score > call_score:
        return {
            "direction": "PUT",
            "score": put_score,
            "reason": "; ".join(put_reasons[:5])
        }

    return None
