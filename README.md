# 🔮 Discord Prediction Market Bot

A fully functional bot for hosting a "Prediction Market" on Discord.
Users trade **YES/NO** shares on the outcome of future events. Share prices are dynamic (based on the CPMM algorithm), mimicking real-world exchanges.

## ✨ Features
*   **Economy:** Share trading, portfolio management, daily rewards.
*   **Interface:** Controlled via modern Buttons and Modal Windows (no complex text commands required).
*   **Visuals:** Generates neon price history charts, progress bars, and smart color indicators.
*   **Notifications:** New market proposals are mirrored to Telegram for admins.
*   **Moderation:** Admin panel for approving proposals and resolving markets (payouts).

## 🛠 Requirements
*   Python 3.10+
*   Libraries listed in `requirements.txt`

## 🚀 Installation & Setup

1.  **Download the project** and open the folder in your terminal/command prompt.

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure Environment Variables:**
    Create a file named `.env` in the same folder and add your tokens:
    ```env
    DISCORD_BOT_TOKEN=your_discord_bot_token
    TELEGRAM_BOT_TOKEN=your_telegram_bot_token
    TELEGRAM_CHAT_ID=your_telegram_user_id
    ```

4.  **Configure Channel IDs:**
    Open `gemini3.py` (lines 20-22) and replace the IDs with your server's IDs:
    *   `ADMIN_CHANNEL_ID` — Hidden channel for incoming proposals.
    *   `MARKETS_CHANNEL_ID` — Public channel where markets are posted.

5.  **Run the bot:**
    ```bash
    python gemini3.py
    ```

## 🎮 First Steps

Immediately after starting the bot, type the following command in your main channel (Admins only):
> **/setup_dashboard**

This will create a permanent, interactive menu with buttons where users can propose markets and check their portfolios.

## 📜 Commands List

### 👤 For Users
*   **Dashboard Buttons:** Propose Market, Portfolio, Sell Shares.
*   `/markets` — List active markets with "Buy" buttons.
*   `/daily` — Claim 100 free points (once every 24h).
*   `/balance` — Check your current balance.
*   `/top` — Leaderboard of the top 10 richest players.

### 👮‍♂️ For Admins
*   `/resolve [id] [winner]` — Close a market and payout winners (winner: YES or NO).
*   **Approval System:** "Approve/Reject" buttons appear in the Admin Channel when a user proposes a new market.

## 🛡️ Extra (Watchdog)
The project includes a `tg_bot.py` script. Run it in parallel with the main bot to receive Telegram notifications if the main bot crashes or goes offline.
