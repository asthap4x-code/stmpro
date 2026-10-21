
"""
STM PRO BOT — Main
Telegram + PyQuotex data adapter + SQLite history.
Signal-only mode; does not place trades.
"""

import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

from ai_strategy import analyze_ai
from all_strategy import analyze_all

load_dotenv()

TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "").strip()
Q_EMAIL = os.getenv("QUOTEX_EMAIL", "").strip()
Q_PASSWORD = os.getenv("QUOTEX_PASSWORD", "").strip()
TIMEZONE = os.getenv("BOT_TIMEZONE", "Asia/Male")
MIN_PAYOUT = float(os.getenv("MIN_PAYOUT", "80"))
MAX_ASSETS = int(os.getenv("MAX_ASSETS", "100"))
CONCURRENCY = int(os.getenv("SCAN_CONCURRENCY", "8"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "5"))
MIN_SCORE = float(os.getenv("MIN_SCORE", "1.5"))
DB_FILE = "stm_history.sqlite3"

TZ = ZoneInfo(TIMEZONE)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("STM-PRO")

# Per-user UI state
STATE = {}
SCANNER_TASK = None
SCANNER_RUNNING = False
QUOTEX = None
SEMAPHORE = asyncio.Semaphore(max(1, CONCURRENCY))


# -------------------- DATABASE --------------------

def db():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            asset TEXT,
            direction TEXT,
            strategy TEXT,
            entry_time INTEGER,
            payout REAL,
            score REAL,
            reason TEXT,
            result TEXT DEFAULT 'PENDING',
            details TEXT,
            created_at INTEGER
        )
        """)


def already_sent(key):
    with db() as conn:
        return conn.execute(
            "SELECT 1 FROM signals WHERE signal_key=?", (key,)
        ).fetchone() is not None


def save_signal(item, entry_time, strategy):
    key = f"{item['asset']}|{entry_time}|{strategy}"
    with db() as conn:
        cur = conn.execute("""
        INSERT OR IGNORE INTO signals
        (signal_key,asset,direction,strategy,entry_time,payout,
         score,reason,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            key, item["asset"], item["direction"], strategy,
            entry_time, item["payout"], item["score"],
            item["reason"], int(time.time())
        ))
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT id FROM signals WHERE signal_key=?", (key,)
            ).fetchone()
            return row["id"] if row else None
        return cur.lastrowid


def update_result(signal_id, result, details):
    with db() as conn:
        conn.execute(
            "UPDATE signals SET result=?, details=? WHERE id=?",
            (result, details, signal_id)
        )


# -------------------- QUOTEX CONNECTOR --------------------

class QuotexFeed:
    """
    PyQuotex is unofficial. Exact API varies between forks.
    Verify the installed package before relying on live data.
    """

    def __init__(self):
        self.client = None

    async def connect(self):
        if not Q_EMAIL or not Q_PASSWORD:
            raise RuntimeError(
                "Set QUOTEX_EMAIL and QUOTEX_PASSWORD in .env"
            )

        try:
            from pyquotex.stable_api import Quotex
        except ImportError:
            from pyquotex import Quotex

        self.client = Quotex(
            email=Q_EMAIL,
            password=Q_PASSWORD,
            lang="en"
        )

        result = await self.client.connect()
        if isinstance(result, tuple):
            success = bool(result[0])
            message = result[1] if len(result) > 1 else ""
        else:
            success, message = bool(result), ""

        if not success:
            raise RuntimeError(f"Quotex connection failed: {message}")

        return True

    async def assets(self):
        raw = await self.client.get_all_assets()
        if isinstance(raw, dict):
            return list(raw.keys())
        return list(raw or [])

    async def payout(self, asset):
        try:
            payments = self.client.get_payment()
            if asyncio.iscoroutine(payments):
                payments = await payments

            row = payments.get(asset, {})
            profit = row.get("profit", {})
            value = profit.get("1M")

            if value is None:
                value = row.get("turbo_payment", row.get("payment"))

            if value is None:
                return None

            value = float(value)
            return value * 100 if 0 < value <= 1 else value
        except Exception:
            log.debug("Payout unavailable: %s", asset, exc_info=True)
            return None

    async def candles(self, asset, count=180, period=60):
        end = time.time()
        raw = await self.client.get_candles(
            asset, end, count, period
        )

        result = []
        for row in raw or []:
            try:
                ts = int(row.get("time", row.get("timestamp")))
                # Some forks may return milliseconds.
                if ts > 10_000_000_000:
                    ts //= 1000

                item = {
                    "time": ts,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"])
                }
                result.append(item)
            except (ValueError, TypeError, KeyError, AttributeError):
                continue

        # Remove duplicate timestamps and sort oldest to newest.
        return sorted(
            {c["time"]: c for c in result}.values(),
            key=lambda x: x["time"]
        )

    async def close(self):
        if self.client:
            method = getattr(self.client, "close", None)
            if method:
                result = method()
                if asyncio.iscoroutine(result):
                    await result


