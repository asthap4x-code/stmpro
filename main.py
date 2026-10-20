"""
STM Pro Bot — Telegram signal assistant (educational; no order execution).
Run with Python 3.11+ where possible. Uses pyquotex candle history and Telegram polling.
"""
import asyncio
import logging
import math
import os
import time
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

try:
    from pyquotex.stable_api import Quotex
except Exception:
    Quotex = None

# ---- Configuration: environment variables are safer than hard-coding secrets ----
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "8994179085:AAHUukYR7Gl3Mg3DzGRVN9Y_0wxWA1d8N9M")
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "1004308221939")  # e.g. @your_channel or -1001234567890
QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL", "janinakihoy00@gmail.com")
QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD", "71&@$@HwvIvw&$#")
ASSETS = [x.strip() for x in os.getenv("STM_ASSETS", "EURUSD_otc,GBPUSD_otc,USDJPY_otc").split(",") if x.strip()]
TIMEFRAME_SECONDS = 60
EXPIRY_SECONDS = 60
SCAN_EVERY_SECONDS = 15
MIN_CONFIDENCE = 68
UTC_OFFSET_HOURS = 5  # User requested UTC+5 for candle result lookup
MTG_ENABLED = False   # Informational one-step label only; no stake sizing or order execution
MTG_MAX_STEPS = 1

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("stm-pro")

client = None
connected = False
bot_enabled = False
scan_lock = asyncio.Lock()
last_signal_key = None
pending_results = []
stats = {"signals": 0, "wins": 0, "losses": 0, "unknown": 0}
last_error = "Not started"


def local_time(ts=None):
    tz = timezone(timedelta(hours=UTC_OFFSET_HOURS))
    return datetime.fromtimestamp(ts or time.time(), tz).strftime("%Y-%m-%d %H:%M:%S UTC+5")


def number(c, *keys, default=None):
    for key in keys:
        value = c.get(key) if isinstance(c, dict) else None
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return default


def normalize_candle(c):
    if not isinstance(c, dict):
        return None
    ts = number(c, "time", "timestamp", "at")
    o = number(c, "open", "Open")
    h = number(c, "high", "max")
    l = number(c, "low", "min")
    cl = number(c, "close", "Close")
    if None in (ts, o, h, l, cl):
        return None
    # Some feeds return timestamps in milliseconds.
    if ts > 10_000_000_000:
        ts /= 1000
    return {"time": int(ts), "open": o, "high": h, "low": l, "close": cl}


def candle_color(c):
    if c["close"] > c["open"]:
        return "GREEN"
    if c["close"] < c["open"]:
        return "RED"
    return "DOJI"


