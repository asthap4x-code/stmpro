
import asyncio
import logging
import math
import os
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

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

from pyquotex.stable_api import Quotex

from ai_strategy import analyze_ai
from all_strategy import analyze_all


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

TG_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "").strip()

QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL", "").strip()
QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD", "").strip()

BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Asia/Male")
MIN_PAYOUT = float(os.getenv("MIN_PAYOUT", "80"))
MAX_ASSETS = int(os.getenv("MAX_ASSETS", "100"))
SCAN_CONCURRENCY = int(os.getenv("SCAN_CONCURRENCY", "4"))
SCAN_INTERVAL_SECONDS = float(
    os.getenv("SCAN_INTERVAL_SECONDS", "5")
)
MIN_SCORE = float(os.getenv("MIN_SCORE", "1.5"))

# Publish 5 seconds before the next minute begins.
SIGNAL_LEAD_SECONDS = 5

DB_FILE = "stm_history.sqlite3"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("STM_PRO_BOT")

try:
    TZ = ZoneInfo(BOT_TIMEZONE)
except Exception:
    try:
        TZ = ZoneInfo("Asia/Male")
    except Exception:
        from datetime import timezone
        TZ = timezone.utc


# ============================================================
# GLOBAL STATE
# ============================================================

client = None
client_lock = asyncio.Lock()
scan_task = None

STATE = {
    "running": False,
    "owner_id": None,
    "category": "ALL",
    "assets": [],
    "strategy": "ALL",
}

ASSET_CACHE = []
ASSET_LOCK = asyncio.Lock()
SEMAPHORE = asyncio.Semaphore(max(1, SCAN_CONCURRENCY))


# ============================================================
# SQLITE DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT NOT NULL,
                direction TEXT NOT NULL,
                strategy TEXT NOT NULL,
                score REAL DEFAULT 0,
                payout REAL DEFAULT 0,
                signal_time INTEGER NOT NULL,
                entry_time INTEGER NOT NULL,
                result TEXT DEFAULT 'PENDING',
                result_source TEXT DEFAULT 'CANDLE_ESTIMATE',
                mtg1_result TEXT DEFAULT 'NOT_USED',
                reason TEXT DEFAULT '',
                UNIQUE(asset, entry_time, strategy)
            )
        """)
        conn.commit()


def save_signal(
    asset,
    direction,
    strategy,
    score,
    payout,
    signal_time,
    entry_time,
    reason,
):
    with db_connect() as conn:
        cur = conn.execute("""
            INSERT OR IGNORE INTO signals (
                asset, direction, strategy, score, payout,
                signal_time, entry_time, reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            asset,
            direction,
            strategy,
            score,
            payout,
            signal_time,
            entry_time,
            reason,
        ))
        conn.commit()

        # Do not republish a duplicate signal.
        if cur.rowcount == 0:
            return None

        return cur.lastrowid


def update_result(signal_id, result, source, mtg1_result="NOT_USED"):
    with db_connect() as conn:
        conn.execute("""
            UPDATE signals
            SET result = ?,
                result_source = ?,
                mtg1_result = ?
            WHERE id = ?
        """, (result, source, mtg1_result, signal_id))
        conn.commit()


# ============================================================
# PYQUOTEX CONNECTION
# ============================================================

async def connect_quotex():
    """
    Connect to the unofficial PyQuotex client.
    This bot does not place trades.
    """
    global client

    async with client_lock:
        if client is not None:
            try:
                if await client.check_connect():
                    return True
            except Exception:
                pass

        if not QUOTEX_EMAIL or not QUOTEX_PASSWORD:
            log.error("Set QUOTEX_EMAIL and QUOTEX_PASSWORD in .env")
            return False

        try:
            client = Quotex(
                email=QUOTEX_EMAIL,
                password=QUOTEX_PASSWORD,
                lang="en",
            )

            ok, message = await client.connect()

            if not ok:
                log.error("Quotex connection failed: %s", message)
                client = None
                return False

            log.info("PyQuotex connected.")
            return True

        except Exception:
            log.exception("PyQuotex connection error")
            client = None
            return False