# -------------------- CATEGORY FILTERS --------------------

CRYPTO_WORDS = (
    "BTC", "ETH", "LTC", "XRP", "DOGE", "SOL", "ADA",
    "BNB", "DOT", "TRX", "AVAX", "LINK", "TON"
)
COMMODITY_WORDS = (
    "GOLD", "SILVER", "XAU", "XAG", "OIL", "BRENT",
    "WTI", "NATGAS", "NGAS", "COPPER"
)
FIAT_CODES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD",
    "CAD", "SGD", "HKD", "NOK", "SEK", "TRY", "ZAR",
    "MXN", "PLN", "THB", "IDR", "INR", "BRL", "CNY"
}


def category_of(asset):
    """
    CURRENCY means OTC fiat pairs in this bot.
    FOREX means non-OTC fiat pairs.
    This is a practical local classification, not an official Quotex taxonomy.
    """
    name = asset.upper().replace("/", "").replace("-", "").replace("_", "")

    if any(word in name for word in CRYPTO_WORDS):
        return "CRYPTO"

    if any(word in name for word in COMMODITY_WORDS):
        return "COMMODITIES"

    otc = "OTC" in name
    letters = "".join(ch for ch in name if ch.isalpha())
    letters = letters.replace("OTC", "")

    if len(letters) >= 6:
        first, second = letters[:3], letters[3:6]
        if first in FIAT_CODES and second in FIAT_CODES:
            return "CURRENCY" if otc else "FOREX"

    return "OTHER"


async def get_assets_for_category(category):
    assets = await QUOTEX.assets()
    if category == "ALL":
        return assets
    return [a for a in assets if category_of(a) == category]


# -------------------- TELEGRAM MENUS --------------------

def category_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 FOREX", callback_data="cat:FOREX")],
        [InlineKeyboardButton("₿ CRYPTO", callback_data="cat:CRYPTO")],
        [InlineKeyboardButton("💱 CURRENCY (OTC FX)", callback_data="cat:CURRENCY")],
        [InlineKeyboardButton("🪙 COMMODITIES", callback_data="cat:COMMODITIES")],
        [InlineKeyboardButton("🌐 ALL CATEGORIES", callback_data="cat:ALL")]
    ])


def pair_keyboard(user_id):
    state = STATE[user_id]
    assets = state.get("assets", [])
    selected = state.setdefault("selected_pairs", set())
    page = state.get("page", 0)
    page_size = 20
    start = page * page_size
    chunk = assets[start:start + page_size]

    rows = []
    for i in range(0, len(chunk), 2):
        row = []
        for asset in chunk[i:i + 2]:
            mark = "✅" if asset in selected else "▫️"
            # Callback data length must remain short.
            index = assets.index(asset)
            row.append(InlineKeyboardButton(
                f"{mark} {asset}"[:60],
                callback_data=f"pair:{index}"
            ))
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data="page:-1"))
    if start + page_size < len(assets):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data="page:1"))
    if nav:
        rows.append(nav)

    rows.extend([
        [InlineKeyboardButton("✅ Select ALL pairs", callback_data="pairs:all")],
        [InlineKeyboardButton("🧹 Clear selection", callback_data="pairs:clear")],
        [InlineKeyboardButton("➡️ Continue", callback_data="pairs:continue")],
        [InlineKeyboardButton("🔙 Categories", callback_data="back:categories")]
    ])
    return InlineKeyboardMarkup(rows)


