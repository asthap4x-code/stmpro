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

try:
    from pyquotex.stable_api import Quotex
except ImportError:
    Quotex = None


# =========================================================
# STM PRO BOT - SINGLE FILE CONFIGURATION
# =========================================================

load_dotenv()

TG_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "").strip()

QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL", "").strip()
QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD", "").strip()

MIN_PAYOUT = float(os.getenv("MIN_PAYOUT", "80"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "5"))
DATABASE = "stm_history.db"

TIMEFRAME = 60
EXPIRY_SECONDS = 60
SIGNAL_LEAD_SECONDS = 7

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("STM_PRO_BOT")


# =========================================================
# MARKET PAIRS
# =========================================================

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
        "EURJPY": "EUR/JPY",
        "GBPJPY": "GBP/JPY",
        "EURAUD": "EUR/AUD",
        "GBPAUD": "GBP/AUD",
        "EURCHF": "EUR/CHF",
        "GBPCHF": "GBP/CHF",
        "AUDJPY": "AUD/JPY",
        "CADJPY": "CAD/JPY",
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
        "USDCHF_otc": "USD/CHF OTC",
        "NZDUSD_otc": "NZD/USD OTC",
        "EURGBP_otc": "EUR/GBP OTC",
        "EURJPY_otc": "EUR/JPY OTC",
        "GBPJPY_otc": "GBP/JPY OTC",
    },
}


# =========================================================
# DATABASE
# =========================================================

