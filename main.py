
import asyncio
import logging
import os
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

from pyquotex.stable_api import Quotex
from strategy import analyse, normalize_candle, candle_color

TG_TOKEN = os.getenv("TG_BOT_TOKEN", "8994179085:AAEbtj-dnNOzWiteG1fz1CGiddUOpRfQBNs)
CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "1004308221939")
EMAIL = os.getenv("QUOTEX_EMAIL", "janinakihoy00@gmail.com")
PASSWORD = os.getenv("QUOTEX_PASSWORD", "71&@$@HwvIvw&$#")

# Set this to your Telegram numeric user ID.
ALLOWED_IDS = {
    x.strip()
    for x in os.getenv("STM_ALLOWED_USER_IDS", "").split(",")
    if x.strip()
}

# All five categories and their candidate Quotex symbols.
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

AUTO_LIMIT = int(os.getenv("STM_AUTO_SCAN_LIMIT", "5"))
client = None
connected = False
enabled = False
auto_select = False
selected = []
last_error = "Not connected"
seen_signals = set()
pending_results = []
stats = {"signals": 0, "wins": 0, "losses": 0, "unknown": 0}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stm")


def authorized(update):
    return (
        not ALLOWED_IDS
        or str(update.effective_user.id) in ALLOWED_IDS
    )