async def close_quotex():
    global client

    async with client_lock:
        if client is not None:
            try:
                await client.close()
            except Exception:
                log.exception("Error closing Quotex connection")
            finally:
                client = None


async def get_asset_list():
    """
    Get asset names from PyQuotex.
    API support can vary between installed versions.
    """
    global ASSET_CACHE

    async with ASSET_LOCK:
        if ASSET_CACHE:
            return ASSET_CACHE[:]

        if not await connect_quotex():
            return []

        try:
            data = await client.get_all_assets()

            if isinstance(data, dict):
                assets = list(data.keys())
            elif isinstance(data, (list, tuple, set)):
                assets = list(data)
            else:
                assets = []

            assets = [
                str(a) for a in assets
                if a and isinstance(a, (str, int))
            ]

            ASSET_CACHE = sorted(set(assets))[:MAX_ASSETS]
            log.info("Loaded %d assets", len(ASSET_CACHE))
            return ASSET_CACHE[:]

        except Exception:
            log.exception("Could not load Quotex assets")
            return []


async def get_candles(asset, count=100):
    """
    PyQuotex get_candles(asset, end_from_time, offset, period).
    Returns normalized candle dictionaries.
    """
    if not await connect_quotex():
        return []

    try:
        end_time = int(time.time())

        raw = await client.get_candles(
            asset,
            end_time,
            count * 60,
            60,
        )

        if not raw:
            return []

        result = []

        for candle in raw:
            if not isinstance(candle, dict):
                continue

            try:
                ts = int(
                    candle.get("time", candle.get("timestamp", 0))
                )

                if ts > 10_000_000_000:
                    ts //= 1000

                op = float(candle["open"])
                cl = float(candle["close"])
                hi = float(candle["high"])
                lo = float(candle["low"])

                if ts <= 0:
                    continue

                result.append({
                    "time": ts,
                    "open": op,
                    "close": cl,
                    "high": hi,
                    "low": lo,
                })

            except (TypeError, ValueError, KeyError):
                continue

        result.sort(key=lambda c: c["time"])

        # Remove duplicate timestamps.
        unique = {c["time"]: c for c in result}
        return list(unique.values())

    except Exception:
        log.warning("Candle request failed for %s", asset, exc_info=True)
        return []


def get_payout(asset):
    """
    Read payout if supported by the installed PyQuotex version.
    Return None if payout cannot be verified.
    """
    if client is None:
        return None

    try:
        value = client.get_payout_by_asset(asset, timeframe="1")

        if asyncio.iscoroutine(value):
            return None

        if isinstance(value, dict):
            value = value.get("1M", value.get("profit"))

        if value is None:
            return None

        value = float(value)

        # Some versions may return decimal payout instead of percent.
        if 0 < value <= 1:
            value *= 100

        if 0 <= value <= 100:
            return value

    except Exception:
        log.debug("Payout unavailable for %s", asset, exc_info=True)

    return None


# ============================================================
# ASSET CATEGORIES
# ============================================================

CRYPTO_TOKENS = (
    "BTC", "ETH", "LTC", "XRP", "DOGE",
    "ADA", "SOL", "BNB", "DOT", "TRX",
)

COMMODITY_TOKENS = (
    "XAU", "XAG", "GOLD", "SILVER",
    "OIL", "BRENT", "WTI", "NGAS",
)


def category_of(asset):
    upper = asset.upper()

    if any(token in upper for token in CRYPTO_TOKENS):
        return "CRYPTO"

    if any(token in upper for token in COMMODITY_TOKENS):
        return "COMMODITIES"

    if "_OTC" in upper:
        return "CURRENCY"

    return "FOREX"


def category_label(category):
    return {
        "ALL": "All Markets",
        "FOREX": "Forex",
        "CURRENCY": "Currency OTC",
        "CRYPTO": "Crypto",
        "COMMODITIES": "Commodities",
    }.get(category, category)