def db():
    return sqlite3.connect(DATABASE)


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                payout REAL NOT NULL,
                entry_price REAL,
                exit_price REAL,
                entry_time INTEGER NOT NULL,
                expiry INTEGER NOT NULL,
                result TEXT DEFAULT 'PENDING',
                mtg INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER PRIMARY KEY,
                mtg_enabled INTEGER DEFAULT 0,
                bot_enabled INTEGER DEFAULT 1
            )
        """)

        conn.commit()


def user_settings(user_id):
    with db() as conn:
        row = conn.execute(
            "SELECT mtg_enabled, bot_enabled FROM settings WHERE user_id=?",
            (user_id,),
        ).fetchone()

        if not row:
            conn.execute(
                "INSERT OR IGNORE INTO settings(user_id) VALUES(?)",
                (user_id,),
            )
            conn.commit()
            return {"mtg": False, "enabled": True}

        return {"mtg": bool(row[0]), "enabled": bool(row[1])}


def set_setting(user_id, field, value):
    if field not in ("mtg_enabled", "bot_enabled"):
        return

    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO settings(user_id) VALUES(?)",
            (user_id,),
        )
        conn.execute(
            f"UPDATE settings SET {field}=? WHERE user_id=?",
            (int(bool(value)), user_id),
        )
        conn.commit()


def save_signal(
    user_id,
    pair,
    direction,
    payout,
    entry_price,
    entry_time,
    mtg=False,
):
    with db() as conn:
        cursor = conn.execute("""
            INSERT INTO signals (
                user_id, pair, direction, payout,
                entry_price, entry_time, expiry,
                result, mtg, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """, (
            user_id,
            pair,
            direction,
            payout,
            entry_price,
            entry_time,
            EXPIRY_SECONDS,
            int(mtg),
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
        return cursor.lastrowid


def pending_signals():
    with db() as conn:
        return conn.execute("""
            SELECT id, user_id, pair, direction, entry_price,
                   entry_time, expiry, mtg
            FROM signals
            WHERE result='PENDING'
        """).fetchall()


def close_signal(signal_id, exit_price, result):
    with db() as conn:
        conn.execute("""
            UPDATE signals
            SET exit_price=?, result=?
            WHERE id=?
        """, (exit_price, result, signal_id))
        conn.commit()


def user_history(user_id, limit=10):
    with db() as conn:
        return conn.execute("""
            SELECT pair, direction, payout, entry_price,
                   exit_price, result, mtg, created_at
            FROM signals
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()


def consecutive_losses(user_id):
    rows = user_history(user_id, 20)
    count = 0

    for row in rows:
        result = row[5]

        if result == "PENDING":
            continue

        if result == "LOSS":
            count += 1
        elif result == "WIN":
            break

    return count


# =========================================================
# QUOTEX CONNECTION
# =========================================================

class MarketAPI:
    def __init__(self):
        self.client = None
        self.connected = False
        self.lock = asyncio.Lock()

    async def connect(self):
        if Quotex is None:
            raise RuntimeError(
                "PyQuotex is not installed. Check your installed package."
            )

        if not QUOTEX_EMAIL or not QUOTEX_PASSWORD:
            raise RuntimeError(
                "QUOTEX_EMAIL and QUOTEX_PASSWORD are missing from .env."
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
            raise RuntimeError("Quotex connection failed.")

        log.info("Quotex connected.")

    async def ensure(self):
        if self.client is None or not self.connected:
            await self.connect()

    async def candles(self, pair, count=80):
        await self.ensure()

        raw = await self.client.get_candles(
            pair,
            TIMEFRAME,
            int(time.time()),
            count * TIMEFRAME,
        )

        result = []

        if not isinstance(raw, (list, tuple)):
            return result

        for item in raw:
            if not isinstance(item, dict):
                continue

            try:
                result.append({
                    "time": int(item.get("time", 0)),
                    "open": float(item["open"]),
                    "close": float(item["close"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                })
            except (ValueError, TypeError, KeyError):
                continue

        result.sort(key=lambda candle: candle["time"])
        return result

    async def payout(self, pair):
        await self.ensure()

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

            if 0 < value <= 1:
                value *= 100

            if not 0 <= value <= 100:
                return None

            return value

        except Exception as exc:
            log.warning("Payout lookup failed for %s: %s", pair, exc)
            return None


market_api = MarketAPI()


# =========================================================
# STRATEGY AND MARKET FILTERS
# =========================================================

def ema(values, period):
    if len(values) < period:
        return []

    current = sum(values[:period]) / period
    output = [None] * (period - 1) + [current]
    multiplier = 2 / (period + 1)

    for value in values[period:]:
        current = (value - current) * multiplier + current
        output.append(current)

    return output


def candle_direction(candle):
    if candle["close"] > candle["open"]:
        return "UP"
    if candle["close"] < candle["open"]:
        return "DOWN"
    return "FLAT"


def analyze_market(raw_candles):
    # Exclude the currently forming candle.
    if len(raw_candles) < 35:
        return None

    now = int(time.time())
    current_candle_start = now - (now % TIMEFRAME)

    candles = [
        candle for candle in raw_candles
        if candle["time"] < current_candle_start
    ]

    if len(candles) < 30:
        return None

    closes = [c["close"] for c in candles]
    if any(price <= 0 for price in closes):
        return None

    fast = ema(closes, 5)
    slow = ema(closes, 13)

    if not fast or not slow or fast[-1] is None or slow[-1] is None:
        return None

    recent = candles[-8:]
    directions = [candle_direction(c) for c in recent]

    # Avoid a market after 3 or more consecutive candles
    # have closed in the same direction.
    streak = 1

    for i in range(len(directions) - 1, 0, -1):
        if (
            directions[i] != "FLAT"
            and directions[i] == directions[i - 1]
        ):
            streak += 1
        else:
            break

    if streak >= 3:
        return None

    # Avoid unusually large candles and sharp movement.
    ranges = [c["high"] - c["low"] for c in candles[-11:-1]]
    if not ranges:
        return None

    average_range = sum(ranges) / len(ranges)
    last_range = candles[-1]["high"] - candles[-1]["low"]

    if average_range <= 0 or last_range > average_range * 2.2:
        return None

    last_price = closes[-1]
    move = abs(closes[-1] - closes[-6]) / last_price * 100

    if move > 0.35:
        return None

    bullish = fast[-1] > slow[-1] and closes[-1] > fast[-1]
    bearish = fast[-1] < slow[-1] and closes[-1] < fast[-1]

    if bullish and directions[-1] == "UP" and streak < 3:
        direction = "CALL"
    elif bearish and directions[-1] == "DOWN" and streak < 3:
        direction = "PUT"
    else:
        return None

    separation = abs(fast[-1] - slow[-1]) / last_price * 100
    momentum = abs(closes[-1] - closes[-4]) / last_price * 100

    score = 60 + min(separation * 10, 15) + min(momentum * 2, 10)

    return {
        "direction": direction,
        "score": round(min(score, 85), 1),
        "price": last_price,
        "candle_time": candles[-1]["time"],
    }


# =========================================================
# TELEGRAM KEYBOARDS
# =========================================================

def home_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📡 Start Scanner", callback_data="scanner"),
            InlineKeyboardButton("⏹ Stop Scanner", callback_data="stop"),
        ],
        [
            InlineKeyboardButton("⚙ MTG Settings", callback_data="mtg"),
            InlineKeyboardButton("📊 Statistics", callback_data="stats"),
        ],
        [
            InlineKeyboardButton("📜 Signal History", callback_data="history"),
            InlineKeyboardButton("ℹ Help", callback_data="help"),
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
        [InlineKeyboardButton("⬅ Back", callback_data="home")],
    ])


def pairs_keyboard(category):
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
        InlineKeyboardButton("⬅ Back", callback_data="scanner")
    ])

    return InlineKeyboardMarkup(rows)


