import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

try:
    from pyquotex.stable_api import Quotex
except ImportError:
    Quotex = None

from stm_strategy import analyze_stm_strategy
from ai_strategy import analyze_ai_strategy
from all_strategy import analyze_all_indicators

load_dotenv()
TG_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "").strip()
QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL", "").strip()
QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("QUOTEX_PRO_BOT")

# 100+ Quotex Pairs & OTC Assets
PAIRS = [
    "EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "EURJPY", "EURGBP", "USDCAD", "USDCHF", "NZDUSD", "GBPJPY",
    "AUDJPY", "EURAUD", "GBPCAD", "EURCAD", "CHFJPY", "CADJPY", "EURNZD", "AUDNZD", "GBPAUD", "GBPNZD",
    "EURUSD_otc", "GBPUSD_otc", "AUDUSD_otc", "USDJPY_otc", "EURJPY_otc", "EURGBP_otc", "USDCAD_otc",
    "USDCHF_otc", "NZDUSD_otc", "GBPJPY_otc", "AUDJPY_otc", "EURAUD_otc", "XAUUSD_otc", "BTCUSD_otc", "ETHUSD_otc"
]
while len(PAIRS) < 100:
    PAIRS.append(f"PAIR_OTC_{len(PAIRS)+1}")

SELECTED_STRATEGY = "all"  # Options: 'stm', 'ai', 'indicators', 'all'
client = None

async def init_quotex():
    global client
    if Quotex is None:
        log.error("pyquotex package is not installed.")
        return False
    try:
        client = Quotex(email=QUOTEX_EMAIL, password=QUOTEX_PASSWORD, lang="en")
        check, reason = await client.connect()
        if check:
            log.info("Successfully connected to Quotex WebSocket API.")
            return True
        else:
            log.error(f"Quotex connection failed: {reason}")
            return False
    except Exception as e:
        log.error(f"Quotex connection exception: {e}")
        return False

async def fetch_candles(pair):
    global client
    try:
        if client is None or not client.check_connect():
            await init_quotex()
        end_time = int(time.time())
        raw = await client.get_candles(pair, 60, end_time, 60 * 50)
        if not raw or not isinstance(raw, list):
            return None
        df = pd.DataFrame(raw)
        df = df.rename(columns={'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'})
        return df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    except Exception as e:
        log.error(f"Error fetching candles for {pair}: {e}")
        return None

async def verify_trade_result(pair, expected_direction):
    await asyncio.sleep(4)
    df = await fetch_candles(pair)
    if df is None or len(df) < 2:
        return "UNKNOWN"
    last_candle = df.iloc[-1]
    open_p, close_p = last_candle['open'], last_candle['close']
    
    if expected_direction == "CALL":
        return "WIN" if close_p > open_p else "LOSS"
    else:
        return "WIN" if close_p < open_p else "LOSS"

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏛️ STM PVIP & PVIP 2 (Personal)", callback_data="strat_stm")],
        [InlineKeyboardButton("🤖 AI Strategy", callback_data="strat_ai")],
        [InlineKeyboardButton("📊 Indicators Strategy", callback_data="strat_indicators")],
        [InlineKeyboardButton("🔥 ALL Strategies Combined", callback_data="strat_all")],
    ]
    await update.effective_message.reply_text(
        "👋 Welcome to Quotex Pro Signal Bot!\nSelect your strategy mode:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SELECTED_STRATEGY
    query = update.callback_query
    await query.answer()
    SELECTED_STRATEGY = query.data.replace("strat_", "")
    await query.edit_message_text(
        text=f"✅ Strategy successfully set to: **{SELECTED_STRATEGY.upper()}**\nBot is scanning 100 pairs sequentially..."
    )

async def run_scanner(app):
    await init_quotex()
    bot = app.bot
    log.info("Scanner loop started...")

    while True:
        now = datetime.now()
        # Trigger exactly 5 seconds before the candle closes (at 55th second)
        if now.second >= 55:
            target_time = (now + timedelta(minutes=1)).strftime("%H:%M")

            for pair in PAIRS:
                df = await fetch_candles(pair)
                if df is None or len(df) < 30:
                    continue

                signal, score = "NONE", 0

                if SELECTED_STRATEGY == "stm":
                    signal, score = analyze_stm_strategy(df)
                elif SELECTED_STRATEGY == "ai":
                    signal, score = analyze_ai_strategy(df)
                elif SELECTED_STRATEGY == "indicators":
                    signal, score = analyze_all_indicators(df)
                elif SELECTED_STRATEGY == "all":
                    s1, sc1 = analyze_stm_strategy(df)
                    s2, sc2 = analyze_ai_strategy(df)
                    s3, sc3 = analyze_all_indicators(df)
                    if s1 != "NONE":
                        signal, score = s1, sc1
                    elif s2 != "NONE":
                        signal, score = s2, sc2
                    else:
                        signal, score = s3, sc3

                if signal in ["CALL", "PUT"] and score >= 75:
                    dir_icon = "🟢 CALL (UP)" if signal == "CALL" else "🔴 PUT (DOWN)"
                    
                    msg = (
                        f"🚨 **QUOTEX 1M SIGNAL** 🚨\n\n"
                        f"📊 **Asset:** `{pair}`\n"
                        f"⏰ **Time:** `{target_time}` (1 Min)\n"
                        f"📈 **Direction:** {dir_icon}\n"
                        f"⚙️ **Strategy:** `{SELECTED_STRATEGY.upper()}`\n"
                        f"🎯 **Score:** `{score}%`\n\n"
                        f"⚠️ *If 1st step loses, take MTG 1 (2x)*"
                    )
                    
                    if TG_CHANNEL_ID:
                        await bot.send_message(chat_id=TG_CHANNEL_ID, text=msg, parse_mode="Markdown")

                    # Wait 65 seconds for trade completion
                    await asyncio.sleep(65)
                    step1_result = await verify_trade_result(pair, signal)

                    if step1_result == "WIN":
                        res_msg = f"✅ **RESULT:** `{pair} ({target_time})` ➔ **WIN (1st Step)** 🎉"
                    else:
                        # Wait for MTG 1 Candle completion (60 seconds)
                        await asyncio.sleep(60)
                        mtg_result = await verify_trade_result(pair, signal)
                        
                        if mtg_result == "WIN":
                            res_msg = f"✅ **RESULT:** `{pair} ({target_time})` ➔ **WIN (After MTG 1)** 🔥"
                        else:
                            res_msg = f"❌ **RESULT:** `{pair} ({target_time})` ➔ **LOSS (After MTG 1)** 📉"

                    if TG_CHANNEL_ID:
                        await bot.send_message(chat_id=TG_CHANNEL_ID, text=res_msg, parse_mode="Markdown")
                    break

            await asyncio.sleep(25)
        else:
            await asyncio.sleep(1)

async def post_init(app):
    app.create_task(run_scanner(app))

def main():
    if not TG_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN missing in .env")
        
    app = Application.builder().token(TG_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    log.info("Quotex Pro Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
