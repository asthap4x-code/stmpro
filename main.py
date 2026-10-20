
"""STM Pro Bot — main.py
Telegram signal bot with all-market scanning and manual selection.
Requires: python-telegram-bot and pyquotex.
"""

import asyncio
import logging
import os
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from pyquotex.stable_api import Quotex
from strategy import analyse, normalize_candle, candle_color


# =========================================================
# CONFIGURATION — set these as environment variables
# =========================================================

TG_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "").strip()
EMAIL = os.getenv("QUOTEX_EMAIL", "").strip()
PASSWORD = os.getenv("QUOTEX_PASSWORD", "")

ALLOWED_IDS = {
    x.strip()
    for x in os.getenv("STM_ALLOWED_USER_IDS", "").split(",")
    if x.strip()
}

CANDLE_PERIOD = 60
CANDLE_HISTORY_OFFSET = 3600
SCAN_DELAY = 1
SCAN_CYCLE_DELAY = 5
RESULT_WAIT_SECONDS = 65

# =========================================================
# MARKET LIST
# =========================================================

ASSETS = {
    "Forex": {
        "EUR/USD": "EURUSD",
        "GBP/USD": "GBPUSD",
        "USD/JPY": "USDJPY",
        "USD/CHF": "USDCHF",
        "USD/CAD": "USDCAD",
        "AUD/USD": "AUDUSD",
        "NZD/USD": "NZDUSD",
    },
    "Stock Index": {
        "S&P/ASX 200": "AUS200",
        "FTSE China A50 Index": "ChinaA50",
        "CAC 40": "F40",
        "FTSE 100": "UK100",
        "Hong Kong 50": "HK50",
        "IBEX 35": "ESP35",
        "Nikkei 225": "JP225",
        "EURO STOXX 50": "EUSTX50",
    },
    "Commodities": {
        "UKBrent (OTC)": "UKBrent_otc",
        "Silver (OTC)": "XAGUSD_otc",
        "USCrude (OTC)": "USCrude_otc",
        "Gold (OTC)": "XAUUSD_otc",
    },
    "Crypto": {
        "Bitcoin (OTC)": "BTCUSD_otc",
        "Ethereum Classic (OTC)": "ETCUSD_otc",
        "Chainlink (OTC)": "LINKUSD_otc",
        "Litecoin (OTC)": "LTCUSD_otc",
        "Toncoin (OTC)": "TONUSD_otc",
        "Trump (OTC)": "TRUMPUSD_otc",
        "Ripple (OTC)": "XRPUSD_otc",
        "Zcash (OTC)": "ZECUSD_otc",
        "Avalanche (OTC)": "AVAXUSD_otc",
        "Dash (OTC)": "DASHUSD_otc",
        "Axie Infinity (OTC)": "AXSUSD_otc",
        "Polkadot (OTC)": "DOTUSD_otc",
        "Binance Coin (OTC)": "BNBUSD_otc",
        "Ethereum (OTC)": "ETHUSD_otc",
        "Cosmos (OTC)": "ATOMUSD_otc",
        "Bitcoin Cash (OTC)": "BCHUSD_otc",
    },
    "Currency OTC": {
        "USD/ARS (OTC)": "USDARS_otc",
        "USD/BDT (OTC)": "USDBDT_otc",
        "USD/BRL (OTC)": "USDBRL_otc",
        "USD/CAD (OTC)": "USDCAD_otc",
        "USD/CHF (OTC)": "USDCHF_otc",
        "USD/COP (OTC)": "USDCOP_otc",
        "USD/DZD (OTC)": "USDDZD_otc",
        "USD/EGP (OTC)": "USDEGP_otc",
        "USD/IDR (OTC)": "USDIDR_otc",
        "USD/INR (OTC)": "USDINR_otc",
        "USD/JPY (OTC)": "USDJPY_otc",
        "USD/MXN (OTC)": "USDMXN_otc",
        "USD/NGN (OTC)": "USDNGN_otc",
        "USD/PHP (OTC)": "USDPHP_otc",
        "USD/PKR (OTC)": "USDPKR_otc",
        "USD/ZAR (OTC)": "USDZAR_otc",
        "AUD/CAD (OTC)": "AUDCAD_otc",
        "AUD/CHF (OTC)": "AUDCHF_otc",
        "AUD/JPY (OTC)": "AUDJPY_otc",
        "AUD/NZD (OTC)": "AUDNZD_otc",
        "AUD/USD (OTC)": "AUDUSD_otc",
        "CAD/CHF (OTC)": "CADCHF_otc",
        "CAD/JPY (OTC)": "CADJPY_otc",
        "CHF/JPY (OTC)": "CHFJPY_otc",
        "EUR/AUD (OTC)": "EURAUD_otc",
        "EUR/CAD (OTC)": "EURCAD_otc",
        "EUR/CHF (OTC)": "EURCHF_otc",
        "EUR/GBP (OTC)": "EURGBP_otc",
        "EUR/JPY (OTC)": "EURJPY_otc",
        "EUR/NZD (OTC)": "EURNZD_otc",
        "EUR/USD (OTC)": "EURUSD_otc",
        "GBP/AUD (OTC)": "GBPAUD_otc",
        "GBP/CAD (OTC)": "GBPCAD_otc",
        "GBP/CHF (OTC)": "GBPCHF_otc",
        "GBP/JPY (OTC)": "GBPJPY_otc",
        "GBP/NZD (OTC)": "GBPNZD_otc",
        "GBP/USD (OTC)": "GBPUSD_otc",
        "NZD/CAD (OTC)": "NZDCAD_otc",
        "NZD/CHF (OTC)": "NZDCHF_otc",
        "NZD/JPY (OTC)": "NZDJPY_otc",
        "NZD/USD (OTC)": "NZDUSD_otc",
    },
}