# =========================================================
# SIGNAL DELIVERY
# =========================================================

async def send_to_channel(context, text):
    if not TG_CHANNEL_ID:
        return

    try:
        await context.bot.send_message(
            chat_id=TG_CHANNEL_ID,
            text=text,
        )
    except Exception:
        log.exception("Could not post signal to configured channel.")


async def publish_signal(
    context,
    user_id,
    pair,
    payout,
    analysis,
    mtg=False,
):
    entry_time = int(time.time())
    signal_id = save_signal(
        user_id=user_id,
        pair=pair,
        direction=analysis["direction"],
        payout=payout,
        entry_price=analysis["price"],
        entry_time=entry_time,
        mtg=mtg,
    )

    direction = analysis["direction"]
    arrow = "🟢 CALL ⬆️" if direction == "CALL" else "🔴 PUT ⬇️"
    mtg_text = "\n⚠️ MARTINGALE STEP 1" if mtg else ""

    message = (
        "🚀 STM PRO BOT\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💱 Pair: {pair}\n"
        f"📍 Direction: {arrow}\n"
        f"💰 Payout: {payout:.0f}%\n"
        f"⏱ Expiry: 1 Minute\n"
        f"🕒 Entry: {datetime.fromtimestamp(entry_time).strftime('%H:%M:%S')}\n"
        f"💵 Entry Price: {analysis['price']}\n"
        f"📊 Setup Score: {analysis['score']}/100\n"
        f"🆔 Signal ID: {signal_id}"
        f"{mtg_text}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "       STM PRO BOT\n"
        "⚠️ Signals are not guaranteed wins."
    )

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=message,
        )
    except Exception:
        log.exception("Failed to deliver signal to user %s", user_id)

    await send_to_channel(context, message)


# =========================================================
# SCANNER
# =========================================================

async def scan_once(context, user_id, pair, mtg=False):
    async with market_api.lock:
        payout = await market_api.payout(pair)

        if payout is None:
            return "Payout could not be verified. No signal sent."

        if payout < MIN_PAYOUT:
            return f"Skipped: payout {payout:.0f}% is below {MIN_PAYOUT:.0f}%."

        candles = await market_api.candles(pair)

    analysis = analyze_market(candles)

    if analysis is None:
        return "No signal: market filters rejected this setup."

    await publish_signal(
        context=context,
        user_id=user_id,
        pair=pair,
        payout=payout,
        analysis=analysis,
        mtg=mtg,
    )

    return "Signal sent."


