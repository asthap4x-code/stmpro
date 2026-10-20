# STM Pro Bot — Telegram signal assistant

A starting project for a personal Telegram bot that analyses 1-minute Quotex candle history and can post signal analyses and approximate candle-direction checks to a Telegram channel.

## Important limitations
- This is a signal/analysis tool only. It does **not** place trades.
- No strategy guarantees high accuracy. Confidence is a heuristic score, not a calibrated probability.
- The result monitor compares a candle's open/close direction; it does not verify Quotex's actual settlement, payout, entry price, or ties. Check results manually before trusting any statistics.
- `pyquotex` is an unofficial integration and may stop working or fail login/websocket checks. Use only where permitted by the platform's terms and your local law.
- MTG is disabled by default. Martingale can rapidly magnify losses; this project does not automate stake increases.
- Keep secrets private. Do not commit `.env`, Telegram bot tokens, or Quotex credentials to a public GitHub repository.

## Files
- `main.py` — Telegram commands, Quotex connection, candle analysis, signal posts and result checks.
- `requirements.txt` — dependencies.
- `.env.example` — configuration template (safe placeholders only).
- `.gitignore` — ignores local secrets.

## 1) Create a Telegram bot
1. Open Telegram and message `@BotFather`.
2. Use `/newbot`, follow the prompts, and copy the token.
3. Add the bot as an **administrator** to your channel with permission to post messages.
4. Set `TG_CHANNEL_ID` to `@channelusername` (public channel) or the channel's numeric `-100...` ID.

## 2) Install on Termux
Use Python 3.11 or 3.12 if possible; newer Python versions may have dependency build issues.

```bash
pkg update
pkg install python git
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If `pyquotex` fails to install on your Python/Termux version, use the same working environment in which your earlier candle test succeeded. Do not assume the bot is connected until `/status` confirms it.

## 3) Set environment variables locally
In Termux, run these commands with your own values (do not share screenshots containing tokens/passwords):

```bash
export TG_BOT_TOKEN='YOUR_TELEGRAM_BOT_TOKEN'
export TG_CHANNEL_ID='@your_channel_username'
export QUOTEX_EMAIL='YOUR_QUOTEX_EMAIL'
export QUOTEX_PASSWORD='YOUR_QUOTEX_PASSWORD'
export STM_ASSETS='EURUSD_otc,GBPUSD_otc,USDJPY_otc'
export STM_ALLOWED_USER_IDS='YOUR_TELEGRAM_NUMERIC_USER_ID'
python main.py
```

For a private personal bot, set `STM_ALLOWED_USER_IDS` to your numeric Telegram user ID. To allow multiple controllers, use comma-separated IDs. Leaving it blank allows anyone who finds the bot to run its commands, so do not leave it blank for a private bot.

## 4) Commands
- `/start` — instructions
- `/on` — start scanning configured assets
- `/off` — stop scanning
- `/signal EURUSD_otc` — analyse one asset immediately
- `/status` — connection and configuration status
- `/stats` — rough candle-direction check counts

## 5) Upload to GitHub
Upload `main.py`, `requirements.txt`, `.gitignore`, `.env.example`, and `README.md`.
**Never upload your real `.env` file or real credentials.** GitHub Pages cannot run this Python bot; GitHub stores the code. Run the script in Termux or on a Python-capable server. The phone must stay awake/connected for a Termux-hosted bot to keep running.

## Strategy currently included
- Short-term moving-average trend comparison
- Simple higher-high/higher-low and lower-low/lower-high proxy
- Recent support/resistance zones
- Bullish/bearish engulfing candle patterns
- Wick-rejection heuristic
- Momentum approximation
- Minimum-confidence filter and WAIT/no-trade state

This is a baseline for testing and later adding properly validated features such as ATR volatility filters, session filters, multi-timeframe context, and out-of-sample backtesting. Do not use untested signals with real money.