def all_assets():
    return [
        {"category": category, "name": name, "symbol": symbol}
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


async def connect():
    global client, connected, last_error

    if not EMAIL or not PASSWORD:
        last_error = "Quotex credentials are not configured."
        return False

    try:
        client = Quotex(email=EMAIL, password=PASSWORD, lang="en")
        result = await client.connect()
        connected = bool(result[0]) if isinstance(result, tuple) else bool(result)
        last_error = "" if connected else f"Connection failed: {result!r}"
        return connected
    except Exception as exc:
        connected = False
        last_error = f"{type(exc).__name__}: {exc}"
        log.exception("Quotex connection failed")
        return False


async def get_candles(symbol):
    global connected, last_error

    if not connected and not await connect():
        return []

    try:
        # end_from_time, offset, period
        raw = await client.get_candles(
            symbol, int(time.time()), 3600, 60
        )

        if isinstance(raw, dict):
            raw = raw.get("candles", raw.get("data", raw.get("history", [])))

        if not isinstance(raw, (list, tuple)):
            last_error = f"Unexpected candle response: {type(raw).__name__}"
            return []

        candles = [normalize_candle(row) for row in raw]
        candles = [c for c in candles if c]
        candles.sort(key=lambda c: c["time"])

        if not candles:
            sample = raw[0] if raw else None
            last_error = f"No usable candle data. Sample: {sample!r}"
            log.warning("%s | %s", symbol, last_error)
        else:
            last_error = ""

        return candles

    except Exception as exc:
        connected = False
        last_error = f"{type(exc).__name__}: {exc}"
        log.exception("Candle request failed for %s", symbol)
        return []


async def post_channel(app, message):
    if not CHANNEL_ID:
        log.error("TG_CHANNEL_ID is not set.")
        return False

    try:
        await app.bot.send_message(chat_id=CHANNEL_ID, text=message)
        return True
    except Exception:
        log.exception("Telegram channel post failed")
        return False


async def make_signal(app, item, manual=False):
    candles = await get_candles(item["symbol"])

    if not candles:
        return (
            f"⚠️ Data unavailable: {item['name']} ({item['symbol']})\n"
            f"{last_error}"
        )

    result = analyse(candles)
    if result is None:
        return f"Need at least 25 valid candles; received {len(candles)}."

    if result["direction"] == "WAIT":
        return f"⏸ {item['name']}: mixed setup; no signal."

    candle = result["candle"]
    key = (item["symbol"], candle["time"], result["direction"])

    if not manual and key in seen_signals:
        return f"No new candle signal for {item['name']}."

    seen_signals.add(key)
    if len(seen_signals) > 1000:
        seen_signals.clear()

    stats["signals"] += 1
    reasons = "\n".join("• " + reason for reason in result["reasons"])

    message = (
        "⚡ STM PRO | 1-MINUTE SIGNAL\n\n"
        f"Category: {item['category']}\n"
        f"Asset: {item['name']}\n"
        f"Symbol: {item['symbol']}\n"
        f"Direction: {'🟢 CALL / UP' if result['direction'] == 'CALL' else '🔴 PUT / DOWN'}\n"
        "Expiry: 1 minute\n"
        f"Model score: {result['confidence']}% (not a win probability)\n"
        f"Support: {result['support']:.6g}\n"
        f"Resistance: {result['resistance']:.6g}\n\n"
        f"Reasons:\n{reasons}\n\n"
        "Analysis only. No trade was placed. High risk."
    )

    posted = await post_channel(app, message)

    if posted:
        pending_results.append({
            "item": item,
            "direction": result["direction"],
            "signal_time": candle["time"],
            "sent_at": time.time(),
        })

    return message


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("Not authorized.")
        return

    await update.message.reply_text(
        "STM Pro Signal Bot\n\n"
        "/categories — list categories\n"
        "/assets — choose a category\n"
        "/select EUR/USD — select an asset\n"
        "/auto — auto-select first assets\n"
        "/clear — clear selection\n"
        "/on — start scanning\n"
        "/off — stop scanning\n"
        "/signal EURUSD — analyse one asset\n"
        "/status — connection status\n"
        "/stats — result statistics"
    )


async def categories(update, context):
    if not authorized(update):
        return

    text = ""
    for category, items in ASSETS.items():
        text += f"\n{category} ({len(items)}):\n"
        text += ", ".join(items.keys()) + "\n"

    await update.message.reply_text(text)


async def assets(update, context):
    if not authorized(update):
        return

    keyboard = [
        [InlineKeyboardButton(category, callback_data=f"cat:{category}")]
        for category in ASSETS
    ]
    keyboard.append([
        InlineKeyboardButton("🤖 Auto select", callback_data="auto")
    ])

    await update.message.reply_text(
        "Choose a category:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def button(update, context):
    global auto_select, selected
    query = update.callback_query
    await query.answer()

    if not authorized(update):
        await query.edit_message_text("Not authorized.")
        return

    if query.data == "auto":
        auto_select = True
        selected = []
        await query.edit_message_text(
            f"Auto selection enabled. First {AUTO_LIMIT} assets will be scanned.\n"
            "Use /on to start."
        )
        return

    if query.data.startswith("cat:"):
        category = query.data[4:]
        keyboard = []

        for name in ASSETS[category]:
            keyboard.append([
                InlineKeyboardButton(
                    name, callback_data=f"pick:{category}|{name}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton("⬅️ Categories", callback_data="back")
        ])

        await query.edit_message_text(
            f"Choose an asset from {category}:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if query.data == "back":
        keyboard = [
            [InlineKeyboardButton(cat, callback_data=f"cat:{cat}")]
            for cat in ASSETS
        ]
        keyboard.append([
            InlineKeyboardButton("🤖 Auto select", callback_data="auto")
        ])
        await query.edit_message_text(
            "Choose category:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if query.data.startswith("pick:"):
        category, name = query.data[5:].split("|", 1)
        item = {
            "category": category,
            "name": name,
            "symbol": ASSETS[category][name],
        }

        if all(x["symbol"] != item["symbol"] for x in selected):
            selected.append(item)

        auto_select = False
        await query.edit_message_text(
            f"Added: {name} ({item['symbol']})\n"
            f"Selected assets: {len(selected)}\n"
            "Use /assets to add more, then /on."
        )


async def select(update, context):
    global auto_select

    if not authorized(update):
        return

    if not context.args:
        await update.message.reply_text("Example: /select EUR/USD")
        return

    item = find_asset(" ".join(context.args))
    if not item:
        await update.message.reply_text(
            "Asset not found. Use /categories to see the list."
        )
        return

    if all(x["symbol"] != item["symbol"] for x in selected):
        selected.append(item)

    auto_select = False
    await update.message.reply_text(
        f"Added {item['name']}. Selected: {len(selected)}"
    )


async def clear(update, context):
    global selected, auto_select

    if not authorized(update):
        return

    selected = []
    auto_select = False
    await update.message.reply_text("Selection cleared.")


async def auto(update, context):
    global selected, auto_select

    if not authorized(update):
        return

    selected = []
    auto_select = True
    await update.message.reply_text(
        f"Auto selection enabled: first {AUTO_LIMIT} listed assets.\n"
        "Use /on to start."
    )


async def on(update, context):
    global enabled

    if not authorized(update):
        return

    if not selected and not auto_select:
        await update.message.reply_text(
            "Choose assets using /assets or enable /auto first."
        )
        return

    enabled = True
    await update.message.reply_text("Scanning enabled.")


async def off(update, context):
    global enabled

    if not authorized(update):
        return

    enabled = False
    await update.message.reply_text("Scanning stopped.")


async def signal(update, context):
    if not authorized(update):
        return

    if not context.args:
        await update.message.reply_text("Example: /signal EURUSD")
        return

    item = find_asset(" ".join(context.args))
    if not item:
        await update.message.reply_text("Asset not found.")
        return

    await update.message.reply_text(
        await make_signal(context.application, item, manual=True)
    )


async def status(update, context):
    if not authorized(update):
        return

    chosen = (
        ", ".join(x["name"] for x in selected)
        or ("Auto selection" if auto_select else "None")
    )

    await update.message.reply_text(
        f"Scanning: {enabled}\n"
        f"Quotex connected: {connected}\n"
        f"Channel configured: {bool(CHANNEL_ID)}\n"
        f"Assets: {chosen}\n"
        f"Last error: {last_error or 'None'}"
    )


async def stats(update, context):
    if not authorized(update):
        return

    await update.message.reply_text(
        f"Signals posted: {stats['signals']}\n"
        "Result tracking is not yet persisted across restarts."
    )


async def scan_loop(app):
    while True:
        if enabled:
            current = (
                all_assets()[:AUTO_LIMIT]
                if auto_select else selected[:]
            )

            for item in current:
                try:
                    await make_signal(app, item)
                except Exception:
                    log.exception("Scan failed")
                await asyncio.sleep(1)

        await asyncio.sleep(15)


async def result_loop(app):
    while True:
        now = time.time()

        for pending in pending_results[:]:
            if now - pending["sent_at"] < 65:
                continue

            item = pending["item"]
            candles = await get_candles(item["symbol"])
            target_time = pending["signal_time"] + 60

            target = next(
                (c for c in candles if c["time"] >= target_time), None
            )

            if target is None:
                outcome = "UNKNOWN — expiry candle unavailable"
                stats["unknown"] += 1
            else:
                colour = candle_color(target)
                win = (
                    pending["direction"] == "CALL" and colour == "GREEN"
                ) or (
                    pending["direction"] == "PUT" and colour == "RED"
                )

                outcome = (
                    "WIN (candle-direction estimate)"
                    if win else "LOSS (candle-direction estimate)"
                )
                stats["wins" if win else "losses"] += 1

            await post_channel(
                app,
                f"📊 STM RESULT\nAsset: {item['name']}\n"
                f"Signal: {pending['direction']}\n"
                f"Result: {outcome}\n"
                "Candle estimate only; not verified broker settlement.",
            )
            pending_results.remove(pending)

        await asyncio.sleep(5)


async def post_init(app):
    app.create_task(scan_loop(app))
    app.create_task(result_loop(app))


def main():
    if not TG_TOKEN:
        raise SystemExit("Set TG_BOT_TOKEN before starting.")

    app = (
        Application.builder()
        .token(TG_TOKEN)
        .post_init(post_init)
        .build()
    )

    commands = {
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
        "stats": stats,
    }

    for name, handler in commands.items():
        app.add_handler(CommandHandler(name, handler))

    app.add_handler(CallbackQueryHandler(button))
    app.run_polling()


if __name__ == "__main__":
    main()