async def scanner_loop(context, user_id, pair):
    while True:
        settings = user_settings(user_id)

        if not settings["enabled"]:
            break

        now = time.time()
        next_minute = (int(now) // 60 + 1) * 60
        target = next_minute - SIGNAL_LEAD_SECONDS

        if target > now:
            await asyncio.sleep(target - now)

        settings = user_settings(user_id)
        if not settings["enabled"]:
            break

        if consecutive_losses(user_id) >= 2:
            await context.bot.send_message(
                chat_id=user_id,
                text="Risk protection: two consecutive losses detected. "
                     "The next signal has been skipped.",
            )
            await asyncio.sleep(60)
            continue

        try:
            result = await scan_once(context, user_id, pair)
            log.info("Scan result for %s: %s", pair, result)
        except Exception:
            log.exception("Scanner error for %s", pair)
            await context.bot.send_message(
                chat_id=user_id,
                text="Scanner error. Check the Quotex connection and logs.",
            )

        await asyncio.sleep(60)


# =========================================================
# RESULT CHECKER
# =========================================================

async def result_checker(app):
    while True:
        try:
            now = int(time.time())

            for row in pending_signals():
                signal_id, user_id, pair, direction, entry_price, entry_time, expiry, mtg = row

                if now < entry_time + expiry:
                    continue

                async with market_api.lock:
                    candles = await market_api.candles(pair, 10)

                if not candles:
                    continue

                # Find the candle at/after the signal's expiry.
                target_time = entry_time + expiry
                candidates = [
                    candle for candle in candles
                    if candle["time"] <= target_time
                ]

                if not candidates:
                    continue

                exit_price = candidates[-1]["close"]

                if entry_price is None:
                    result = "UNKNOWN"
                elif exit_price == entry_price:
                    result = "DRAW"
                elif direction == "CALL":
                    result = "WIN" if exit_price > entry_price else "LOSS"
                else:
                    result = "WIN" if exit_price < entry_price else "LOSS"

                close_signal(signal_id, exit_price, result)

                message = (
                    "📋 STM PRO BOT — RESULT\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"Pair: {pair}\n"
                    f"Direction: {direction}\n"
                    f"Opening Price: {entry_price}\n"
                    f"Closing Price: {exit_price}\n"
                    f"Result: {result}\n"
                    "━━━━━━━━━━━━━━━━━━━━"
                )

                try:
                    await app.bot.send_message(
                        chat_id=user_id,
                        text=message,
                    )
                except Exception:
                    log.exception("Could not send result to user.")

                await send_to_channel(
                    type("Context", (), {"bot": app.bot})(),
                    message,
                )

                # MTG is proposed only after a confirmed first-step loss.
                settings = user_settings(user_id)

                if result == "LOSS" and not mtg and settings["mtg"]:
                    await app.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "⚠️ First signal resulted in a loss.\n"
                            "Prepare for MTG step 1 on the next candle.\n"
                            "The next setup must pass all market filters."
                        ),
                    )

        except Exception:
            log.exception("Result checker error.")

        await asyncio.sleep(3)