# ============================================================
# TELEGRAM UI
# ============================================================

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌍 All Markets", callback_data="cat:ALL"),
        ],
        [
            InlineKeyboardButton("💱 Forex", callback_data="cat:FOREX"),
            InlineKeyboardButton("💹 Currency OTC", callback_data="cat:CURRENCY"),
        ],
        [
            InlineKeyboardButton("₿ Crypto", callback_data="cat:CRYPTO"),
            InlineKeyboardButton("🪙 Commodities", callback_data="cat:COMMODITIES"),
        ],
        [
            InlineKeyboardButton("📊 Bot Status", callback_data="status"),
        ],
    ])


async def show_pairs(query, category):
    assets = await get_asset_list()

    if category != "ALL":
        assets = [a for a in assets if category_of(a) == category]

    if not assets:
        await query.edit_message_text(
            "❌ Quotex থেকে এই ক্যাটাগরির asset list পাওয়া যায়নি।\n"
            "লগ চেক করো এবং PyQuotex connection পরীক্ষা করো।",
            reply_markup=main_menu(),
        )
        return

    # Avoid huge Telegram inline keyboards.
    assets = assets[:60]
    buttons = []

    for i in range(0, len(assets), 2):
        row = []
        for asset in assets[i:i + 2]:
            row.append(
                InlineKeyboardButton(
                    f"➕ {asset}",
                    callback_data=f"asset:{asset}",
                )
            )
        buttons.append(row)

    buttons.extend([
        [
            InlineKeyboardButton(
                "✅ Select All Listed",
                callback_data="select_all",
            )
        ],
        [
            InlineKeyboardButton("➡️ Choose Strategy", callback_data="strategy_menu"),
            InlineKeyboardButton("⬅️ Back", callback_data="home"),
        ],
    ])

    STATE["category"] = category
    STATE["assets"] = []

    await query.edit_message_text(
        f"📂 Category: {category_label(category)}\n\n"
        "Pair নির্বাচন করো। একাধিক pair নির্বাচন করতে পারো।\n"
        "Select All Listed চাপলে দেখানো pair-গুলো নির্বাচন হবে.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def pair_menu():
    assets = STATE["assets"]
    buttons = []

    for i in range(0, min(len(assets), 40), 2):
        row = []
        for asset in assets[i:i + 2]:
            row.append(
                InlineKeyboardButton(
                    f"✓ {asset}",
                    callback_data=f"remove:{asset}",
                )
            )
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton("➕ Add More Pairs", callback_data="back_pairs"),
        InlineKeyboardButton("➡️ Strategy", callback_data="strategy_menu"),
    ])

    return InlineKeyboardMarkup(buttons)