# =========================================================
# GLOBAL STATE
# =========================================================

client = None
connected = False
enabled = False

# Empty selection means ALL markets.
selected = []

last_error = "Not connected"
seen_signals = set()
pending_results = []

stats = {
    "signals": 0,
    "wins": 0,
    "losses": 0,
    "unknown": 0,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("stm")


# =========================================================
# HELPERS
# =========================================================

def authorized(update: Update) -> bool:
    """If allowed IDs are configured, restrict bot commands to them."""
    if not ALLOWED_IDS:
        return True

    user = update.effective_user
    return bool(user and str(user.id) in ALLOWED_IDS)


def all_assets():
    return [
        {
            "category": category,
            "name": name,
            "symbol": symbol,
        }
        for category, items in ASSETS.items()
        for name, symbol in items.items()
    ]


def find_asset(query):
    query = query.casefold().strip()

    for item in all_assets():
        if query in (
            item["name"].casefold(),
            item["symbol"].casefold(),
        ):
            return item

    return None


def active_assets():
    """Selected markets if any; otherwise every listed market."""
    return selected[:] if selected else all_assets()


def market_mode():
    return "SELECTED" if selected else "ALL MARKETS"


# =========================================================
# QUOTEX CONNECTION
# =========================================================

async def connect():
    global client, connected, last_error

    if not EMAIL or not PASSWORD:
        connected = False
        last_error = (
            "Missing QUOTEX_EMAIL or QUOTEX_PASSWORD "
            "environment variable."
        )
        return False

    try:
        if client is None:
            client = Quotex(
                email=EMAIL,
                password=PASSWORD,
                lang="en",
            )

        result = await client.connect()

        if isinstance(result, tuple):
            connected = bool(result[0])
        else:
            connected = bool(result)

        last_error = "" if connected else (
            f"Quotex connection failed: {result!r}"
        )

        if connected:
            log.info("Quotex connected successfully")
        else:
            log.warning("%s", last_error)

        return connected

    except Exception as exc:
        connected = False
        last_error = f"{type(exc).__name__}: {exc}"
        log.exception("Quotex connection failed")
        return False


async def get_candles(symbol):
    """Fetch and normalize candles; discard incomplete latest candles."""
    global connected, last_error

    if not connected and not await connect():
        return []

    try:
        raw = await client.get_candles(
            symbol,
            int(time.time()),
            CANDLE_HISTORY_OFFSET,
            CANDLE_PERIOD,
        )

        if isinstance(raw, dict):
            raw = raw.get(
                "candles",
                raw.get("data", raw.get("history", [])),
            )

        if not isinstance(raw, (list, tuple)):
            last_error = (
                "Unexpected candle response: "
                f"{type(raw).__name__}"
            )
            log.warning("%s | %s", symbol, last_error)
            return []

        candles = []

        for row in raw:
            candle = normalize_candle(row)
            if candle is not None:
                candles.append(candle)

        candles.sort(key=lambda item: item["time"])

        # Remove duplicate timestamps.
        unique = {}
        for candle in candles:
            unique[candle["time"]] = candle

        candles = list(unique.values())
        candles.sort(key=lambda item: item["time"])

        # Only use fully closed candles.
        now = int(time.time())
        candles = [
            candle
            for candle in candles
            if candle["time"] + CANDLE_PERIOD <= now
        ]

        if not candles:
            sample = raw[0] if raw else None
            last_error = (
                "No usable closed candles. "
                f"Sample: {sample!r}"
            )
            log.warning("%s | %s", symbol, last_error)
            return []

        last_error = ""
        return candles

    except Exception as exc:
        connected = False
        last_error = f"{type(exc).__name__}: {exc}"
        log.exception("Candle request failed for %s", symbol)
        return []


# =========================================================
# TELEGRAM CHANNEL
# =========================================================

async def post_channel(app, message):
    if not CHANNEL_ID:
        log.error("TG_CHANNEL_ID is not configured.")
        return False

    try:
        await app.bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
        )
        return True

    except Exception:
        log.exception("Telegram channel post failed")
        return False


