
import asyncio
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from pyquotex.stable_api import Quotex

from ai_strategy import analyze_ai
from all_strategy import analyze_all

# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TG_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "").strip()
QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL", "").strip()
QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD", "").strip()

MIN_PAYOUT = float(os.getenv("MIN_PAYOUT", "80"))
MIN_SCORE = float(os.getenv("MIN_SCORE", "1.5"))
SCAN_INTERVAL = max(3, float(os.getenv("SCAN_INTERVAL_SECONDS", "5")))
DB_FILE = "stm_history.sqlite3"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("STM_PRO_BOT")

# Timezone fallback: no ZoneInfo/tzdata dependency required.
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Male"))
except Exception:
    TZ = timezone.utc


# ============================================================
# FOUR CATEGORIES / ALL REQUESTED PAIRS
# ============================================================

PAIRS = {
    "FOREX": [
        "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
        "USD/CAD", "AUD/USD", "NZD/USD",
    ],

    "COMMODITIES": [
        "UKBrent (OTC)", "Silver (OTC)",
        "USCrude (OTC)", "Gold (OTC)",
    ],

    "CRYPTO": [
        "Bitcoin (OTC)", "Ethereum Classic (OTC)",
        "Chainlink (OTC)", "Litecoin (OTC)",
        "Toncoin (OTC)", "Trump (OTC)", "Ripple (OTC)",
        "Zcash (OTC)", "Avalanche (OTC)", "Dash (OTC)",
        "Axie Infinity (OTC)", "Polkadot (OTC)",
        "Binance Coin (OTC)", "Ethereum (OTC)",
        "Cosmos (OTC)", "Bitcoin Cash (OTC)",
    ],

    "CURRENCY": [
        "USD/ARS (OTC)", "USD/BDT (OTC)", "USD/BRL (OTC)",
        "USD/CAD (OTC)", "USD/CHF (OTC)", "USD/COP (OTC)",
        "USD/DZD (OTC)", "USD/EGP (OTC)", "USD/IDR (OTC)",
        "USD/INR (OTC)", "USD/JPY (OTC)", "USD/MXN (OTC)",
        "USD/NGN (OTC)", "USD/PHP (OTC)", "USD/PKR (OTC)",
        "USD/ZAR (OTC)",

        "AUD/CAD (OTC)", "AUD/CHF (OTC)", "AUD/JPY (OTC)",
        "AUD/NZD (OTC)", "AUD/USD (OTC)", "CAD/CHF (OTC)",
        "CAD/JPY (OTC)", "CHF/JPY (OTC)", "EUR/AUD (OTC)",
        "EUR/CAD (OTC)", "EUR/CHF (OTC)", "EUR/GBP (OTC)",
        "EUR/JPY (OTC)", "EUR/NZD (OTC)", "EUR/USD (OTC)",
        "GBP/AUD (OTC)", "GBP/CAD (OTC)", "GBP/CHF (OTC)",
        "GBP/JPY (OTC)", "GBP/NZD (OTC)", "GBP/USD (OTC)",
        "NZD/CAD (OTC)", "NZD/CHF (OTC)", "NZD/JPY (OTC)",
        "NZD/USD (OTC)",
    ],
}

CATEGORY_ORDER = ["FOREX", "COMMODITIES", "CRYPTO", "CURRENCY"]

# Some Quotex versions use ticker symbols instead of full crypto names.
ALIASES = {
    "bitcoin": ["BTC"],
    "ethereumclassic": ["ETC"],
    "chainlink": ["LINK"],
    "litecoin": ["LTC"],
    "toncoin": ["TON"],
    "trump": ["TRUMP"],
    "ripple": ["XRP"],
    "zcash": ["ZEC"],
    "avalanche": ["AVAX"],
    "dash": ["DASH"],
    "axieinfinity": ["AXS"],
    "polkadot": ["DOT"],
    "binancecoin": ["BNB"],
    "ethereum": ["ETH"],
    "cosmos": ["ATOM"],
    "bitcoincash": ["BCH"],
}


# ============================================================
# LANGUAGES
# ============================================================