# =========================================================
# COMMAND HANDLERS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    await update.effective_message.reply_text(
        "Welcome to STM PRO BOT.\n\n"
        "Language: English\n"
        f"Minimum payout: {MIN_PAYOUT:.0f}%\n"
        "Expiry: 1 minute\n"
        "Mode: Signal-only — no automatic trading.\n\n"
        "Select an option:",
        reply_markup=home_keyboard(),
    )

    # Ask MTG preference at startup.
    await update.effective_message.reply_text(
        "Enable Martingale (MTG) step 1?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Yes", callback_data="mtg_yes"),
                InlineKeyboardButton("No", callback_data="mtg_no"),
            ]
        ]),
    )


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Choose a market category:",
        reply_markup=category_keyboard(),
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = user_history(update.effective_user.id, 10)

    if not rows:
        await update.effective_message.reply_text("No signal history yet.")
        return

    lines = ["📜 STM PRO BOT — SIGNAL HISTORY\n"]

    for row in rows:
        pair, direction, payout, entry, exit_price, result, mtg, created = row
        lines.append(
            f"{pair} | {direction} | {result}\n"
            f"Payout: {payout:.0f}% | Entry: {entry} | Exit: {exit_price}\n"
            f"MTG: {'Yes' if mtg else 'No'} | {created[:19]}\n"
        )

    await update.effective_message.reply_text("\n".join(lines))


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = user_history(update.effective_user.id, 1000)
    closed = [row for row in rows if row[5] in ("WIN", "LOSS", "DRAW")]

    wins = sum(1 for row in closed if row[5] == "WIN")
    losses = sum(1 for row in closed if row[5] == "LOSS")
    accuracy = (wins / len(closed) * 100) if closed else 0

    await update.effective_message.reply_text(
        "📊 STM PRO BOT STATISTICS\n\n"
        f"Closed signals: {len(closed)}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"Win rate: {accuracy:.1f}%\n\n"
        "Historical results do not predict future outcomes."
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting(update.effective_user.id, "bot_enabled", False)
    await update.effective_message.reply_text("Scanner stopped.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "STM PRO BOT Help\n\n"
        "/start - Main menu and MTG preference\n"
        "/scan - Select a market and asset\n"
        "/history - Recent signals\n"
        "/stats - Signal statistics\n"
        "/stop - Stop scanner\n\n"
        "Signals are informational. No trade is executed automatically."
    )


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data or ""

    if data == "home":
        await query.edit_message_text(
            "STM PRO BOT — Main Menu",
            reply_markup=home_keyboard(),
        )
        return

    if data == "scanner":
        await query.edit_message_text(
            "Choose a market category:",
            reply_markup=category_keyboard(),
        )
        return

    if data == "stop":
        set_setting(user_id, "bot_enabled", False)
        await query.edit_message_text("Scanner stopped.")
        return

    if data == "mtg":
        await query.edit_message_text(
            "Enable Martingale step 1?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Yes", callback_data="mtg_yes"),
                    InlineKeyboardButton("No", callback_data="mtg_no"),
                ],
                [InlineKeyboardButton("Back", callback_data="home")],
            ]),
        )
        return

    if data == "mtg_yes":
        set_setting(user_id, "mtg_enabled", True)
        await query.edit_message_text("MTG enabled.")
        return

    if data == "mtg_no":
        set_setting(user_id, "mtg_enabled", False)
        await query.edit_message_text("MTG disabled.")
        return

    if data == "history":
        rows = user_history(user_id, 10)
        if not rows:
            await query.edit_message_text("No signal history yet.")
            return

        lines = ["📜 Recent Signal History\n"]
        for row in rows:
            pair, direction, payout, entry, exit_price, result, mtg, created = row
            lines.append(
                f"{pair} | {direction} | {result}\n"
                f"Payout {payout:.0f}% | Entry {entry} | Exit {exit_price}"
            )

        await query.edit_message_text("\n\n".join(lines))
        return

    if data == "stats":
        rows = user_history(user_id, 1000)
        closed = [r for r in rows if r[5] in ("WIN", "LOSS", "DRAW")]
        wins = sum(1 for r in closed if r[5] == "WIN")
        losses = sum(1 for r in closed if r[5] == "LOSS")
        rate = wins / len(closed) * 100 if closed else 0

        await query.edit_message_text(
            f"Closed signals: {len(closed)}\n"
            f"Wins: {wins}\nLosses: {losses}\nWin rate: {rate:.1f}%"
        )
        return

    if data == "help":
        await query.edit_message_text(
            "Use /scan to select a market.\n"
            "Use /history to review signals.\n"
            "Use /stats to review closed results.\n"
            "Use /stop to stop the scanner."
        )
        return

    if data.startswith("cat:"):
        category = data.split(":", 1)[1]

        if category not in MARKETS:
            await query.edit_message_text("Unknown market category.")
            return

        await query.edit_message_text(
            "Select an asset:",
            reply_markup=pairs_keyboard(category),
        )
        return

    if data.startswith("pair:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.edit_message_text("Invalid asset.")
            return

        category, pair = parts[1], parts[2]

        if category not in MARKETS or pair not in MARKETS[category]:
            await query.edit_message_text("Unknown asset.")
            return

        set_setting(user_id, "bot_enabled", True)

        await query.edit_message_text(
            f"Scanning {pair}. Please wait..."
        )

        try:
            result = await scan_once(context, user_id, pair)
            await query.message.reply_text(result)
        except Exception as exc:
            log.exception("Manual scan failed.")
            await query.message.reply_text(
                "Scan failed. Check Quotex API compatibility.\n"
                f"Error: {str(exc)[:200]}"
            )


# =========================================================
# APP STARTUP
# =========================================================

async def post_init(app):
    app.create_task(result_checker(app))


def main():
    if not TG_TOKEN:
        raise RuntimeError(
            "TG_BOT_TOKEN is missing. Check your .env file."
        )

    init_db()

    application = (
        Application.builder()
        .token(TG_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(callback_handler))

    log.info("STM PRO BOT started in English mode.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