def analyse(candles):
    """Transparent heuristic: trend, support/resistance, candle pattern, momentum."""
    if len(candles) < 25:
        return None
    candles = sorted(candles, key=lambda x: x["time"])
    recent = candles[-25:]
    closes = [c["close"] for c in recent]
    last = recent[-1]
    prev = recent[-2]
    avg_fast = sum(closes[-5:]) / 5
    avg_slow = sum(closes[-15:]) / 15
    score = 0.0
    reasons = []

    # Market structure / short trend
    if avg_fast > avg_slow:
        score += 1.3
        reasons.append("short trend bullish")
    elif avg_fast < avg_slow:
        score -= 1.3
        reasons.append("short trend bearish")

    # Recent swing structure (simple HH/HL vs LH/LL proxy)
    highs = [c["high"] for c in recent[-8:]]
    lows = [c["low"] for c in recent[-8:]]
    if highs[-1] > max(highs[:-2]) and lows[-1] > min(lows[:-2]):
        score += 0.8
        reasons.append("higher-high / higher-low pressure")
    elif highs[-1] < min(highs[:-2]) and lows[-1] < max(lows[:-2]):
        score -= 0.8
        reasons.append("lower-low / lower-high pressure")

    # Support / resistance proximity (recent 20 candles)
    range_c = recent[-20:]
    support = min(c["low"] for c in range_c)
    resistance = max(c["high"] for c in range_c)
    span = max(resistance - support, 1e-12)
    near_support = (last["close"] - support) / span < 0.15
    near_resistance = (resistance - last["close"]) / span < 0.15
    if near_support and last["close"] >= last["open"]:
        score += 0.7
        reasons.append("support reaction")
    elif near_resistance and last["close"] <= last["open"]:
        score -= 0.7
        reasons.append("resistance reaction")

    # Candle patterns: engulfing and rejection wick
    body_last = abs(last["close"] - last["open"])
    body_prev = abs(prev["close"] - prev["open"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    upper_wick = last["high"] - max(last["open"], last["close"])
    if (last["close"] > last["open"] and prev["close"] < prev["open"]
        and last["close"] >= prev["open"] and last["open"] <= prev["close"]):
        score += 1.0
        reasons.append("bullish engulfing")
    elif (last["close"] < last["open"] and prev["close"] > prev["open"]
          and last["close"] <= prev["open"] and last["open"] >= prev["close"]):
        score -= 1.0
        reasons.append("bearish engulfing")
    elif body_last > 0 and lower_wick > body_last * 1.8 and lower_wick > upper_wick:
        score += 0.5
        reasons.append("lower-wick rejection")
    elif body_last > 0 and upper_wick > body_last * 1.8 and upper_wick > lower_wick:
        score -= 0.5
        reasons.append("upper-wick rejection")

    # RSI-like momentum approximation
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    ups = sum(x for x in changes[-10:] if x > 0)
    downs = abs(sum(x for x in changes[-10:] if x < 0))
    momentum = (ups - downs) / max(ups + downs, 1e-12)
    score += momentum * 0.7
    reasons.append("momentum confirms up" if momentum > 0.2 else "momentum confirms down" if momentum < -0.2 else "momentum mixed")

    if abs(score) < 0.8:
        return {"direction": "WAIT", "confidence": int(50 + abs(score) * 8),
                "reasons": reasons, "candle": last, "support": support, "resistance": resistance}
    direction = "CALL" if score > 0 else "PUT"
    confidence = min(82, int(55 + abs(score) * 7))
    return {"direction": direction, "confidence": confidence, "reasons": reasons,
            "candle": last, "support": support, "resistance": resistance}


async def connect_quotex():
    global client, connected, last_error
    if Quotex is None:
        last_error = "pyquotex import failed. Install requirements.txt in the same Python environment."
        return False
    if not QUOTEX_EMAIL or not QUOTEX_PASSWORD:
        last_error = "Missing QUOTEX_EMAIL / QUOTEX_PASSWORD environment variables."
        return False
    try:
        client = Quotex(email=QUOTEX_EMAIL, password=QUOTEX_PASSWORD, lang="en")
        result = await client.connect()
        # pyquotex releases differ in connect() return shape.
        connected = bool(result[0]) if isinstance(result, tuple) else bool(result)
        if not connected:
            last_error = f"Quotex connect returned: {result!r}"
        else:
            last_error = ""
        return connected
    except Exception as exc:
        connected = False
        last_error = f"Quotex connection error: {type(exc).__name__}: {exc}"
        log.exception("Quotex connection failed")
        return False


async def fetch_candles(asset):
    global connected, last_error
    if not connected and not await connect_quotex():
        return []
    try:
        raw = await client.get_candles(asset, time.time(), 60 * 60, TIMEFRAME_SECONDS)
        candles = [normalize_candle(c) for c in (raw or [])]
        candles = [c for c in candles if c]
        if not candles:
            last_error = f"No usable candle data returned for {asset}."
        return sorted(candles, key=lambda x: x["time"])
    except Exception as exc:
        connected = False
        last_error = f"Candle request failed for {asset}: {type(exc).__name__}: {exc}"
        log.exception("Candle request failed")
        return []


async def send_channel(application, text):
    if not TG_CHANNEL_ID:
        log.warning("TG_CHANNEL_ID is empty; not posting to channel.")
        return False
    try:
        await application.bot.send_message(chat_id=TG_CHANNEL_ID, text=text)
        return True
    except Exception:
        log.exception("Could not send Telegram channel message")
        return False


async def make_signal(application, asset, manual=False):
    global last_signal_key, pending_results
    async with scan_lock:
        candles = await fetch_candles(asset)
        analysis = analyse(candles)
        if not analysis:
            return f"⚠️ {asset}: not enough candle data or data unavailable.\n{last_error}"
        if analysis["direction"] == "WAIT" or analysis["confidence"] < MIN_CONFIDENCE:
            return (f"⏸ STM ANALYSIS — {asset}\nNo trade: mixed/weak setup.\n"
                    f"Confidence estimate: {analysis['confidence']}%\n"
                    f"Time: {local_time()}\n\nEducational analysis only; no trade executed.")
        signal_candle = analysis["candle"]
        key = (asset, signal_candle["time"], analysis["direction"])
        if not manual and key == last_signal_key:
            return "No new signal — latest candle already analysed."
        last_signal_key = key
        stats["signals"] += 1
        reasons = "\n".join(f"• {r}" for r in analysis["reasons"][:5])
        mtg_label = "\nMTG: informational step only (no automatic stake sizing)." if MTG_ENABLED else ""
        message = (
            f"⚡ STM PRO BOT | 1-MIN ANALYSIS\n\n"
            f"Asset: {asset}\n"
            f"Direction: {'🟢 CALL / UP' if analysis['direction']=='CALL' else '🔴 PUT / DOWN'}\n"
            f"Expiry: 1 minute\n"
            f"Model confidence estimate: {analysis['confidence']}% (not a win probability guarantee)\n"
            f"Signal time: {local_time()}\n"
            f"Support: {analysis['support']:.6g}\nResistance: {analysis['resistance']:.6g}\n\n"
            f"Logic:\n{reasons}{mtg_label}\n\n"
            f"⚠️ Demo/educational signal. Binary options are high risk. Confirm price and expiry in your own platform."
        )
        await send_channel(application, message)
        pending_results.append({"asset": asset, "signal": analysis["direction"],
                                "signal_time": signal_candle["time"], "sent_at": time.time()})
        return message


async def result_monitor(application):
    """After one minute, compare the next closed candle with signal direction; approximate result only."""
    while True:
        try:
            now = time.time()
            ready = [p for p in pending_results if now - p["sent_at"] >= EXPIRY_SECONDS + 5]
            for item in ready:
                try:
                    candles = await fetch_candles(item["asset"])
                    # Select a candle that began at/after signal time + expiry.
                    target = next((c for c in candles if c["time"] >= item["signal_time"] + EXPIRY_SECONDS), None)
                    if target is None:
                        stats["unknown"] += 1
                        outcome = "UNKNOWN — expiry candle not available"
                    else:
                        color = candle_color(target)
                        win = (item["signal"] == "CALL" and color == "GREEN") or (item["signal"] == "PUT" and color == "RED")
                        outcome = "WIN (candle-direction proxy)" if win else "LOSS (candle-direction proxy)"
                        stats["wins" if win else "losses"] += 1
                    msg = (f"📊 STM RESULT CHECK\nAsset: {item['asset']}\n"
                           f"Signal: {item['signal']} | Expiry: 1 minute\nResult: {outcome}\n"
                           f"Checked: {local_time()}\n\n"
                           f"Result is estimated from candle open/close, not verified broker settlement or payout.")
                    await send_channel(application, msg)
                except Exception:
                    log.exception("Result check failed")
                    stats["unknown"] += 1
                finally:
                    if item in pending_results:
                        pending_results.remove(item)
        except Exception:
            log.exception("Result monitor loop error")
        await asyncio.sleep(5)


async def scan_loop(application):
    while True:
        if bot_enabled:
            for asset in ASSETS:
                try:
                    await make_signal(application, asset)
                except Exception:
                    log.exception("Signal scan failed")
                await asyncio.sleep(1)
        await asyncio.sleep(SCAN_EVERY_SECONDS)


async def post_init(application: Application):
    await application.bot.set_my_commands([
        ("start", "Show bot commands"), ("on", "Start automatic signal scanning"),
        ("off", "Stop automatic scanning"), ("signal", "Analyse one asset"),
        ("status", "Show bot status"), ("stats", "Show result-check stats"),
    ])
    application.create_task(scan_loop(application))
    application.create_task(result_monitor(application))


def authorized(update: Update):
    # Optional allow-list. If STM_ALLOWED_USER_IDS is set, only those Telegram user IDs can control bot.
    allowed = {x.strip() for x in os.getenv("STM_ALLOWED_USER_IDS", "").split(",") if x.strip()}
    return not allowed or str(update.effective_user.id) in allowed


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    await update.message.reply_text(
        "🤖 STM Pro Bot is online.\n\n"
        "/on — start automatic scanning\n/off — stop scanning\n"
        "/signal EURUSD_otc — analyse one asset\n/status — connection/status\n/stats — result checks\n\n"
        "This bot sends analysis only; it does not place trades."
    )


async def on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_enabled
    if not authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    bot_enabled = True
    await update.message.reply_text(f"✅ Auto scan ON\nAssets: {', '.join(ASSETS)}\nExpiry: 1 minute")


async def off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_enabled
    if not authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    bot_enabled = False
    await update.message.reply_text("⏹ Auto scan OFF. Pending result checks may still be posted.")


async def signal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    asset = context.args[0] if context.args else (ASSETS[0] if ASSETS else "EURUSD_otc")
    await update.message.reply_text("Analysing candle history…")
    result = await make_signal(context.application, asset, manual=True)
    await update.message.reply_text(result)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    await update.message.reply_text(
        f"STM Pro Bot status\nAuto scan: {'ON' if bot_enabled else 'OFF'}\n"
        f"Quotex connected: {connected}\nChannel configured: {bool(TG_CHANNEL_ID)}\n"
        f"Assets: {', '.join(ASSETS)}\nTime: {local_time()}\n"
        f"Last error: {last_error or 'None'}"
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    total = stats["wins"] + stats["losses"]
    rate = (100 * stats["wins"] / total) if total else 0
    await update.message.reply_text(
        f"📈 Result-check stats (current process only)\nSignals: {stats['signals']}\n"
        f"Win proxy: {stats['wins']} | Loss proxy: {stats['losses']} | Unknown: {stats['unknown']}\n"
        f"Proxy rate: {rate:.1f}% of classified candle checks.\n"
        f"Not a verified trading win rate; no payout or broker settlement is checked."
    )


def main():
    if TG_BOT_TOKEN == "PASTE_TELEGRAM_BOT_TOKEN_HERE" or not TG_BOT_TOKEN:
        raise SystemExit("Set TG_BOT_TOKEN environment variable before starting.")
    app = Application.builder().token(TG_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("on", on_cmd))
    app.add_handler(CommandHandler("off", off_cmd))
    app.add_handler(CommandHandler("signal", signal_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    log.info("STM Pro Bot starting; channel=%s assets=%s", TG_CHANNEL_ID or "(not configured)", ASSETS)
    app.run_polling()


if __name__ == "__main__":
    main()