LANGUAGES = {
    "en": {
        "name": "English",
        "choose": "🌐 Select your language first:",
        "home": "🏠 Main Menu",
        "category": "📂 Select a market category:",
        "FOREX": "💱 Forex",
        "COMMODITIES": "🪙 Commodities",
        "CRYPTO": "₿ Crypto",
        "CURRENCY": "💹 Currency Pairs (OTC)",
        "pairs": "Select pairs. Tap a pair to select or deselect it.",
        "all": "✅ Select All in Category",
        "next": "Next ➡️",
        "prev": "⬅️ Previous",
        "strategy": "🧠 Choose a strategy:",
        "ai": "🤖 AI Strategy",
        "all_strategy": "📊 All Strategy",
        "start": "▶️ Start Scanning",
        "stop": "⏹ Stop Scanning",
        "selected": "Selected pairs",
        "none": "Please select at least one pair.",
        "started": "✅ Scanning started.",
        "stopped": "⏹ Scanning stopped.",
        "connecting": "Connecting to Quotex...",
        "failed": "❌ Quotex connection failed. Check credentials and logs.",
        "unavailable": "Unavailable on your current Quotex asset list",
        "signal": "SIGNAL",
        "entry": "Entry",
        "expiry": "Expiry",
        "reason": "Reason",
        "result": "ESTIMATED RESULT",
        "win": "WIN",
        "loss": "LOSS",
        "draw": "DRAW",
        "unknown": "UNKNOWN",
        "disclaimer": "Candle-based estimate only; not broker-confirmed.",
        "status": "📊 Status",
        "language_changed": "Language updated.",
    },

    "bn": {
        "name": "বাংলা",
        "choose": "🌐 প্রথমে ভাষা নির্বাচন করুন:",
        "home": "🏠 মূল মেনু",
        "category": "📂 মার্কেট ক্যাটাগরি নির্বাচন করুন:",
        "FOREX": "💱 ফরেক্স",
        "COMMODITIES": "🪙 কমোডিটি",
        "CRYPTO": "₿ ক্রিপ্টো",
        "CURRENCY": "💹 কারেন্সি পেয়ার (OTC)",
        "pairs": "পেয়ার নির্বাচন করুন। আবার চাপলে নির্বাচন বাতিল হবে।",
        "all": "✅ এই ক্যাটাগরির সব পেয়ার",
        "next": "পরবর্তী ➡️",
        "prev": "⬅️ আগের পেজ",
        "strategy": "🧠 স্ট্র্যাটেজি নির্বাচন করুন:",
        "ai": "🤖 AI স্ট্র্যাটেজি",
        "all_strategy": "📊 All Strategy",
        "start": "▶️ স্ক্যানিং শুরু",
        "stop": "⏹ স্ক্যানিং বন্ধ",
        "selected": "নির্বাচিত পেয়ার",
        "none": "অন্তত একটি পেয়ার নির্বাচন করুন।",
        "started": "✅ স্ক্যানিং শুরু হয়েছে।",
        "stopped": "⏹ স্ক্যানিং বন্ধ হয়েছে।",
        "connecting": "Quotex-এ সংযোগ হচ্ছে...",
        "failed": "❌ Quotex সংযোগ ব্যর্থ। লগইন তথ্য ও লগ পরীক্ষা করুন।",
        "unavailable": "আপনার Quotex asset list-এ পাওয়া যায়নি",
        "signal": "সিগন্যাল",
        "entry": "এন্ট্রি",
        "expiry": "মেয়াদ",
        "reason": "কারণ",
        "result": "আনুমানিক রেজাল্ট",
        "win": "উইন",
        "loss": "লস",
        "draw": "ড্র",
        "unknown": "অজানা",
        "disclaimer": "ক্যান্ডেলভিত্তিক অনুমান; ব্রোকার-নিশ্চিত ফলাফল নয়।",
        "status": "📊 অবস্থা",
        "language_changed": "ভাষা পরিবর্তন হয়েছে।",
    },

    "hi": {
        "name": "हिन्दी",
        "choose": "🌐 पहले अपनी भाषा चुनें:",
        "home": "🏠 मुख्य मेनू",
        "category": "📂 मार्केट कैटेगरी चुनें:",
        "FOREX": "💱 फॉरेक्स",
        "COMMODITIES": "🪙 कमोडिटी",
        "CRYPTO": "₿ क्रिप्टो",
        "CURRENCY": "💹 करेंसी पेयर (OTC)",
        "pairs": "पेयर चुनें। दोबारा दबाने पर चयन हट जाएगा।",
        "all": "✅ इस कैटेगरी के सभी पेयर",
        "next": "अगला ➡️",
        "prev": "⬅️ पिछला",
        "strategy": "🧠 स्ट्रैटेजी चुनें:",
        "ai": "🤖 AI स्ट्रैटेजी",
        "all_strategy": "📊 सभी स्ट्रैटेजी",
        "start": "▶️ स्कैनिंग शुरू",
        "stop": "⏹ स्कैनिंग बंद",
        "selected": "चुने गए पेयर",
        "none": "कम से कम एक पेयर चुनें।",
        "started": "✅ स्कैनिंग शुरू हो गई।",
        "stopped": "⏹ स्कैनिंग बंद हो गई।",
        "connecting": "Quotex से कनेक्ट हो रहा है...",
        "failed": "❌ Quotex कनेक्शन विफल। लॉगिन और लॉग जांचें।",
        "unavailable": "आपकी Quotex asset list में उपलब्ध नहीं",
        "signal": "सिग्नल",
        "entry": "एंट्री",
        "expiry": "समाप्ति",
        "reason": "कारण",
        "result": "अनुमानित परिणाम",
        "win": "जीत",
        "loss": "हार",
        "draw": "ड्रॉ",
        "unknown": "अज्ञात",
        "disclaimer": "कैंडल आधारित अनुमान; ब्रोकर द्वारा पुष्टि नहीं।",
        "status": "📊 स्थिति",
        "language_changed": "भाषा बदल दी गई।",
    },

    "ur": {
        "name": "اردو",
        "choose": "🌐 پہلے اپنی زبان منتخب کریں:",
        "home": "🏠 مرکزی مینو",
        "category": "📂 مارکیٹ کیٹیگری منتخب کریں:",
        "FOREX": "💱 فاریکس",
        "COMMODITIES": "🪙 کموڈٹیز",
        "CRYPTO": "₿ کرپٹو",
        "CURRENCY": "💹 کرنسی پیئرز (OTC)",
        "pairs": "پیئر منتخب کریں۔ دوبارہ دبانے سے انتخاب ختم ہوگا۔",
        "all": "✅ اس کیٹیگری کے تمام پیئرز",
        "next": "اگلا ➡️",
        "prev": "⬅️ پچھلا",
        "strategy": "🧠 اسٹریٹجی منتخب کریں:",
        "ai": "🤖 AI اسٹریٹجی",
        "all_strategy": "📊 تمام اسٹریٹجیز",
        "start": "▶️ اسکیننگ شروع",
        "stop": "⏹ اسکیننگ بند",
        "selected": "منتخب پیئرز",
        "none": "کم از کم ایک پیئر منتخب کریں۔",
        "started": "✅ اسکیننگ شروع ہوگئی۔",
        "stopped": "⏹ اسکیننگ بند ہوگئی۔",
        "connecting": "Quotex سے رابطہ ہو رہا ہے...",
        "failed": "❌ Quotex کنکشن ناکام۔ لاگ اِن اور لاگز چیک کریں۔",
        "unavailable": "آپ کی Quotex asset list میں دستیاب نہیں",
        "signal": "سگنل",
        "entry": "انٹری",
        "expiry": "میعاد",
        "reason": "وجہ",
        "result": "تخمینی نتیجہ",
        "win": "جیت",
        "loss": "ہار",
        "draw": "برابر",
        "unknown": "نامعلوم",
        "disclaimer": "کینڈل کی بنیاد پر تخمینہ؛ بروکر سے تصدیق شدہ نہیں۔",
        "status": "📊 حالت",
        "language_changed": "زبان تبدیل ہوگئی۔",
    },
}


