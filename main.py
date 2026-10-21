import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =========================================================
# STM PRO BOT — MAIN.PY
# No forced channel joining / no unrelated channel promotion
# =========================================================

load_dotenv(".env")

def env_value(key, default=""):
    return os.getenv(key, default).strip().strip("'\"")

TG_BOT_TOKEN = env_value("TG_BOT_TOKEN")
TG_CHANNEL_ID = env_value("TG_CHANNEL_ID")
QUOTEX_EMAIL = env_value("QUOTEX_EMAIL")
QUOTEX_PASSWORD = env_value("QUOTEX_PASSWORD")
BOT_TIMEZONE = env_value("BOT_TIMEZONE", "Asia/Male")
MIN_PAYOUT = float(env_value("MIN_PAYOUT", "80") or 80)
MIN_SCORE = float(env_value("MIN_SCORE", "1.5") or 1.5)
SCAN_INTERVAL_SECONDS = max(
    2, int(env_value("SCAN_INTERVAL_SECONDS", "5") or 5)
)
DB_PATH = env_value("DB_PATH", "stmpro_history.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("STM_PRO_BOT")

try:
    BOT_TZ = ZoneInfo(BOT_TIMEZONE)
except Exception:
    BOT_TZ = timezone.utc
    log.warning("Timezone unavailable; using UTC.")

# =========================================================
# MARKET PAIRS
# =========================================================

PAIRS = {
    "FOREX": [
        "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
        "USD/CAD", "AUD/USD", "NZD/USD",
    ],
    "COMMODITIES OTC": [
        "UKBrent", "Silver", "USCrude", "Gold",
    ],
    "CRYPTO OTC": [
        "Bitcoin", "Ethereum Classic", "Chainlink",
        "Litecoin", "Toncoin", "Trump", "Ripple", "Zcash",
        "Avalanche", "Dash", "Axie Infinity", "Polkadot",
        "Binance Coin", "Ethereum", "Cosmos", "Bitcoin Cash",
    ],
    "CURRENCY OTC": [
        "USD/ARS", "USD/BDT", "USD/BRL", "USD/CAD",
        "USD/CHF", "USD/COP", "USD/DZD", "USD/EGP",
        "USD/IDR", "USD/INR", "USD/JPY", "USD/MXN",
        "USD/NGN", "USD/PHP", "USD/PKR", "USD/ZAR",
        "AUD/CAD", "AUD/CHF", "AUD/JPY", "AUD/NZD",
        "AUD/USD", "CAD/CHF", "CAD/JPY", "CHF/JPY",
        "EUR/AUD", "EUR/CAD", "EUR/CHF", "EUR/GBP",
        "EUR/JPY", "EUR/NZD", "EUR/USD", "GBP/AUD",
        "GBP/CAD", "GBP/CHF", "GBP/JPY", "GBP/NZD",
        "GBP/USD", "NZD/CAD", "NZD/CHF", "NZD/JPY",
        "NZD/USD",
    ],
}

# =========================================================
# TRANSLATIONS
# =========================================================

TEXT = {
    "en": {
        "welcome": "🏠 STM PRO BOT\n\nChoose an option:",
        "language": "Choose your language:",
        "markets": "📊 Select a market category:",
        "pick": "Select pairs to scan. Tap again to remove:",
        "added": "Pair added: {pair}",
        "removed": "Pair removed: {pair}",
        "start": "✅ Scanner started.",
        "stop": "⏹ Scanner stopped.",
        "running": "Scanner is already running.",
        "empty": "Select at least one pair first.",
        "status": "📟 STATUS\nScanner: {running}\nMTG: {mtg}\nPairs: {count}\nMinimum payout: {payout}%",
        "mtg": "Enable MTG follow-up alerts?",
        "mtg_on": "MTG enabled.",
        "mtg_off": "MTG disabled.",
        "history": "📚 Recent signal history",
        "nohistory": "No history yet.",
        "connected": "Quotex connection successful.",
        "connect_error": "Quotex connection failed. Check credentials and PyQuotex compatibility.",
    },
    "bn": {
        "welcome": "🏠 STM PRO BOT\n\nএকটি অপশন বেছে নাও:",
        "language": "ভাষা নির্বাচন করো:",
        "markets": "📊 মার্কেট ক্যাটাগরি বেছে নাও:",
        "pick": "স্ক্যান করার পেয়ার বেছে নাও। আবার চাপলে বাদ যাবে:",
        "added": "পেয়ার যোগ হয়েছে: {pair}",
        "removed": "পেয়ার বাদ দেওয়া হয়েছে: {pair}",
        "start": "✅ স্ক্যানার চালু হয়েছে।",
        "stop": "⏹ স্ক্যানার বন্ধ হয়েছে।",
        "running": "স্ক্যানার আগেই চালু আছে।",
        "empty": "আগে অন্তত একটি পেয়ার বেছে নাও।",
        "status": "📟 অবস্থা\nস্ক্যানার: {running}\nMTG: {mtg}\nপেয়ার: {count}\nসর্বনিম্ন payout: {payout}%",
        "mtg": "MTG follow-up alert চালু করবে?",
        "mtg_on": "MTG চালু হয়েছে।",
        "mtg_off": "MTG বন্ধ হয়েছে।",
        "history": "📚 সাম্প্রতিক সিগন্যাল ইতিহাস",
        "nohistory": "এখনো কোনো ইতিহাস নেই।",
        "connected": "Quotex সংযোগ সফল হয়েছে।",
        "connect_error": "Quotex সংযোগ ব্যর্থ। লগইন তথ্য ও PyQuotex সংস্করণ পরীক্ষা করো।",
    },
    "hi": {
        "welcome": "🏠 STM PRO BOT\n\nएक विकल्प चुनें:",
        "language": "अपनी भाषा चुनें:",
        "markets": "📊 मार्केट श्रेणी चुनें:",
        "pick": "पेयर चुनें। दोबारा दबाने पर हट जाएगा:",
        "added": "पेयर जोड़ा गया: {pair}",
        "removed": "पेयर हटाया गया: {pair}",
        "start": "✅ स्कैनर शुरू हो गया।",
        "stop": "⏹ स्कैनर बंद हो गया।",
        "running": "स्कैनर पहले से चालू है।",
        "empty": "पहले कम से कम एक पेयर चुनें।",
        "status": "📟 स्थिति\nस्कैनर: {running}\nMTG: {mtg}\nपेयर: {count}\nन्यूनतम payout: {payout}%",
        "mtg": "MTG follow-up alert चालू करें?",
        "mtg_on": "MTG चालू है।",
        "mtg_off": "MTG बंद है।",
        "history": "📚 हाल के सिग्नल",
        "nohistory": "अभी कोई इतिहास नहीं।",
        "connected": "Quotex कनेक्शन सफल।",
        "connect_error": "Quotex कनेक्शन विफल। लॉगिन और PyQuotex जाँचें।",
    },
    "ur": {
        "welcome": "🏠 STM PRO BOT\n\nایک آپشن منتخب کریں:",
        "language": "اپنی زبان منتخب کریں:",
        "markets": "📊 مارکیٹ کیٹیگری منتخب کریں:",
        "pick": "پیئر منتخب کریں۔ دوبارہ دبانے سے ہٹ جائے گا:",
        "added": "پیئر شامل ہوگیا: {pair}",
        "removed": "پیئر ہٹا دیا گیا: {pair}",
        "start": "✅ اسکینر شروع ہوگیا۔",
        "stop": "⏹ اسکینر بند ہوگیا۔",
        "running": "اسکینر پہلے سے چل رہا ہے۔",
        "empty": "پہلے کم از کم ایک پیئر منتخب کریں۔",
        "status": "📟 حالت\nاسکینر: {running}\nMTG: {mtg}\nپیئر: {count}\nکم از کم payout: {payout}%",
        "mtg": "MTG follow-up alert آن کریں؟",
        "mtg_on": "MTG آن ہے۔",
        "mtg_off": "MTG آف ہے۔",
        "history": "📚 حالیہ سگنلز",
        "nohistory": "ابھی کوئی ہسٹری نہیں۔",
        "connected": "Quotex کنکشن کامیاب۔",
        "connect_error": "Quotex کنکشن ناکام۔ لاگ اِن اور PyQuotex چیک کریں۔",
    },
}

USER_LANG = {}
SELECTED_PAIRS = set()
PAIR_CATEGORY = {}
SCANNER_RUNNING = False
SCANNER_TASK = None
MTG_ENABLED = False
LAST_SIGNAL = {}
LOSS_STREAK = {}
PAUSE_NEXT = set()
QUOTEX = None
SCANNER_CHAT_ID = None


def tr(user_id, key, **kwargs):
    lang = USER_LANG.get(user_id, "en")
    value = TEXT.get(lang, TEXT["en"]).get(key, TEXT["en"].get(key, key))
    return value.format(**kwargs)


# =========================================================
# SQLITE HISTORY
# =========================================================

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                payout REAL,
                score REAL,
                created_at TEXT,
                result TEXT DEFAULT 'PENDING'
            )
        """)


def save_signal(pair, direction, payout, score):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO signals
            (pair,direction,payout,score,created_at)
            VALUES(?,?,?,?,?)""",
            (
                pair, direction, payout, score,
                datetime.now(BOT_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        return cur.lastrowid


def update_result(signal_id, result):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET result=? WHERE id=?",
            (result, signal_id),
        )


def recent_history(limit=10):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """SELECT pair,direction,payout,score,created_at,result
            FROM signals ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()


# =========================================================
# TELEGRAM KEYBOARDS
# =========================================================

def lang_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("English 🇬🇧", callback_data="lang:en"),
            InlineKeyboardButton("বাংলা 🇧🇩", callback_data="lang:bn"),
        ],
        [
            InlineKeyboardButton("हिन्दी 🇮🇳", callback_data="lang:hi"),
            InlineKeyboardButton("اردو 🇵🇰", callback_data="lang:ur"),
        ],
    ])


def home_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Markets", callback_data="markets"),
            InlineKeyboardButton("▶️ Start", callback_data="scan:start"),
        ],
        [
            InlineKeyboardButton("⏹ Stop", callback_data="scan:stop"),
            InlineKeyboardButton("⚙️ MTG", callback_data="mtg"),
        ],
        [
            InlineKeyboardButton("📟 Status", callback_data="status"),
            InlineKeyboardButton("📚 History", callback_data="history"),
        ],
        [InlineKeyboardButton("🌐 Language", callback_data="language")],
    ])


def category_keyboard():
    rows = []
    categories = list(PAIRS)
    for i in range(0, len(categories), 2):
        rows.append([
            InlineKeyboardButton(
                cat, callback_data="cat:" + cat
            )
            for cat in categories[i:i + 2]
        ])
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def pair_keyboard(category):
    rows = []
    pairs = PAIRS.get(category, [])
    for i in range(0, len(pairs), 2):
        row = []
        for pair in pairs[i:i + 2]:
            mark = "✅ " if pair in SELECTED_PAIRS else ""
            row.append(
                InlineKeyboardButton(
                    mark + pair,
                    callback_data=f"pair:{category}:{pair}",
                )
            )
        rows.append(row)
    rows.append([
        InlineKeyboardButton("⬅️ Categories", callback_data="markets"),
        InlineKeyboardButton("🏠 Home", callback_data="home"),
    ])
    return InlineKeyboardMarkup(rows)


async def send_text(context, chat_id, message, **kwargs):
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=message, **kwargs
        )
    except Exception as exc:
        log.warning("Telegram message failed: %s", exc)


# =========================================================
# COMMANDS
# =========================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        TEXT["en"]["language"], reply_markup=lang_keyboard()
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "STM PRO BOT\n"
        "/start - language/menu\n"
        "/markets - choose pairs\n"
        "/startscan - start scanner\n"
        "/stopscan - stop scanner\n"
        "/status - scanner status\n"
        "/mtg - MTG setting\n"
        "/history - recent signals"
    )


async def markets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.effective_message.reply_text(
        tr(uid, "markets"), reply_markup=category_keyboard()
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.effective_message.reply_text(
        tr(
            uid, "status",
            running="ON" if SCANNER_RUNNING else "OFF",
            mtg="ON" if MTG_ENABLED else "OFF",
            count=len(SELECTED_PAIRS),
            payout=MIN_PAYOUT,
        ),
        reply_markup=home_keyboard(),
    )


async def mtg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Enable MTG follow-up alerts?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes ✅", callback_data="mtg:on"),
            InlineKeyboardButton("No ❌", callback_data="mtg:off"),
        ]]),
    )


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = recent_history()
    if not rows:
        await update.effective_message.reply_text(tr(uid, "nohistory"))
        return

    lines = [tr(uid, "history")]
    for pair, direction, payout, score, created, result in rows:
        lines.append(
            f"{pair} | {direction} | {payout:.0f}% | "
            f"{result} | {created}"
        )
    await update.effective_message.reply_text("\n".join(lines))


async def startscan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_scanner(context, update.effective_chat.id, update.effective_user.id)


async def stopscan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stop_scanner()
    await update.effective_message.reply_text(
        tr(update.effective_user.id, "stop")
    )


# =========================================================
# CALLBACKS
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MTG_ENABLED
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data or ""

    if data.startswith("lang:"):
        USER_LANG[uid] = data.split(":", 1)[1]
        await q.edit_message_text(
            tr(uid, "welcome"), reply_markup=home_keyboard()
        )
        return

    if data == "language":
        await q.edit_message_text(
            tr(uid, "language"), reply_markup=lang_keyboard()
        )
        return

    if data == "home":
        await q.edit_message_text(
            tr(uid, "welcome"), reply_markup=home_keyboard()
        )
        return

    if data == "markets":
        await q.edit_message_text(
            tr(uid, "markets"), reply_markup=category_keyboard()
        )
        return

    if data.startswith("cat:"):
        category = data.split(":", 1)[1]
        await q.edit_message_text(
            category + "\n" + tr(uid, "pick"),
            reply_markup=pair_keyboard(category),
        )
        return

    if data.startswith("pair:"):
        _, category, pair = data.split(":", 2)
        if pair in SELECTED_PAIRS:
            SELECTED_PAIRS.remove(pair)
            await q.answer(tr(uid, "removed", pair=pair))
        else:
            SELECTED_PAIRS.add(pair)
            PAIR_CATEGORY[pair] = category
            await q.answer(tr(uid, "added", pair=pair))
        await q.edit_message_reply_markup(
            reply_markup=pair_keyboard(category)
        )
        return

    if data == "scan:start":
        await start_scanner(context, q.message.chat_id, uid)
        return

    if data == "scan:stop":
        await stop_scanner()
        await q.edit_message_text(
            tr(uid, "stop"), reply_markup=home_keyboard()
        )
        return

    if data == "status":
        await q.edit_message_text(
            tr(
                uid, "status",
                running="ON" if SCANNER_RUNNING else "OFF",
                mtg="ON" if MTG_ENABLED else "OFF",
                count=len(SELECTED_PAIRS),
                payout=MIN_PAYOUT,
            ),
            reply_markup=home_keyboard(),
        )
        return

    if data == "history":
        rows = recent_history()
        lines = [tr(uid, "history")]
        if not rows:
            lines.append(tr(uid, "nohistory"))
        else:
            for pair, direction, payout, score, created, result in rows:
                lines.append(
                    f"{pair} | {direction} | {payout:.0f}% | {result}"
                )
        await q.edit_message_text(
            "\n".join(lines), reply_markup=home_keyboard()
        )
        return

    if data == "mtg":
        await q.edit_message_text(
            tr(uid, "mtg"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Yes ✅", callback_data="mtg:on"),
                InlineKeyboardButton("No ❌", callback_data="mtg:off"),
            ]]),
        )
        return

    if data == "mtg:on":
        MTG_ENABLED = True
        await q.edit_message_text(
            tr(uid, "mtg_on"), reply_markup=home_keyboard()
        )
        return

    if data == "mtg:off":
        MTG_ENABLED = False
        await q.edit_message_text(
            tr(uid, "mtg_off"), reply_markup=home_keyboard()
        )
        return


# =========================================================
# QUOTEX API
# =========================================================

async def maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


async def api_call(method_name, *args):
    method = getattr(QUOTEX, method_name, None)
    if method is None:
        raise AttributeError(f"PyQuotex has no method {method_name}")
    return await maybe_await(method(*args))


async def connect_quotex():
    global QUOTEX

    if not QUOTEX_EMAIL or not QUOTEX_PASSWORD:
        log.error("Set QUOTEX_EMAIL and QUOTEX_PASSWORD in .env")
        return False

    try:
        from pyquotex.stable_api import Quotex

        QUOTEX = Quotex(
            email=QUOTEX_EMAIL,
            password=QUOTEX_PASSWORD,
            lang="en",
        )

        result = await maybe_await(QUOTEX.connect())

        if isinstance(result, (tuple, list)):
            ok = bool(result[0]) if result else False
            message = result[1] if len(result) > 1 else ""
        else:
            ok = bool(result)
            message = ""

        if not ok:
            log.error("Quotex login failed: %s", message)
            return False

        log.info("Quotex connected")
        return True

    except Exception:
        log.exception("Quotex connection error")
        return False


def normalize_asset(name):
    return str(name).strip().replace("/", "").replace("_", "").replace(" ", "").upper()


async def live_assets():
    try:
        raw = await api_call("get_all_assets")
        names = set()

        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(key, str):
                    names.add(normalize_asset(key))
                if isinstance(value, str):
                    names.add(normalize_asset(value))
                if isinstance(value, dict):
                    for field in ("name", "symbol", "asset"):
                        if value.get(field):
                            names.add(normalize_asset(value[field]))

        elif isinstance(raw, (list, tuple, set)):
            for value in raw:
                if isinstance(value, str):
                    names.add(normalize_asset(value))
                elif isinstance(value, dict):
                    for field in ("name", "symbol", "asset"):
                        if value.get(field):
                            names.add(normalize_asset(value[field]))

        log.info("Loaded %d asset names", len(names))
        return names

    except Exception as exc:
        log.warning("Asset list unavailable: %s", exc)
        return set()


async def resolve_asset(pair, assets):
    if not assets:
        return pair

    key = normalize_asset(pair)
    if key in assets:
        return pair

    # Do not invent broker symbol names; try common formats.
    candidates = [pair, pair.replace("/", ""), pair.replace("/", "_")]
    for candidate in candidates:
        if normalize_asset(candidate) in assets:
            return candidate

    return None


async def payout_for(asset):
    for name in ("get_payout_by_asset", "get_payout"):
        try:
            value = await api_call(name, asset)
            if isinstance(value, dict):
                for key in ("payout", "percent", "profit", "value"):
                    if key in value:
                        value = value[key]
                        break
            value = float(value)
            if 0 <= value <= 1:
                value *= 100
            if 0 <= value <= 100:
                return value
        except Exception:
            continue
    return None


async def candles_for(asset, count=40):
    raw = await api_call(
        "get_candles", asset, int(time.time()), count * 60, 60
    )

    if isinstance(raw, dict):
        raw = raw.get("candles", raw.get("data", []))

    if not isinstance(raw, (list, tuple)):
        return []

    result = []
    current_minute = int(time.time() // 60 * 60)

    for candle in raw:
        if not isinstance(candle, dict):
            continue
        try:
            ts = int(candle.get("time", candle.get("from", candle.get("timestamp", 0))))
            op = float(candle.get("open", candle.get("Open")))
            hi = float(candle.get("high", candle.get("High")))
            lo = float(candle.get("low", candle.get("Low")))
            cl = float(candle.get("close", candle.get("Close")))

            if ts and ts < current_minute and hi >= lo:
                result.append({
                    "time": ts, "open": op, "high": hi,
                    "low": lo, "close": cl,
                })
        except (TypeError, ValueError):
            continue

    result.sort(key=lambda c: c["time"])
    return result


# =========================================================
# STRATEGY AND SAFETY FILTERS
# =========================================================

def direction(candle):
    if candle["close"] > candle["open"]:
        return "CALL"
    if candle["close"] < candle["open"]:
        return "PUT"
    return "DOJI"


def ema(values, period):
    alpha = 2 / (period + 1)
    value = values[0]
    for item in values[1:]:
        value = alpha * item + (1 - alpha) * value
    return value


def make_signal(candles):
    if len(candles) < 25:
        return None, 0, "Not enough candles"

    recent = candles[-4:]
    dirs = [direction(c) for c in recent]

    # Avoid doji-heavy periods.
    if dirs.count("DOJI") >= 3:
        return None, 0, "Doji filter"

    # Avoid consecutive same-direction streaks.
    if len(dirs) >= 3 and (
        all(x == "CALL" for x in dirs[-3:])
        or all(x == "PUT" for x in dirs[-3:])
    ):
        return None, 0, "Consecutive direction filter"

    # Avoid unusually large movement.
    ranges = [max(c["high"] - c["low"], 1e-12) for c in candles[-21:]]
    baseline = sum(ranges[:-4]) / max(1, len(ranges[:-4]))
    if baseline and max(ranges[-4:]) > baseline * 3.2:
        return None, 0, "Fast movement filter"

    # Avoid wick-heavy candles.
    wick_count = 0
    for c in recent:
        span = max(c["high"] - c["low"], 1e-12)
        body = abs(c["close"] - c["open"])
        if (span - body) / span >= 0.60:
            wick_count += 1
    if wick_count >= 3:
        return None, 0, "Long wick filter"

    closes = [c["close"] for c in candles]
    fast = ema(closes[-25:], 9)
    slow = ema(closes[-25:], 21)
    momentum = closes[-1] - closes[-4]
    last_dir = direction(candles[-1])

    call_score = 0.0
    put_score = 0.0

    if fast > slow:
        call_score += 1.0
    elif fast < slow:
        put_score += 1.0

    if momentum > 0:
        call_score += 0.75
    elif momentum < 0:
        put_score += 0.75

    if last_dir == "CALL":
        call_score += 0.5
    elif last_dir == "PUT":
        put_score += 0.5

    if call_score >= MIN_SCORE and call_score > put_score:
        return "CALL", call_score, "EMA + momentum"

    if put_score >= MIN_SCORE and put_score > call_score:
        return "PUT", put_score, "EMA + momentum"

    return None, max(call_score, put_score), "No clear setup"


# =========================================================
# SCANNER
# =========================================================

async def start_scanner(context, chat_id, user_id):
    global SCANNER_RUNNING, SCANNER_TASK, SCANNER_CHAT_ID

    if SCANNER_RUNNING:
        await send_text(context, chat_id, tr(user_id, "running"))
        return

    if not SELECTED_PAIRS:
        await send_text(
            context, chat_id, tr(user_id, "empty"),
            reply_markup=category_keyboard(),
        )
        return

    if QUOTEX is None:
        ok = await connect_quotex()
        if not ok:
            await send_text(context, chat_id, tr(user_id, "connect_error"))
            return

    SCANNER_CHAT_ID = chat_id
    SCANNER_RUNNING = True
    SCANNER_TASK = asyncio.create_task(scanner_loop(context))
    await send_text(context, chat_id, tr(user_id, "start"))


async def stop_scanner():
    global SCANNER_RUNNING, SCANNER_TASK
    SCANNER_RUNNING = False

    if SCANNER_TASK and not SCANNER_TASK.done():
        SCANNER_TASK.cancel()

    SCANNER_TASK = None


async def scanner_loop(context):
    global SCANNER_RUNNING

    assets = await live_assets()

    try:
        while SCANNER_RUNNING:
            for pair in list(SELECTED_PAIRS):
                if not SCANNER_RUNNING:
                    break

                try:
                    asset = await resolve_asset(pair, assets)
                    if not asset:
                        log.info("Skip %s: not found in broker asset list", pair)
                        continue

                    payout = await payout_for(asset)
                    if payout is None or payout < MIN_PAYOUT:
                        log.info("Skip %s: payout %s", pair, payout)
                        continue

                    candles = await candles_for(asset, 40)
                    signal, score, reason = make_signal(candles)

                    if not signal or score < MIN_SCORE:
                        continue

                    candle_ts = candles[-1]["time"]
                    if LAST_SIGNAL.get(pair) == candle_ts:
                        continue

                    if pair in PAUSE_NEXT:
                        PAUSE_NEXT.discard(pair)
                        LAST_SIGNAL[pair] = candle_ts
                        log.info("Paused one signal for %s after losses", pair)
                        continue

                    LAST_SIGNAL[pair] = candle_ts
                    signal_id = save_signal(pair, signal, payout, score)

                    local_time = datetime.now(BOT_TZ).strftime("%H:%M:%S")
                    icon = "🟢" if signal == "CALL" else "🔴"

                    message = (
                        "🚀 STM PRO BOT\n"
                        "━━━━━━━━━━━━━━\n"
                        f"📊 Pair: {pair}\n"
                        f"{icon} Signal: {signal}\n"
                        "⏱ Expiry: 1 minute\n"
                        f"💰 Payout: {payout:.0f}%\n"
                        f"⭐ Score: {score:.2f}\n"
                        f"🕒 Time: {local_time}\n"
                        f"🧠 Strategy: {reason}\n"
