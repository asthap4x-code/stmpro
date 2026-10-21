import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# Optional Quotex API
try:
    from pyquotex.stable_api import Quotex
except ImportError:
    Quotex = None


# =========================
# CONFIGURATION
# =========================

load_dotenv()

TG_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "").strip()

QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL", "").strip()
QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD", "").strip()

MIN_PAYOUT = float(os.getenv("MIN_PAYOUT", "80"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "10"))
CANDLE_COUNT = 60
EXPIRY_SECONDS = 60

DB_FILE = "stm_history.db"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("STM_PRO_BOT")


# =========================
# MARKETS
# =========================

MARKETS = {
    "forex": {
        "EURUSD": "EUR/USD",
        "GBPUSD": "GBP/USD",
        "USDJPY": "USD/JPY",
        "AUDUSD": "AUD/USD",
        "USDCAD": "USD/CAD",
        "USDCHF": "USD/CHF",
        "NZDUSD": "NZD/USD",
        "EURGBP": "EUR/GBP",
    },
    "commodities": {
        "XAUUSD_otc": "Gold OTC",
        "XAGUSD_otc": "Silver OTC",
    },
    "crypto": {
        "BTCUSD_otc": "Bitcoin OTC",
        "ETHUSD_otc": "Ethereum OTC",
    },
    "currency_otc": {
        "EURUSD_otc": "EUR/USD OTC",
        "GBPUSD_otc": "GBP/USD OTC",
        "USDJPY_otc": "USD/JPY OTC",
        "AUDUSD_otc": "AUD/USD OTC",
        "USDCAD_otc": "USD/CAD OTC",
    },
}


# =========================
# DATABASE
# =========================

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                user_id INTEGER,
                pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                payout REAL,
                confidence REAL,
                result TEXT DEFAULT 'PENDING'
            )
        """)
        conn.commit()


def save_signal(user_id, pair, direction, payout, confidence):
    created_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.execute("""
            INSERT INTO signals
            (created_at, user_id, pair, direction, payout, confidence)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            created_at,
            user_id,
            pair,
            direction,
            payout,
            confidence,
        ))
        conn.commit()
        return cursor.lastrowid