def T(context):
    lang = context.user_data.get("lang", "en")
    return LANGUAGES.get(lang, LANGUAGES["en"])


def language_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
            InlineKeyboardButton("🇧🇩 বাংলা", callback_data="lang:bn"),
        ],
        [
            InlineKeyboardButton("🇮🇳 हिन्दी", callback_data="lang:hi"),
            InlineKeyboardButton("🇵🇰 اردو", callback_data="lang:ur"),
        ],
    ])


def home_keyboard(context):
    t = T(context)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t[c], callback_data=f"cat:{c}")]
        for c in CATEGORY_ORDER
    ] + [
        [InlineKeyboardButton(t["status"], callback_data="status")],
        [InlineKeyboardButton("🌐 Language", callback_data="language")],
    ])


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT NOT NULL,
                display_pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                strategy TEXT NOT NULL,
                score REAL,
                payout REAL,
                signal_time INTEGER,
                entry_time INTEGER,
                result TEXT DEFAULT 'PENDING',
                result_source TEXT DEFAULT 'CANDLE_ESTIMATE',
                reason TEXT DEFAULT '',
                UNIQUE(asset, entry_time, strategy)
            )
        """)
        conn.commit()


def save_signal(s, entry_time):
    with db() as conn:
        cur = conn.execute("""
            INSERT OR IGNORE INTO signals (
                asset, display_pair, direction, strategy, score,
                payout, signal_time, entry_time, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            s["asset"], s["display"], s["direction"], s["strategy"],
            s["score"], s["payout"], int(time.time()), entry_time,
            s.get("reason", ""),
        ))
        conn.commit()
        return cur.lastrowid if cur.rowcount else None