def strategy_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 AI Strategy", callback_data="strat:AI"),
        ],
        [
            InlineKeyboardButton("📈 All Strategy", callback_data="strat:ALL"),
        ],
        [
            InlineKeyboardButton("▶️ Start Scanning", callback_data="start"),
            InlineKeyboardButton("⏹ Stop", callback_data="stop"),
        ],
        [
            InlineKeyboardButton("🏠 Home", callback_data="home"),
        ],
    ])


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 STM PRO BOT\n\n"
        "PyQuotex market-data connection ব্যবহার করে signal scan করবে।\n"
        "এটি signal-only bot; স্বয়ংক্রিয়ভাবে trade open করে না।\n\n"
        "Market category নির্বাচন করো:",
        reply_markup=main_menu(),
    )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    global scan_task

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "home":
        await query.edit_message_text(
            "Market category নির্বাচন করো:",
            reply_markup=main_menu(),
        )
        return

    if data.startswith("cat:"):
        await show_pairs(query, data.split(":", 1)[1])
        return

    if data.startswith("asset:"):
        asset = data.split(":", 1)[1]

        if asset not in STATE["assets"]:
            STATE["assets"].append(asset)

        await query.edit_message_text(
            "Selected pairs:\n"
            + "\n".join(STATE["assets"])
            + "\n\nআরও pair নির্বাচন করতে Back/Add More ব্যবহার করো।",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("➕ Add More Pairs", callback_data="back_pairs"),
                    InlineKeyboardButton("➡️ Strategy", callback_data="strategy_menu"),
                ],
                [
                    InlineKeyboardButton("🏠 Home", callback_data="home"),
                ],
            ]),
        )
        return

    if data == "select_all":
        assets = await get_asset_list()
        if STATE["category"] != "ALL":
            assets = [
                a for a in assets
                if category_of(a) == STATE["category"]
            ]
        STATE["assets"] = assets[:60]

        await query.edit_message_text(
            f"✅ Selected {len(STATE['assets'])} pairs.\n"
            "এখন Strategy নির্বাচন করো।",
            reply_markup=strategy_menu(),
        )
        return

    if data == "back_pairs":
        await show_pairs(query, STATE["category"])
        return

    if data == "strategy_menu":
        await query.edit_message_text(
            f"Selected pairs: {len(STATE['assets'])}\n"
            f"Current strategy: {STATE['strategy']}\n\n"
            "Strategy নির্বাচন করো:",
            reply_markup=strategy_menu(),
        )
        return

    if data.startswith("strat:"):
        STATE["strategy"] = data.split(":", 1)[1]
        await query.edit_message_text(
            f"Strategy set: {STATE['strategy']}\n"
            "Start Scanning চাপো।",
            reply_markup=strategy_menu(),
        )
        return

    if data == "status":
        await query.edit_message_text(
            f"Running: {STATE['running']}\n"
            f"Selected pairs: {len(STATE['assets'])}\n"
            f"Strategy: {STATE['strategy']}",
            reply_markup=main_menu(),
        )
        return

    if data == "start":
        if not STATE["assets"]:
            await query.edit_message_text(
                "❌ আগে অন্তত একটি pair নির্বাচন করো।",
                reply_markup=main_menu(),
            )
            return

        if not TG_CHANNEL_ID:
            await query.edit_message_text(
                "❌ .env-এ TG_CHANNEL_ID সেট করা নেই।",
                reply_markup=strategy_menu(),
            )
            return

        if not await connect_quotex():
            await query.edit_message_text(
                "❌ Quotex connection failed.\n"
                "Credentials এবং PyQuotex install পরীক্ষা করো।",
                reply_markup=strategy_menu(),
            )
            return

        STATE["owner_id"] = user_id
        STATE["running"] = True

        if scan_task is None or scan_task.done():
            scan_task = asyncio.create_task(
                scanning_loop(context.application)
            )

        await query.edit_message_text(
            "✅ STM PRO BOT scanning started.\n"
            f"Pairs: {len(STATE['assets'])}\n"
            f"Strategy: {STATE['strategy']}\n"
            f"Minimum payout: {MIN_PAYOUT}%\n\n"
            "Signal channel-এ publish হবে।",
            reply_markup=strategy_menu(),
        )
        return

    if data == "stop":
        if STATE["owner_id"] not in (None, user_id):
            await query.edit_message_text(
                "শুধু bot শুরু করা user scanning বন্ধ করতে পারবে।",
                reply_markup=strategy_menu(),
            )
            return

        STATE["running"] = False
        await query.edit_message_text(
            "⏹ Scanning stopped.",
            reply_markup=main_menu(),
        )


# ============================================================
# STRATEGY ANALYSIS
# ============================================================

def analyze_candles(candles, strategy):
    if strategy == "AI":
        return analyze_ai(candles)

    return analyze_all(candles)


async def analyze_asset(asset):
    async with SEMAPHORE:
        candles = await get_candles(asset, 100)

    if len(candles) < 60:
        return None

    # Require recent candle data.
    latest_time = candles[-1]["time"]
    if abs(time.time() - latest_time) > 150:
        log.debug("Skipping stale candles for %s", asset)
        return None

    payout = get_payout(asset)

    # Unknown payout is not treated as 80%.
    if payout is None or payout < MIN_PAYOUT:
        return None

    try:
        result = analyze_candles(candles, STATE["strategy"])
    except Exception:
        log.exception("Strategy failed for %s", asset)
        return None

    if not result:
        return None

    direction = str(result.get("direction", "")).upper()
    score = float(result.get("score", 0))
    reason = str(result.get("reason", ""))

    if direction not in ("CALL", "PUT"):
        return None

    if score < MIN_SCORE:
        return None

    # Avoid extreme last-candle spikes.
    last = candles[-1]
    ranges = [
        max(c["high"] - c["low"], 0)
        for c in candles[-21:-1]
    ]

    if ranges:
        average_range = sum(ranges) / len(ranges)
        last_range = max(last["high"] - last["low"], 0)

        if average_range > 0 and last_range > average_range * 2.5:
            log.info("Skipped spike candle: %s", asset)
            return None

    return {
        "asset": asset,
        "direction": direction,
        "score": score,
        "payout": payout,
        "reason": reason,
        "strategy": STATE["strategy"],
    }