def get_history(user_id, limit=10):
    with sqlite3.connect(DB_FILE) as conn:
        return conn.execute("""
            SELECT created_at, pair, direction, payout,
                   confidence, result
            FROM signals
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()


def update_result(signal_id, result):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "UPDATE signals SET result = ? WHERE id = ?",
            (result, signal_id),
        )
        conn.commit()


# =========================
# QUOTEX CONNECTION
# =========================

class QuotexEngine:
    def __init__(self):
        self.client = None
        self.connected = False
        self.lock = asyncio.Lock()

    async def connect(self):
        if Quotex is None:
            raise RuntimeError(
                "pyquotex is not installed. Install your compatible "
                "PyQuotex package first."
            )

        if not QUOTEX_EMAIL or not QUOTEX_PASSWORD:
            raise RuntimeError(
                "Set QUOTEX_EMAIL and QUOTEX_PASSWORD in your .env file."
            )

        self.client = Quotex(
            email=QUOTEX_EMAIL,
            password=QUOTEX_PASSWORD,
            lang="en",
        )

        result = await self.client.connect()

        if isinstance(result, tuple):
            self.connected = bool(result[0])
        else:
            self.connected = bool(result)

        if not self.connected:
            raise RuntimeError(
                "Quotex connection failed. Check login and API compatibility."
            )

        logger.info("Quotex connection established.")
        return True

    async def ensure_connected(self):
        if self.client is None or not self.connected:
            await self.connect()

    async def get_candles(self, pair):
        await self.ensure_connected()

        candles = await self.client.get_candles(
            pair,
            60,
            int(time.time()),
            CANDLE_COUNT * 60,
        )

        if not candles or not isinstance(candles, (list, tuple)):
            return []

        cleaned = []

        for candle in candles:
            if not isinstance(candle, dict):
                continue

            try:
                open_price = float(candle["open"])
                close_price = float(candle["close"])
                high_price = float(candle.get("high", max(
                    open_price, close_price
                )))
                low_price = float(candle.get("low", min(
                    open_price, close_price
                )))

                cleaned.append({
                    "open": open_price,
                    "close": close_price,
                    "high": high_price,
                    "low": low_price,
                    "time": candle.get("time", 0),
                })
            except (KeyError, TypeError, ValueError):
                continue

        cleaned.sort(key=lambda item: item["time"])
        return cleaned

    async def get_payout(self, pair):
        """
        PyQuotex versions differ in payout methods and return formats.
        If this installed version exposes get_payment(), read its result.
        Return None if payout cannot be verified.
        """
        await self.ensure_connected()

        method = getattr(self.client, "get_payment", None)

        if method is None:
            return None

        try:
            data = method()
            if asyncio.iscoroutine(data):
                data = await data

            if not isinstance(data, dict):
                return None

            item = data.get(pair)

            if isinstance(item, dict):
                value = item.get("payment", item.get("payout"))
            elif isinstance(item, (int, float)):
                value = item
            else:
                return None

            if value is None:
                return None

            value = float(value)

            # Some API versions report 0.80 instead of 80.
            if 0 < value <= 1:
                value *= 100

            if not 0 <= value <= 100:
                return None

            return value

        except Exception as exc:
            logger.warning("Could not verify payout for %s: %s", pair, exc)
            return None


engine = QuotexEngine()


# =========================
# STRATEGY
# =========================

def calculate_ema(values, period):
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    result = [None] * (period - 1) + [ema]

    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
        result.append(ema)

    return result


def analyze_candles(candles):
    """
    Educational trend/momentum filter.
    Returns None when data is insufficient or movement is too strong.
    """
    if len(candles) < 30:
        return None

    # Use only completed candles where possible.
    candles = candles[:-1]

    if len(candles) < 25:
        return None

    closes = [c["close"] for c in candles]
    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    if any(price <= 0 for price in closes):
        return None

    ema_fast = calculate_ema(closes, 5)
    ema_slow = calculate_ema(closes, 13)

    if not ema_fast or not ema_slow:
        return None

    fast_now = ema_fast[-1]
    slow_now = ema_slow[-1]

    if fast_now is None or slow_now is None:
        return None

    recent = candles[-8:]
    directions = []

    for candle in recent:
        if candle["close"] > candle["open"]:
            directions.append("UP")
        elif candle["close"] < candle["open"]:
            directions.append("DOWN")
        else:
            directions.append("FLAT")

    # Avoid markets with 3 or more consecutive candles
    # moving in the same direction.
    streak = 1
    for index in range(len(directions) - 1, 0, -1):
        if (
            directions[index] != "FLAT"
            and directions[index] == directions[index - 1]
        ):
            streak += 1
        else:
            break

    if streak >= 3:
        return None

    last = candles[-1]
    price = closes[-1]

    # Avoid unusually large recent candles.
    avg_range = sum(
        highs[i] - lows[i] for i in range(-11, -1)
    ) / 10

    last_range = last["high"] - last["low"]

    if avg_range <= 0:
        return None

    if last_range > avg_range * 2.2:
        return None

    # Reject extremely large short-term movement.
    recent_move = abs(closes[-1] - closes[-6]) / price * 100

    if recent_move > 0.35:
        return None

    bullish = fast_now > slow_now and closes[-1] > fast_now
    bearish = fast_now < slow_now and closes[-1] < fast_now

    if not bullish and not bearish:
        return None

    direction = "CALL" if bullish else "PUT"

    # Do not chase a long one-direction streak.
    if directions[-1] != "FLAT" and streak >= 2:
        return None

    # Basic scoring, not a probability of winning.
    separation = abs(fast_now - slow_now) / price * 100
    momentum = abs(closes[-1] - closes[-4]) / price * 100

    score = 1.0
    score += min(separation * 10, 0.7)
    score += min(momentum * 2, 0.3)

    confidence = min(95.0, 60.0 + score * 10)

    return {
        "direction": direction,
        "confidence": round(confidence, 1),
    }


# =========================
# TELEGRAM KEYBOARDS
# =========================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Scan Markets", callback_data="scan_menu"),
        ],
        [
            InlineKeyboardButton("📈 Signal History", callback_data="history"),
            InlineKeyboardButton("ℹ️ Help", callback_data="help"),
        ],
    ])


def category_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Forex", callback_data="cat:forex"),
            InlineKeyboardButton(
                "Commodities OTC", callback_data="cat:commodities"
            ),
        ],
        [
            InlineKeyboardButton("Crypto OTC", callback_data="cat:crypto"),
            InlineKeyboardButton(
                "Currency OTC", callback_data="cat:currency_otc"
            ),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="home"),
        ],
    ])


def pair_keyboard(category):
    pairs = MARKETS.get(category, {})
    rows = []
    row = []

    for pair, label in pairs.items():
        row.append(InlineKeyboardButton(
            label,
            callback_data=f"pair:{category}:{pair}",
        ))

        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data="scan_menu")
    ])

    return InlineKeyboardMarkup(rows)


# =========================
# SIGNAL SCANNING
# =========================

async def scan_pair(pair):
    async with engine.lock:
        payout = await engine.get_payout(pair)

        # Never assume a payout when it cannot be verified.
        if payout is None:
            return {
                "ok": False,
                "reason": (
                    "Payout could not be verified for this asset. "
                    "Check your installed PyQuotex API version."
                ),
            }

        if payout < MIN_PAYOUT:
            return {
                "ok": False,
                "reason": f"Payout is {payout:.0f}%, below the {MIN_PAYOUT:.0f}% minimum.",
            }

        candles = await engine.get_candles(pair)

    signal = analyze_candles(candles)

    if signal is None:
        return {
            "ok": False,
            "reason": "No valid setup. Market filters rejected this pair.",
        }

    return {
        "ok": True,
        "pair": pair,
        "payout": payout,
        **signal,
    }


async def send_signal(update, context, pair, result):
    user_id = update.effective_user.id

    signal_id = save_signal(
        user_id=user_id,
        pair=pair,
        direction=result["direction"],
        payout=result["payout"],
        confidence=result["confidence"],
    )

    arrow = "🟢 CALL ⬆️" if result["direction"] == "CALL" else "🔴 PUT ⬇️"

    message = (
        "🚀 STM PRO BOT\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💱 Asset: {pair}\n"
        f"📍 Direction: {arrow}\n"
        f"⏱ Expiry: {EXPIRY_SECONDS} seconds\n"
        f"💰 Payout: {result['payout']:.0f}%\n"
        f"📊 Setup score: {result['confidence']:.1f}/100\n"
        f"🆔 Signal ID: {signal_id}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Analytical signal only. No profit is guaranteed."
    )

    await update.effective_chat.send_message(message)

    if TG_CHANNEL_ID:
        try:
            await context.bot.send_message(
                chat_id=TG_CHANNEL_ID,
                text=message,
            )
        except Exception as exc:
            logger.warning("Channel post failed: %s", exc)


# =========================
# COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Welcome to STM PRO BOT!\n\n"
        "Language: English 🇬🇧\n"
        f"Minimum payout: {MIN_PAYOUT:.0f}%\n"
        "Expiry: 1 minute\n\n"
        "Choose an option below.",
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "STM PRO BOT — Help\n\n"
        "/start - Open the main menu\n"
        "/scan - Choose a market and scan once\n"
        "/history - View your recent signal history\n"
        "/status - Check configuration\n\n"
        "The bot filters out low payouts and some high-momentum "
        "conditions. It does not guarantee winning trades."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "STM PRO BOT Status\n\n"
        f"Minimum payout: {MIN_PAYOUT:.0f}%\n"
        f"Scan interval setting: {SCAN_INTERVAL} seconds\n"
        f"PyQuotex package: {'Available' if Quotex else 'Not installed'}\n"
        f"Quotex credentials: "
        f"{'Configured' if QUOTEX_EMAIL and QUOTEX_PASSWORD else 'Missing'}\n"
        f"Channel broadcasting: {'Enabled' if TG_CHANNEL_ID else 'Disabled'}"
    )


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Choose a market category:",
        reply_markup=category_keyboard(),
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_history(update.effective_user.id)

    if not rows:
        await update.effective_message.reply_text(
            "You have no saved signals yet."
        )
        return

    lines = ["📈 STM PRO BOT — Recent Signal History\n"]

    for row in rows:
        created_at, pair, direction, payout, confidence, result = row
        timestamp = created_at[:19].replace("T", " ")

        lines.append(
            f"{timestamp} UTC\n"
            f"{pair} | {direction}\n"
            f"Payout: {payout:.0f}% | Score: {confidence:.1f}/100\n"
            f"Result: {result}\n"
        )

    await update.effective_message.reply_text("\n".join(lines))


# =========================
# CALLBACKS
# =========================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    if data == "home":
        await query.edit_message_text(
            "STM PRO BOT\nChoose an option:",
            reply_markup=main_keyboard(),
        )
        return

    if data == "scan_menu":
        await query.edit_message_text(
            "Choose a market category:",
            reply_markup=category_keyboard(),
        )
        return

    if data == "help":
        await query.edit_message_text(
            "Commands:\n"
            "/start - Main menu\n"
            "/scan - Scan a selected market\n"
            "/history - Recent signal history\n"
            "/status - Bot configuration\n\n"
            "Signals are analytical estimates, not guaranteed outcomes.",
            reply_markup=main_keyboard(),
        )
        return

    if data == "history":
        rows = get_history(query.from_user.id)

        if not rows:
            await query.edit_message_text(
                "You have no saved signals yet.",
                reply_markup=main_keyboard(),
            )
            return

        lines = ["📈 Recent Signal History\n"]

        for row in rows:
            created_at, pair, direction, payout, confidence, result = row
            lines.append(
                f"{created_at[:19].replace('T', ' ')} UTC\n"
                f"{pair} | {direction} | {payout:.0f}% payout\n"
                f"Score: {confidence:.1f}/100 | {result}\n"
            )

        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back", callback_data="home")
            ]]),
        )
        return

    if data.startswith("cat:"):
        category = data.split(":", 1)[1]

        if category not in MARKETS:
            await query.edit_message_text("Unknown market category.")
            return

        await query.edit_message_text(
            "Choose an asset:",
            reply_markup=pair_keyboard(category),
        )
        return

    if data.startswith("pair:"):
        parts = data.split(":", 2)

        if len(parts) != 3:
            await query.edit_message_text("Invalid asset selection.")
            return

        category, pair = parts[1], parts[2]

        if category not in MARKETS or pair not in MARKETS[category]:
            await query.edit_message_text("Unknown asset.")
            return

        await query.edit_message_text(
            f"Scanning {pair}...\nPlease wait."
        )

        try:
            result = await scan_pair(pair)

            if not result["ok"]:
                await query.message.reply_text(
                    f"⏸ No signal for {pair}\n\n"
                    f"Reason: {result['reason']}",
                    reply_markup=main_keyboard(),
                )
                return

            await send_signal(update, context, pair, result)

        except Exception as exc:
            logger.exception("Scan failed for %s", pair)
            await query.message.reply_text(
                "❌ Scan failed.\n"
                "Check your Quotex connection, asset name, and installed "
                "PyQuotex API compatibility.\n\n"
                f"Error: {str(exc)[:250]}",
                reply_markup=main_keyboard(),
            )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error("Telegram update error: %s", context.error)


# =========================
# MAIN
# =========================

def main():
    if not TG_TOKEN:
        raise RuntimeError(
            "TG_BOT_TOKEN is missing. Set it in your .env file."
        )

    init_db()

    application = Application.builder().token(TG_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("status", status_command))

    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    application.add_error_handler(error_handler)

    logger.info("STM PRO BOT starting in English mode...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