def set_result(signal_id, result, source="CANDLE_ESTIMATE"):
    with db() as conn:
        conn.execute(
            "UPDATE signals SET result=?, result_source=? WHERE id=?",
            (result, source, signal_id),
        )
        conn.commit()


# ============================================================
# QUOTEX CLIENT
# ============================================================

client = None
connect_lock = asyncio.Lock()
asset_cache = {}


async def connect_quotex():
    global client

    async with connect_lock:
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

            log.info("Connected to Quotex")
            return True

        except Exception:
            log.exception("Quotex connection error")
            client = None
            return False


async def close_quotex():
    global client
    if client is not None:
        try:
            await client.close()
        except Exception:
            log.exception("Quotex close error")
        client = None


async def get_live_assets():
    if not await connect_quotex():
        return {}

    try:
        assets = await client.get_all_assets()
        if isinstance(assets, dict):
            return assets
        return {}
    except Exception:
        log.exception("Unable to get Quotex asset list")
        return {}


def norm(value):
    value = str(value).lower()
    value = value.replace("(otc)", "").replace("_otc", "")
    return re.sub(r"[^a-z0-9]", "", value)


def is_otc(label):
    return "(otc)" in label.lower()


def resolve_asset(display, live_assets):
    """
    Resolve a displayed pair to an actual Quotex asset key.
    Never invent a key that is not returned by Quotex.
    """
    target = norm(display)
    candidates = [target]

    # Expand common full crypto names to possible ticker symbols.
    for name, symbols in ALIASES.items():
        if target == norm(name):
            candidates.extend(norm(symbol) for symbol in symbols)

    # Commodities may be exposed with different OTC suffix formats.
    commodity_aliases = {
        "ukbrent": ["ukbrent", "brent", "brentcrude"],
        "silver": ["silver", "xag"],
        "uscrude": ["uscrude", "wti", "oil"],
        "gold": ["gold", "xau"],
    }
    for name, values in commodity_aliases.items():
        if target == norm(name):
            candidates.extend(norm(v) for v in values)

    matches = []
    for actual in live_assets.keys():
        actual_norm = norm(actual)
        if actual_norm in candidates:
            matches.append(str(actual))

    if not matches:
        return None

    # Prefer OTC for requested OTC pairs, regular asset otherwise.
    wanted_otc = is_otc(display)
    matches.sort(
        key=lambda a: (
            0 if ("otc" in a.lower()) == wanted_otc else 1,
            len(a),
        )
    )
    return matches[0]


def get_payout(asset):
    if client is None:
        return None

    try:
        value = client.get_payout_by_asset(asset, timeframe="1")
        if value is None:
            return None

        value = float(value)
        if 0 < value <= 1:
            value *= 100

        return value if 0 <= value <= 100 else None

    except Exception:
        log.debug("Payout unavailable for %s", asset, exc_info=True)
        return None