# ============================================================
# SIGNAL PUBLISHING
# ============================================================

def next_minute_timestamp(now=None):
    if now is None:
        now = time.time()

    return (int(now) // 60 + 1) * 60


def format_time(timestamp):
    return datetime.fromtimestamp(
        timestamp, TZ
    ).strftime("%H:%M:%S")


async def publish_signal(application, signal, entry_time):
    now = int(time.time())

    # Skip stale signals. Never publish a signal after entry.
    if now >= entry_time - 2:
        log.info("Skipped late signal for %s", signal["asset"])
        return

    signal_time = now

    signal_id = save_signal(
        asset=signal["asset"],
        direction=signal["direction"],
        strategy=signal["strategy"],
        score=signal["score"],
        payout=signal["payout"],
        signal_time=signal_time,
        entry_time=entry_time,
        reason=signal["reason"],
    )

    if signal_id is None:
        return

    arrow = "🟢 CALL ⬆️" if signal["direction"] == "CALL" else "🔴 PUT ⬇️"

    message = (
        "━━━━━━━━━━━━━━━━━━\n"
        "       🟦 STM PRO BOT 🟦\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💱 Pair: {signal['asset']}\n"
        f"📊 Direction: {arrow}\n"
        f"⏱ Expiry: 1 Minute\n"
        f"🎯 Entry Time: {format_time(entry_time)}\n"
        f"💰 Payout: {signal['payout']:.0f}%\n"
        f"🧠 Strategy: {signal['strategy']}\n"
        f"⭐ Score: {signal['score']:.2f}\n\n"
        f"📝 Reason: {signal['reason'] or 'Strategy conditions matched'}\n\n"
        "⚠️ Signal only. Result is estimated from market candles; "
        "it is not a broker-confirmed trade result.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "         STM PRO BOT\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    try:
        await application.bot.send_message(
            chat_id=TG_CHANNEL_ID,
            text=message,
        )
        log.info("Signal published: %s %s", signal["asset"], signal["direction"])

        asyncio.create_task(
            resolve_signal(signal_id, signal, entry_time, application)
        )

    except Exception:
        log.exception("Could not publish signal")


# ============================================================
# RESULT CHECKING
# ============================================================

def determine_candle_result(direction, candle):
    op = float(candle["open"])
    cl = float(candle["close"])

    if cl == op:
        return "DRAW"

    if direction == "CALL":
        return "WIN" if cl > op else "LOSS"

    return "WIN" if cl < op else "LOSS"


async def find_candle(asset, target_time):
    """
    Fetch recent candles and find the candle whose timestamp
    matches target_time. Retries because broker data can arrive late.
    """
    for _ in range(6):
        candles = await get_candles(asset, 8)

        for candle in candles:
            if int(candle["time"]) == int(target_time):
                return candle

        await asyncio.sleep(2)

    return None


async def resolve_signal(signal_id, signal, entry_time, application):
    """
    Result is inferred from the 1-minute candle.
    It is not a real account trade settlement because this bot
    does not place trades and therefore has no broker order ID.
    """
    try:
        # Wait until the one-minute expiry has ended.
        await asyncio.sleep(max(0, entry_time + 62 - time.time()))

        candle = await find_candle(signal["asset"], entry_time)

        if candle is None:
            update_result(
                signal_id,
                "UNKNOWN",
                "CANDLE_DATA_UNAVAILABLE",
            )
            await application.bot.send_message(
                chat_id=TG_CHANNEL_ID,
                text=(
                    f"⚠️ RESULT UNAVAILABLE\n"
                    f"Pair: {signal['asset']}\n"
                    "Expiry candle could not be verified."
                ),
            )
            return

        result = determine_candle_result(
            signal["direction"],
            candle,
        )

        mtg1_result = "NOT_USED"

        # MTG1: if first candle loses, evaluate the next candle
        # in the same direction. This is still only an estimate.
        if result == "LOSS":
            next_candle = await find_candle(
                signal["asset"],
                entry_time + 60,
            )

            if next_candle is None:
                mtg1_result = "UNKNOWN"
            else:
                mtg1_result = determine_candle_result(
                    signal["direction"],
                    next_candle,
                )

        update_result(
            signal_id,
            result,
            "CANDLE_ESTIMATE",
            mtg1_result,
        )

        if result == "WIN":
            result_text = "✅ WIN"
        elif result == "LOSS":
            result_text = "❌ LOSS"
        else:
            result_text = "➖ DRAW"

        if mtg1_result == "WIN":
            mtg_text = "✅ MTG1 WIN (estimated)"
        elif mtg1_result == "LOSS":
            mtg_text = "❌ MTG1 LOSS (estimated)"
        elif mtg1_result == "UNKNOWN":
            mtg_text = "⚠️ MTG1 result unavailable"
        else:
            mtg_text = "Not used"

        await application.bot.send_message(
            chat_id=TG_CHANNEL_ID,
            text=(
                "━━━━━━━━━━━━━━━━━━\n"
                "      📊 STM PRO RESULT\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💱 Pair: {signal['asset']}\n"
                f"📈 Direction: {signal['direction']}\n"
                f"🏁 Result: {result_text}\n"
                f"🔁 MTG1: {mtg_text}\n\n"
                "ℹ️ Result source: Quotex candle estimate.\n"
                "This is not verified against an actual broker order.\n"
                "━━━━━━━━━━━━━━━━━━"
            ),
        )

    except Exception:
        log.exception("Result resolution failed for signal %s", signal_id)
        update_result(
            signal_id,
            "UNKNOWN",
            "RESULT_CHECK_ERROR",
        )


# ============================================================
# SCANNING LOOP
# ============================================================

async def scanning_loop(application):
    log.info("Scanning loop started")

    try:
        while STATE["running"]:
            started = time.time()

            if not await connect_quotex():
                await asyncio.sleep(10)
                continue

            assets = STATE["assets"][:]

            if not assets:
                await asyncio.sleep(2)
                continue

            tasks = [
                analyze_asset(asset)
                for asset in assets
            ]

            outcomes = await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

            signals = []

            for item in outcomes:
                if isinstance(item, Exception):
                    log.warning("Asset scan error: %s", item)
                elif item:
                    signals.append(item)

            if signals and STATE["running"]:
                # Reserve enough time for the signal to be sent.
                entry_time = next_minute_timestamp()

                # If scanning already took too long, skip this cycle.
                if entry_time - time.time() <= 2:
                    entry_time += 60

                publish_time = entry_time - SIGNAL_LEAD_SECONDS
                delay = publish_time - time.time()

                if delay > 0:
                    await asyncio.sleep(delay)

                # Re-check after waiting; never send expired signals.
                if STATE["running"] and time.time() < entry_time - 1:
                    for signal in signals:
                        await publish_signal(
                            application,
                            signal,
                            entry_time,
                        )

            elapsed = time.time() - started
            await asyncio.sleep(
                max(0.5, SCAN_INTERVAL_SECONDS - elapsed)
            )

    except asyncio.CancelledError:
        log.info("Scanning loop cancelled")
        raise
    except Exception:
        log.exception("Scanning loop crashed")
    finally:
        log.info("Scanning loop stopped")


# ============================================================
# APP START / STOP
# ============================================================

async def post_init(application):
    init_db()
    log.info("STM PRO BOT initialized")


async def post_shutdown(application):
    STATE["running"] = False
    await close_quotex()


def main():
    if not TG_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN is missing from .env")

    init_db()

    application = (
        Application.builder()
        .token(TG_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CallbackQueryHandler(button_handler))

    log.info("STM PRO BOT starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