def strategy_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 AI Strategy", callback_data="strategy:ai")],
        [InlineKeyboardButton("⚡ All Strategy", callback_data="strategy:all")],
        [InlineKeyboardButton("▶️ Start scanning", callback_data="scanner:start")],
        [InlineKeyboardButton("🔙 Categories", callback_data="back:categories")]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    STATE[uid] = {
        "category": None,
        "assets": [],
        "selected_pairs": set(),
        "strategy": "all",
        "page": 0
    }
    await update.effective_message.reply_text(
        "🟦 STM PRO BOT\n\n"
        "Step 1/3 — Select pair category:",
        reply_markup=category_keyboard()
    )


async def show_pairs(query, uid, category):
    try:
        assets = await get_assets_for_category(category)
    except Exception as exc:
        await query.edit_message_text(
            f"Could not load Quotex assets: {exc}\n"
            "Check your connector and credentials."
        )
        return

    state = STATE.setdefault(uid, {})
    state["category"] = category
    state["assets"] = assets[:MAX_ASSETS]
    state["selected_pairs"] = set()
    state["page"] = 0

    if not state["assets"]:
        await query.edit_message_text(
            f"No assets detected in {category}.\n"
            "The connector's asset names may need category mapping.",
            reply_markup=category_keyboard()
        )
        return

    await query.edit_message_text(
        f"Step 2/3 — {category}\n"
        f"Detected pairs: {len(state['assets'])}\n"
        "Select individual pairs or select all:",
        reply_markup=pair_keyboard(uid)
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data or ""
    state = STATE.setdefault(uid, {
        "category": None, "assets": [],
        "selected_pairs": set(), "strategy": "all", "page": 0
    })

    if data.startswith("cat:"):
        await show_pairs(query, uid, data.split(":", 1)[1])
        return

    if data == "back:categories":
        await query.edit_message_text(
            "Step 1/3 — Select pair category:",
            reply_markup=category_keyboard()
        )
        return

    if data.startswith("page:"):
        state["page"] = max(
            0, state.get("page", 0) + int(data.split(":")[1])
        )
        await query.edit_message_reply_markup(
            reply_markup=pair_keyboard(uid)
        )
        return

    if data.startswith("pair:"):
        idx = int(data.split(":")[1])
        assets = state.get("assets", [])
        if 0 <= idx < len(assets):
            asset = assets[idx]
            selected = state.setdefault("selected_pairs", set())
            if asset in selected:
                selected.remove(asset)
            else:
                selected.add(asset)
        await query.edit_message_reply_markup(
            reply_markup=pair_keyboard(uid)
        )
        return

    if data == "pairs:all":
        state["selected_pairs"] = set(state.get("assets", []))
        await query.edit_message_reply_markup(
            reply_markup=pair_keyboard(uid)
        )
        return

    if data == "pairs:clear":
        state["selected_pairs"] = set()
        await query.edit_message_reply_markup(
            reply_markup=pair_keyboard(uid)
        )
        return

    if data == "pairs:continue":
        if not state.get("selected_pairs"):
            await query.message.reply_text(
                "Select at least one pair or tap Select ALL pairs."
            )
            return
        await query.edit_message_text(
            "Step 3/3 — Select strategy:",
            reply_markup=strategy_keyboard()
        )
        return

    if data.startswith("strategy:"):
        state["strategy"] = data.split(":", 1)[1]
        await query.edit_message_text(
            f"Selected strategy: {state['strategy'].upper()}\n"
            f"Category: {state.get('category')}\n"
            f"Pairs selected: {len(state.get('selected_pairs', []))}\n"
            "Press Start scanning when ready.",
            reply_markup=strategy_keyboard()
        )
        return

    if data == "scanner:start":
        await start_scanner(context.application, uid)
        await query.message.reply_text(
            "Scanner started. Use /stopscan to stop it."
        )
        return


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "/start — select category, pairs and strategy\n"
        "/startscan — start scanner\n"
        "/stopscan — stop scanner\n"
        "/status — scanner status\n"
        "/stats — results history"
    )


# -------------------- SIGNAL ENGINE --------------------