async def get_candles(asset, count=100):
    if not await connect_quotex():
        return []

    try:
        raw = await client.get_candles(
            asset,
            time.time(),
            count * 60,
            60,
        )

        if not raw:
            return []

        output = []
        for candle in raw:
            try:
                ts = int(candle.get("time", candle.get("timestamp", 0)))
                if ts > 10_000_000_000:
                    ts //= 1000

                output.append({
                    "time": ts,
                    "open": float(candle["open"]),
                    "close": float(candle["close"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                })
            except (KeyError, ValueError, TypeError):
                continue

        output.sort(key=lambda c: c["time"])
        return list({c["time"]: c for c in output}.values())

    except Exception:
        log.warning("Candle request failed: %s", asset, exc_info=True)
        return []


# ============================================================
# MENUS / PAIR SELECTION
# ============================================================

PAGE_SIZE = 10
SCAN = {
    "running": False,
    "owner": None,
    "pairs": [],
    "strategy": "ALL",
    "task": None,
}


def pair_keyboard(context, category, page):
    t = T(context)
    pairs = PAIRS[category]
    start = page * PAGE_SIZE
    shown = pairs[start:start + PAGE_SIZE]
    selected = context.user_data.setdefault("selected_pairs", [])

    rows = []
    for i, pair in enumerate(shown, start=start):
        mark = "✅" if pair in selected else "▫️"
        rows.append([
            InlineKeyboardButton(
                f"{mark} {pair}",
                callback_data=f"pair:{category}:{i}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(t["prev"], callback_data=f"page:{category}:{page-1}")
        )
    if start + PAGE_SIZE < len(pairs):
        nav.append(
            InlineKeyboardButton(t["next"], callback_data=f"page:{category}:{page+1}")
        )
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(t["all"], callback_data=f"allpairs:{category}"),
    ])
    rows.append([
        InlineKeyboardButton(t["strategy"], callback_data="strategies"),
        InlineKeyboardButton(t["home"], callback_data="home"),
    ])
    return InlineKeyboardMarkup(rows)


def strategy_keyboard(context):
    t = T(context)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t["ai"], callback_data="strategy:AI")],
        [InlineKeyboardButton(t["all_strategy"], callback_data="strategy:ALL")],
        [InlineKeyboardButton(t["start"], callback_data="startscan")],
        [InlineKeyboardButton(t["stop"], callback_data="stopscan")],
        [InlineKeyboardButton(t["home"], callback_data="home")],
    ])


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("selected_pairs", [])
    if "lang" not in context.user_data:
        await update.message.reply_text(
            "🌐 Select your language / ভাষা নির্বাচন করুন / "
            "अपनी भाषा चुनें / زبان منتخب کریں:",
            reply_markup=language_keyboard(),
        )
        return

    t = T(context)
    await update.message.reply_text(
        t["category"],
        reply_markup=home_keyboard(context),
    )


# ============================================================
# SIGNAL / RESULT
# ============================================================