# =========================================================
# SIGNAL GENERATION
# =========================================================

async def make_signal(app, item, manual=False):
    candles = await get_candles(item["symbol"])

    if not candles:
        return (
            f"⚠️ Data unavailable: {item['name']}\n"
            f"Symbol: {item['symbol']}\n"
            f"Reason: {last_error}"
        )

    try:
        result = analyse(candles)
    except Exception as exc:
        log.exception("Strategy failed for %s", item["symbol"])
        return (
            f"⚠️ Strategy error: {item['name']}\n"
            f"{type(exc).__name__}: {exc}"
        )

    if result is None:
        return (
            f"⏳ {item['name']}: not enough valid candles "
            f"({len(candles)} received)."
        )

    direction = result.get("direction")

    if direction not in ("CALL", "PUT"):
        return (
            f"⏸ {item['name']}: no signal.\n"
            f"Reason: {direction or 'WAIT'}"
        )

    candle = result.get("candle") or candles[-1]

    key = (
        item["symbol"],
        candle["time"],
        direction,
    )

    if not manual and key in seen_signals:
        return f"No new candle signal for {item['name']}."

    reasons = result.get("reasons", [])
    reasons_text = (
        "\n".join("• " + str(reason) for reason in reasons)
        or "• Strategy conditions passed"
    )

    confidence = result.get("confidence")
    confidence_text = (
        f"{confidence}% model score (not win probability)"
        if confidence is not None
        else "Not calibrated"
    )

    support = result.get("support")
    resistance = result.get("resistance")

    support_text = (
        f"{support:.6g}" if isinstance(support, (int, float))
        else "N/A"
    )
    resistance_text = (
        f"{resistance:.6g}"
        if isinstance(resistance, (int, float))
        else "N/A"
    )

    message = (
        "⚡ STM PRO | 1-MINUTE SIGNAL\n\n"
        f"Category: {item['category']}\n"
        f"Asset: {item['name']}\n"
        f"Symbol: {item['symbol']}\n"
        f"Direction: "
        f"{'🟢 CALL / UP' if direction == 'CALL' else '🔴 PUT / DOWN'}\n"
        "Expiry: 1 minute\n"
        f"Model: {confidence_text}\n"
        f"Support: {support_text}\n"
        f"Resistance: {resistance_text}\n"
        f"Candle time: {candle['time']}\n\n"
        f"Reasons:\n{reasons_text}\n\n"
        "Analysis only. No trade was placed.\n"
        "Signals are uncertain; losses are possible."
    )

    posted = await post_channel(app, message)

    if posted:
        seen_signals.add(key)
        stats["signals"] += 1

        pending_results.append({
            "item": item,
            "direction": direction,
            "signal_time": candle["time"],
            "sent_at": time.time(),
        })

        if len(seen_signals) > 5000:
            seen_signals.clear()

        log.info(
            "Signal posted: %s %s",
            item["symbol"],
            direction,
        )

        return message

    return (
        f"⚠️ Signal calculated but Telegram post failed.\n"
        f"Asset: {item['name']}\n"
        f"Direction: {direction}\n"
        "Check bot permissions and TG_CHANNEL_ID."
    )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    await update.message.reply_text(
        "⚡ STM PRO SIGNAL BOT\n\n"
        "Default mode: ALL MARKETS\n\n"
        "/categories — list market categories\n"
        "/assets — choose markets\n"
        "/select EUR/USD — select one market\n"
        "/auto — use all markets\n"
        "/clear — clear selection (all markets mode)\n"
        "/on — start scanning\n"
        "/off — stop scanning\n"
        "/signal EURUSD — analyse one market\n"
        "/status — bot status\n"
        "/stats — signal result statistics"
    )