def normalize_signal(raw):
    if not raw:
        return None
    if raw.get("direction") not in ("CALL", "PUT"):
        return None
    if float(raw.get("score", 0)) < MIN_SCORE:
        return None
    return raw


async def analyze_asset(asset, strategy):
    async with SEMAPHORE:
        try:
            candles = await QUOTEX.candles(asset, 180, 60)
            if len(candles) < 60:
                return None

            # Reject stale market data.
            if int(time.time()) - candles[-1]["time"] > 150:
                return None

            payout = await QUOTEX.payout(asset)
            if payout is None or payout < MIN_PAYOUT:
                return None

            if strategy == "ai":
                signal = analyze_ai(candles)
            else:
                signal = analyze_all(candles)

            signal = normalize_signal(signal)
            if not signal:
                return None

            return {
                "asset": asset,
                "direction": signal["direction"],
                "score": float(signal["score"]),
                "reason": signal.get("reason", "Rule-based setup"),
                "payout": payout
            }
        except Exception:
            log.debug("Analysis failed for %s", asset, exc_info=True)
            return None


def time_text(timestamp):
    return datetime.fromtimestamp(timestamp, TZ).strftime("%H:%M:%S")


async def publish(context, message):
    if not CHANNEL_ID:
        log.warning("TG_CHANNEL_ID is empty; not publishing to channel.")
        return
    await context.bot.send_message(chat_id=CHANNEL_ID, text=message)


async def candle_result(asset, direction, entry_time):
    """
    Candle-proxy result only. This is not the user's actual entry price
    and is not confirmed Quotex settlement.
    """
    candles = await QUOTEX.candles(asset, 180, 60)
    lookup = {c["time"]: c for c in candles}
    candle = lookup.get(entry_time)
    if not candle:
        return None

    o, c = candle["open"], candle["close"]
    if abs(o - c) < 1e-12:
        return "DRAW"

    if direction == "CALL":
        return "WIN" if c > o else "LOSS"
    return "WIN" if c < o else "LOSS"


async def resolve_signal(context, signal_id, asset, direction, entry_time):
    try:
        wait = max(0, entry_time + 62 - int(time.time()))
        await asyncio.sleep(wait)

        initial = await candle_result(asset, direction, entry_time)
        if initial is None:
            update_result(signal_id, "PENDING", "Initial candle unavailable")
            final = "PENDING"
            detail = "Initial candle unavailable; not verified"
        elif initial == "LOSS":
            # MTG1 is modeled as the next candle in the same direction.
            await asyncio.sleep(max(0, entry_time + 122 - int(time.time())))
            mtg = await candle_result(asset, direction, entry_time + 60)
            if mtg is None:
                final = "PENDING"
                detail = "Initial LOSS; MTG1 candle unavailable"
            else:
                final = "WIN" if mtg == "WIN" else (
                    "DRAW" if mtg == "DRAW" else "LOSS"
                )
                detail = f"Initial {initial}; MTG1 proxy {mtg}"
        else:
            final = initial
            detail = "Initial 1-minute candle proxy"

        update_result(signal_id, final, detail)
        await publish(
            context,
            "📊 STM PRO BOT RESULT\n"
            f"Pair: {asset}\nDirection: {direction}\n"
            f"Result: {final}\nDetails: {detail}\n"
            "⚠️ Candle proxy only; not confirmed broker settlement."
        )
    except Exception:
        log.exception("Result check failed for %s", asset)
        update_result(signal_id, "PENDING", "Result check error")