def next_minute():
    return (int(time.time()) // 60 + 1) * 60


def fmt_time(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%H:%M:%S")


def candle_result(direction, candle):
    op = candle["open"]
    cl = candle["close"]
    if cl == op:
        return "DRAW"
    if direction == "CALL":
        return "WIN" if cl > op else "LOSS"
    return "WIN" if cl < op else "LOSS"


async def publish_signal(app, s, entry_time):
    # Do not publish stale signals.
    if time.time() >= entry_time - 2:
        return

    signal_id = save_signal(s, entry_time)
    if signal_id is None:
        return

    t = LANGUAGES.get(SCAN.get("lang", "en"), LANGUAGES["en"])
    arrow = "🟢 CALL ⬆️" if s["direction"] == "CALL" else "🔴 PUT ⬇️"

    text = (
        "━━━━━━━━━━━━━━━━━━\n"
        "       🟦 STM PRO BOT 🟦\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 {s['display']} ({s['asset']})\n"
        f"📊 {t['signal']}: {arrow}\n"
        f"⏰ {t['entry']}: {fmt_time(entry_time)}\n"
        f"⌛ {t['expiry']}: 1 Minute\n"
        f"💰 Payout: {s['payout']:.0f}%\n"
        f"🧠 Strategy: {s['strategy']}\n"
        f"⭐ Score: {s['score']:.2f}\n"
        f"📝 {t['reason']}: {s.get('reason', '')}\n\n"
        f"⚠️ {t['disclaimer']}\n\n"
        "       STM PRO BOT"
    )

    try:
        await app.bot.send_message(chat_id=TG_CHANNEL_ID, text=text)
        asyncio.create_task(resolve_result(app, signal_id, s, entry_time))
    except Exception:
        log.exception("Signal publishing failed")


async def find_candle(asset, timestamp):
    for _ in range(6):
        candles = await get_candles(asset, 10)
        for candle in candles:
            if int(candle["time"]) == int(timestamp):
                return candle
        await asyncio.sleep(2)
    return None


async def resolve_result(app, signal_id, s, entry_time):
    await asyncio.sleep(max(0, entry_time + 62 - time.time()))
    candle = await find_candle(s["asset"], entry_time)

    if candle is None:
        set_result(signal_id, "UNKNOWN", "CANDLE_DATA_UNAVAILABLE")
        result = "UNKNOWN"
    else:
        result = candle_result(s["direction"], candle)
        set_result(signal_id, result, "CANDLE_ESTIMATE")

    lang = SCAN.get("lang", "en")
    t = LANGUAGES.get(lang, LANGUAGES["en"])
    label = {
        "WIN": t["win"],
        "LOSS": t["loss"],
        "DRAW": t["draw"],
        "UNKNOWN": t["unknown"],
    }[result]

    try:
        await app.bot.send_message(
            chat_id=TG_CHANNEL_ID,
            text=(
                "━━━━━━━━━━━━━━━━━━\n"
                f"📊 STM PRO BOT — {t['result']}\n\n"
                f"💱 {s['display']}\n"
                f"📈 {s['direction']}\n"
                f"🏁 {label}\n\n"
                f"ℹ️ {t['disclaimer']}\n"
                "━━━━━━━━━━━━━━━━━━"
            ),
        )
    except Exception:
        log.exception("Result message failed")


# ============================================================
# SCANNER
# ============================================================

async def analyze_pair(display, asset, strategy):
    candles = await get_candles(asset, 100)
    if len(candles) < 60:
        return None

    # Do not analyze stale market data.
    if abs(time.time() - candles[-1]["time"]) > 150:
        return None

    payout = get_payout(asset)
    if payout is None or payout < MIN_PAYOUT:
        return None

    try:
        result = analyze_ai(candles) if strategy == "AI" else analyze_all(candles)
    except Exception:
        log.exception("Strategy error: %s", asset)
        return None

    if not result:
        return None

    direction = str(result.get("direction", "")).upper()
    score = float(result.get("score", 0))
    if direction not in ("CALL", "PUT") or score < MIN_SCORE:
        return None

    # Avoid exceptionally large candles.
    ranges = [c["high"] - c["low"] for c in candles[-21:-1]]
    if ranges:
        average = sum(ranges) / len(ranges)
        last_range = candles[-1]["high"] - candles[-1]["low"]
        if average > 0 and last_range > average * 2.5:
            return None

    return {
        "display": display,
        "asset": asset,
        "direction": direction,
        "score": score,
        "payout": payout,
        "strategy": strategy,
        "reason": str(result.get("reason", "")),
    }


async def scanning_loop(app):
    log.info("Scanning started")
    while SCAN["running"]:
        started = time.time()
        live_assets = await get_live_assets()

        if not live_assets:
            log.error("No live assets received from Quotex")
            await asyncio.sleep(10)
            continue

        resolved = []
        unavailable = []

        for display in SCAN["pairs"]:
            actual = resolve_asset(display, live_assets)
            if actual is None:
                unavailable.append(display)
            else:
                resolved.append((display, actual))

        if unavailable:
            log.info("Unavailable pairs: %s", ", ".join(unavailable))

        results = await asyncio.gather(
            *[
                analyze_pair(display, actual, SCAN["strategy"])
                for display, actual in resolved
            ],
            return_exceptions=True,
        )

        valid = [
            r for r in results
            if isinstance(r, dict)
        ]

        if valid and SCAN["running"]:
            entry = next_minute()
            # Publish 5 seconds before entry, not after it.
            publish_at = entry - 5
            delay = publish_at - time.time()
            if delay > 0:
                await asyncio.sleep(delay)

            if SCAN["running"] and time.time() < entry - 1:
                for signal in valid:
                    await publish_signal(app, signal, entry)

        await asyncio.sleep(max(0.5, SCAN_INTERVAL - (time.time() - started)))

    log.info("Scanning stopped")


# ============================================================
# CALLBACKS
# ============================================================

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    t = T(context)

    if data == "language":
        await query.edit_message_text(
            "🌐 Select your language:",
            reply_markup=language_keyboard(),
        )
        return

    if data.startswith("lang:"):
        context.user_data["lang"] = data.split(":", 1)[1]
        context.user_data.setdefault("selected_pairs", [])
        t = T(context)
        await query.edit_message_text(
            f"{t['language_changed']}\n\n{t['category']}",
            reply_markup=home_keyboard(context),
        )
        return

    if "lang" not in context.user_data:
        await query.edit_message_text(
            "🌐 Select your language first:",
            reply_markup=language_keyboard(),
        )
        return

    if data == "home":
        await query.edit_message_text(
            t["category"],
            reply_markup=home_keyboard(context),
        )
        return

    if data.startswith("cat:"):
        category = data.split(":", 1)[1]
        context.user_data["category"] = category
        context.user_data["page"] = 0
        await query.edit_message_text(
            f"{t[category]}\n\n{t['pairs']}",
            reply_markup=pair_keyboard(context, category, 0),
        )
        return

    if data.startswith("page:"):
        _, category, page = data.split(":")
        page = int(page)
        context.user_data["page"] = page
        await query.edit_message_text(
            f"{t[category]}\n\n{t['pairs']}",
            reply_markup=pair_keyboard(context, category, page),
        )
        return

    if data.startswith("pair:"):
        _, category, idx = data.split(":")
        pair = PAIRS[category][int(idx)]
        selected = context.user_data.setdefault("selected_pairs", [])

        if pair in selected:
            selected.remove(pair)
        else:
            selected.append(pair)

        page = context.user_data.get("page", 0)
        await query.edit_message_reply_markup(
            reply_markup=pair_keyboard(context, category, page),
        )
        return

    if data.startswith("allpairs:"):
        category = data.split(":", 1)[1]
        selected = context.user_data.setdefault("selected_pairs", [])
        for pair in PAIRS[category]:
            if pair not in selected:
                selected.append(pair)

        await query.edit_message_text(
            f"{t['selected']}: {len(selected)}\n\n{t['strategy']}",
            reply_markup=strategy_keyboard(context),
        )
        return

    if data == "strategies":
        selected = context.user_data.setdefault("selected_pairs", [])
        await query.edit_message_text(
            f"{t['selected']}: {len(selected)}\n{t['strategy']}",
            reply_markup=strategy_keyboard(context),
        )
        return

    if data.startswith("strategy:"):
        context.user_data["strategy"] = data.split(":", 1)[1]
        await query.edit_message_text(
            f"{t['strategy']}\n"
            f"Selected: {len(context.user_data.get('selected_pairs', []))}",
            reply_markup=strategy_keyboard(context),
        )
        return

    if data == "startscan":
        if SCAN["running"]:
            await query.edit_message_text(
                "A scan is already running.",
                reply_markup=strategy_keyboard(context),
            )
            return

        selected = context.user_data.get("selected_pairs", [])
        if not selected:
            await query.edit_message_text(
                t["none"],
                reply_markup=home_keyboard(context),
            )
            return

        if not TG_CHANNEL_ID:
            await query.edit_message_text(
                "Set TG_CHANNEL_ID in .env first.",
                reply_markup=strategy_keyboard(context),
            )
            return

        await query.edit_message_text(t["connecting"])

        if not await connect_quotex():
            await query.edit_message_text(
                t["failed"],
                reply_markup=strategy_keyboard(context),
            )
            return

        SCAN["running"] = True
        SCAN["owner"] = query.from_user.id
        SCAN["pairs"] = selected[:]
        SCAN["strategy"] = context.user_data.get("strategy", "ALL")
        SCAN["lang"] = context.user_data.get("lang", "en")
        SCAN["task"] = asyncio.create_task(scanning_loop(context.application))

        await query.edit_message_text(
            f"{t['started']}\n"
            f"{t['selected']}: {len(selected)}\n"
            f"Strategy: {SCAN['strategy']}",
            reply_markup=strategy_keyboard(context),
        )
        return

    if data == "stopscan":
        if SCAN["owner"] not in (None, query.from_user.id):
            await query.edit_message_text(
                "Only the user who started scanning can stop it."
            )
            return

        SCAN["running"] = False
        await query.edit_message_text(
            t["stopped"],
            reply_markup=home_keyboard(context),
        )
        return

    if data == "status":
        await query.edit_message_text(
            f"Running: {SCAN['running']}\n"
            f"Pairs: {len(SCAN['pairs'])}\n"
            f"Strategy: {SCAN['strategy']}",
            reply_markup=home_keyboard(context),
        )


# ============================================================
# STARTUP
# ============================================================

async def post_init(app):
    init_db()
    log.info("STM PRO BOT initialized")


async def post_shutdown(app):
    SCAN["running"] = False
    await close_quotex()


def main():
    if not TG_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN is missing in .env")

    init_db()

    app = (
        Application.builder()
        .token(TG_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(callback))

    log.info("STM PRO BOT starting")
    app.run_polling()


if __name__ == "__main__":
    main()