async def categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    parts = []

    for category, items in ASSETS.items():
        parts.append(
            f"{category} ({len(items)}):\n"
            + ", ".join(items.keys())
        )

    message = "\n\n".join(parts)

    # Avoid Telegram's message-length limit.
    for start_index in range(0, len(message), 3500):
        await update.message.reply_text(
            message[start_index:start_index + 3500]
        )


async def assets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    keyboard = [
        [
            InlineKeyboardButton(
                category,
                callback_data=f"cat:{category}",
            )
        ]
        for category in ASSETS
    ]

    keyboard.append([
        InlineKeyboardButton(
            "🌐 All markets",
            callback_data="auto",
        )
    ])

    await update.message.reply_text(
        f"Mode: {market_mode()}\n"
        "Choose a category or all markets:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global selected

    query = update.callback_query
    await query.answer()

    if not authorized(update):
        await query.edit_message_text("Not authorized.")
        return

    if query.data == "auto":
        selected = []
        await query.edit_message_text(
            f"🌐 All-market mode enabled.\n"
            f"Markets: {len(all_assets())}\n"
            "Use /on to start scanning."
        )
        return

    if query.data.startswith("cat:"):
        category = query.data[4:]

        if category not in ASSETS:
            await query.edit_message_text("Unknown category.")
            return

        keyboard = []

        for name in ASSETS[category]:
            keyboard.append([
                InlineKeyboardButton(
                    name,
                    callback_data=f"pick:{category}|{name}",
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ Categories",
                callback_data="back",
            )
        ])

        keyboard.append([
            InlineKeyboardButton(
                "🌐 All markets",
                callback_data="auto",
            )
        ])

        await query.edit_message_text(
            f"Choose an asset from {category}:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if query.data == "back":
        keyboard = [
            [
                InlineKeyboardButton(
                    category,
                    callback_data=f"cat:{category}",
                )
            ]
            for category in ASSETS
        ]

        keyboard.append([
            InlineKeyboardButton(
                "🌐 All markets",
                callback_data="auto",
            )
        ])

        await query.edit_message_text(
            "Choose a category:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if query.data.startswith("pick:"):
        try:
            category, name = query.data[5:].split("|", 1)
            symbol = ASSETS[category][name]
        except (ValueError, KeyError):
            await query.edit_message_text("Invalid market selection.")
            return

        item = {
            "category": category,
            "name": name,
            "symbol": symbol,
        }

        if all(x["symbol"] != symbol for x in selected):
            selected.append(item)

        await query.edit_message_text(
            f"✅ Added: {name} ({symbol})\n"
            f"Selected markets: {len(selected)}\n"
            "Only selected markets will be scanned.\n"
            "Use /assets to add more, /clear for all markets, "
            "or /on to start."
        )


async def select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Example: /select EUR/USD\n"
            "Use /categories for market names."
        )
        return

    item = find_asset(" ".join(context.args))

    if not item:
        await update.message.reply_text(
            "Market not found. Use /categories."
        )
        return

    if all(x["symbol"] != item["symbol"] for x in selected):
        selected.append(item)

    await update.message.reply_text(
        f"✅ Selected: {item['name']} ({item['symbol']})\n"
        f"Selected count: {len(selected)}\n"
        "Only selected markets will be scanned.\n"
        "Use /on to start."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global selected

    if not authorized(update):
        return

    selected = []

    await update.message.reply_text(
        f"🌐 Selection cleared.\n"
        f"Mode: ALL MARKETS ({len(all_assets())} markets)\n"
        "Use /on to start or continue scanning."
    )


async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global selected

    if not authorized(update):
        return

    selected = []

    await update.message.reply_text(
        f"🌐 All-market mode enabled.\n"
        f"Total listed markets: {len(all_assets())}\n"
        "Use /on to start scanning."
    )


async def on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global enabled

    if not authorized(update):
        return

    enabled = True

    await update.message.reply_text(
        "✅ Scanning enabled.\n"
        f"Mode: {market_mode()}\n"
        f"Markets to scan: {len(active_assets())}"
    )


async def off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global enabled

    if not authorized(update):
        return

    enabled = False
    await update.message.reply_text("⏹ Scanning stopped.")


async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Example: /signal EURUSD"
        )
        return

    item = find_asset(" ".join(context.args))

    if not item:
        await update.message.reply_text(
            "Market not found. Use /categories."
        )
        return

    response = await make_signal(
        context.application,
        item,
        manual=True,
    )

    await update.message.reply_text(response)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    await update.message.reply_text(
        f"Scanning: {enabled}\n"
        f"Quotex connected: {connected}\n"
        f"Telegram channel configured: {bool(CHANNEL_ID)}\n"
        f"Mode: {market_mode()}\n"
        f"Markets to scan: {len(active_assets())}\n"
        f"Last error: {last_error or 'None'}"
    )


async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not authorized(update):
        return

    total = stats["wins"] + stats["losses"]
    rate = (
        stats["wins"] / total * 100
        if total else 0
    )

    await update.message.reply_text(
        f"Signals posted: {stats['signals']}\n"
        f"Estimated wins: {stats['wins']}\n"
        f"Estimated losses: {stats['losses']}\n"
        f"Unknown results: {stats['unknown']}\n"
        f"Estimated win rate: {rate:.1f}%\n\n"
        "Candle-based estimates only; not verified broker results."
    )


# =========================================================
# SIGNAL RESULT MONITOR
# =========================================================

async def result_loop(app):
    while True:
        now = time.time()

        for pending in pending_results[:]:
            if now - pending["sent_at"] < RESULT_WAIT_SECONDS:
                continue

            item = pending["item"]

            try:
                candles = await get_candles(item["symbol"])
                target_time = (
                    pending["signal_time"] + CANDLE_PERIOD
                )

                target = next(
                    (
                        candle for candle in candles
                        if candle["time"] == target_time
                    ),
                    None,
                )

                if target is None:
                    # Do not label an arbitrary later candle as
                    # the exact expiry candle.
                    stats["unknown"] += 1
                    outcome = "UNKNOWN — expiry candle unavailable"

                else:
                    colour = candle_color(target)

                    win = (
                        pending["direction"] == "CALL"
                        and colour == "GREEN"
                    ) or (
                        pending["direction"] == "PUT"
                        and colour == "RED"
                    )

                    if colour == "DOJI":
                        stats["unknown"] += 1
                        outcome = "UNKNOWN — doji expiry candle"
                    elif win:
                        stats["wins"] += 1
                        outcome = "WIN (candle-direction estimate)"
                    else:
                        stats["losses"] += 1
                        outcome = "LOSS (candle-direction estimate)"

                await post_channel(
                    app,
                    "📊 STM RESULT\n"
                    f"Asset: {item['name']}\n"
                    f"Signal: {pending['direction']}\n"
                    f"Result: {outcome}\n\n"
                    "Candle estimate only; not verified broker settlement.",
                )

            except Exception:
                log.exception(
                    "Result check failed for %s",
                    item["symbol"],
                )
                stats["unknown"] += 1

            finally:
                if pending in pending_results:
                    pending_results.remove(pending)

        await asyncio.sleep(5)


# =========================================================
# AUTOMATIC SCANNER
# =========================================================

async def scan_loop(app):
    while True:
        if enabled:
            current = active_assets()

            log.info(
                "Scanning %d markets | mode=%s",
                len(current),
                market_mode(),
            )

            for item in current:
                if not enabled:
                    break

                try:
                    await make_signal(app, item)
                except Exception:
                    log.exception(
                        "Scan failed for %s",
                        item["symbol"],
                    )

                await asyncio.sleep(SCAN_DELAY)

        await asyncio.sleep(SCAN_CYCLE_DELAY)


async def post_init(app):
    app.create_task(scan_loop(app))
    app.create_task(result_loop(app))


# =========================================================
# START BOT
# =========================================================

def main():
    if not TG_TOKEN:
        raise SystemExit(
            "Missing TG_BOT_TOKEN environment variable."
        )

    if not EMAIL or not PASSWORD:
        raise SystemExit(
            "Set QUOTEX_EMAIL and QUOTEX_PASSWORD environment variables."
        )

    if not CHANNEL_ID:
        raise SystemExit(
            "Set TG_CHANNEL_ID environment variable."
        )

    if not ALLOWED_IDS:
        log.warning(
            "STM_ALLOWED_USER_IDS is empty; commands are not restricted "
            "to specific Telegram user IDs."
        )

    app = (
        Application.builder()
        .token(TG_TOKEN)
        .post_init(post_init)
        .build()
    )

    handlers = {
        "start": start,
        "categories": categories,
        "assets": assets,
        "select": select,
        "clear": clear,
        "auto": auto,
        "on": on,
        "off": off,
        "signal": signal,
        "status": status,
        "stats": stats_command,
    }

    for command, handler in handlers.items():
        app.add_handler(CommandHandler(command, handler))

    app.add_handler(CallbackQueryHandler(button))

    log.info("STM Pro Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