async def scanner_loop(context, user_id):
    global SCANNER_RUNNING

    try:
        while SCANNER_RUNNING:
            state = STATE.get(user_id, {})
            pairs = list(state.get("selected_pairs", []))
            strategy = state.get("strategy", "all")

            if not pairs:
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            results = await asyncio.gather(
                *(analyze_asset(asset, strategy) for asset in pairs),
                return_exceptions=True
            )

            # Queue each qualifying signal for the next available minute.
            now = int(time.time())
            publish_at = (now // 60 + 1) * 60 - 5
            entry_time = publish_at + 5

            # If the signal time has already passed, target the next minute.
            if publish_at <= now:
                publish_at += 60
                entry_time += 60

            delay = publish_at - int(time.time())
            if delay > 0:
                await asyncio.sleep(delay)

            if not SCANNER_RUNNING:
                break

            for result in results:
                if not isinstance(result, dict):
                    continue

                item = result
                signal_id = save_signal(item, entry_time, strategy)
                if signal_id is None:
                    continue

                direction_text = (
                    "🟢 CALL / UP" if item["direction"] == "CALL"
                    else "🔴 PUT / DOWN"
                )

                message = (
                    "🟦 STM PRO BOT\n"
                    f"Pair: {item['asset']}\n"
                    f"Direction: {direction_text}\n"
                    f"Entry: {time_text(entry_time)} ({TIMEZONE})\n"
                    "Expiry: 1 minute\n"
                    f"Payout: {item['payout']:.0f}%\n"
                    f"Strategy: {strategy.upper()}\n"
                    f"Score: {item['score']:.1f}\n"
                    f"Setup: {item['reason']}\n"
                    "MTG: 1 (manual only)\n"
                    "⚠️ Signal only. Result is estimated from candles."
                )

                try:
                    await publish(context, message)
                    asyncio.create_task(
                        resolve_signal(
                            context, signal_id, item["asset"],
                            item["direction"], entry_time
                        )
                    )
                except Exception:
                    log.exception("Could not publish signal")

            await asyncio.sleep(SCAN_INTERVAL)

    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Scanner crashed")
    finally:
        SCANNER_RUNNING = False


async def start_scanner(application, user_id):
    global SCANNER_TASK, SCANNER_RUNNING

    if SCANNER_RUNNING:
        return

    if not QUOTEX or not QUOTEX.client:
        raise RuntimeError("Quotex data connector is not connected")

    SCANNER_RUNNING = True
    SCANNER_TASK = asyncio.create_task(
        scanner_loop(application, user_id)
    )


async def startscan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = STATE.get(uid)

    if not state or not state.get("selected_pairs"):
        await update.effective_message.reply_text(
            "First use /start and select category and pairs."
        )
        return

    try:
        await start_scanner(context.application, uid)
        await update.effective_message.reply_text("Scanner started.")
    except Exception as exc:
        await update.effective_message.reply_text(f"Cannot start: {exc}")


async def stopscan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SCANNER_RUNNING, SCANNER_TASK
    SCANNER_RUNNING = False

    if SCANNER_TASK and not SCANNER_TASK.done():
        SCANNER_TASK.cancel()
        try:
            await SCANNER_TASK
        except asyncio.CancelledError:
            pass

    SCANNER_TASK = None
    await update.effective_message.reply_text("Scanner stopped.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Scanner: {'RUNNING' if SCANNER_RUNNING else 'STOPPED'}"
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        rows = conn.execute(
            "SELECT result, COUNT(*) n FROM signals GROUP BY result"
        ).fetchall()

    stats = {row["result"]: row["n"] for row in rows}
    await update.effective_message.reply_text(
        "📊 STM PRO BOT HISTORY\n"
        f"Total: {sum(stats.values())}\n"
        f"WIN: {stats.get('WIN', 0)}\n"
        f"LOSS: {stats.get('LOSS', 0)}\n"
        f"DRAW: {stats.get('DRAW', 0)}\n"
        f"PENDING: {stats.get('PENDING', 0)}\n"
        "Results are candle proxies, not broker-confirmed outcomes."
    )


async def post_init(application):
    global QUOTEX
    init_db()
    QUOTEX = QuotexFeed()
    try:
        await QUOTEX.connect()
        log.info("Quotex connector connected")
    except Exception:
        log.exception("Quotex connection failed")
        # Bot can still open menus, but scanner cannot run.


async def post_shutdown(application):
    global SCANNER_RUNNING
    SCANNER_RUNNING = False
    if QUOTEX:
        await QUOTEX.close()


def main():
    if not TOKEN:
        raise SystemExit("Set TG_BOT_TOKEN in your .env file.")

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("startscan", startscan_cmd))
    app.add_handler(CommandHandler("stopscan", stopscan_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(callbacks))

    log.info("STM PRO BOT starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
