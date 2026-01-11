# 🔮 Discord Prediction Market Bot (LMSR v3)

A professional Prediction Market bot for Discord powered by the **LMSR (Logarithmic Market Scoring Rule)** algorithm.
Users buy and sell shares of future events. Unlike traditional betting, this uses an **Automated Market Maker (AMM)** system, ensuring guaranteed liquidity and dynamic pricing based on supply and demand.

## ✨ Key Features

### 🧠 Smart Economy (LMSR)
*   **Dynamic Pricing:** Share prices fluctuate automatically as users buy/sell.
*   **Trading:** Users can sell their position *before* the market closes to lock in profits or cut losses.
*   **Guaranteed Liquidity:** The bot acts as the house (Market Maker), so you can always buy or sell instantly.

### ⚙️ Automation & Safety
*   **Auto-Resolution:** Markets with a clear winner (>51% probability) are paid out automatically when the timer ends.
*   **Safety Net:** If a market is stuck/ignored by admins for >6 hours after closing, the bot **automatically cancels it and refunds 100% of funds** to all users.
*   **Anti-Snipe:** Betting is strictly disabled the moment the timer hits 0.

### 📊 Visuals & Interface
*   **Price Charts:** Generates beautiful neon-style graphs showing price history for every market.
*   **Modern UI:** Buttons, Dropdowns, and Modals. No complex commands needed for trading.
*   **Progress Bars:** Visual indicators of current YES/NO odds.

### 🔔 Integrations
*   **Telegram Mirror:** New proposals are instantly sent to a Telegram channel for admin review.

---

## 🛠 Requirements
*   Python 3.10+
*   **Libraries:** `discord.py`, `matplotlib`, `requests`

## 🚀 Installation & Setup

1.  **Download the project**
    Clone the repo or download the files.

2.  **Install dependencies**
    ```bash
    pip install discord.py matplotlib requests
    ```

3.  **Configuration (`config.py`)**
    Create a file named `config.py` in the same folder. This is where all settings live.
    *Example content for `config.py`:*
    ```python
    # Bot Tokens
    TOKEN = "YOUR_DISCORD_BOT_TOKEN"
    TG_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
    TG_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"

    # Discord Channel IDs
    ADMIN_CHANNEL_ID = 123456789012345678  # Hidden channel for approvals
    MARKETS_CHANNEL_ID = 123456789012345678 # Public channel for markets
    BYPASS_ROLE_ID = 123456789012345678     # Role ID that skips proposal costs

    # Economy Settings
    STARTING_BALANCE = 1000
    DAILY_REWARD = 100
    PROPOSAL_COST = 50
    TRADING_FEE = 0.02      # 2% fee
    LMSR_B = 1000           # Liquidity parameter

    # Database
    DB_NAME = "prediction_market.db"
    DB_TIMEOUT = 10
    HEARTBEAT_INTERVAL_SECONDS = 60

    # Auto-Resolve Settings
    AUTO_RESOLVE_ENABLED = True
    AUTO_RESOLVE_THRESHOLD = 75         # Winner declared if >75%
    CHECK_EXPIRED_INTERVAL_MINUTES = 1  # How often to check timers
    APPEAL_WINDOW_HOURS = 24
    MANUAL_RESOLVE_RANGE = 0.05

    # Visuals
    PROGRESS_BAR_LENGTH = 15
    CHART_WIDTH = 10
    CHART_HEIGHT = 6
    CHART_DPI = 100
    COLOR_GREEN_THRESHOLD = 60.0
    COLOR_RED_THRESHOLD = 40.0

    # Time Configuration class (Keep the one from the code)
    class TimeConfig:
        def __init__(self):
            self.debug_mode = False
            # ... (copy the rest from your code)
    time_config = TimeConfig()
    ```

4.  **Run the bot**
    ```bash
    python LMSR_v3.py
    ```

---

## 🎮 How to Use

### 1. Initialization (Admin Only)
Once the bot is online, type this in your main channel to create the menu:
> **/setup_dashboard**

### 2. For Users
*   **Daily Reward:** Use `/daily` or the Dashboard button to get points.
*   **Proposing:** Click "Propose Market". It costs points. Your proposal goes to the Admin Channel.
*   **Betting:** Click **Buy YES** or **Buy NO** on any active market.
*   **Selling:** Use `/portfolio` or the "Portfolio / Sell" button to view positions and cash out early.
*   **Charts:** Click the "📉 Chart" button on any market to see the price history.

### 3. For Admins
*   **Approve/Reject:** Buttons appear in `ADMIN_CHANNEL_ID` when a user suggests a market.
*   **Resolution:**
    *   If `AUTO_RESOLVE_ENABLED` is True, markets resolve themselves.
    *   If manual intervention is needed, use: `/resolve [market_id] [yes/no]`.
*   **Debug Mode:** Use `/debug` to switch between Minutes (Testing) and Hours (Production) for timers.
*   **Status:** Use `/status` and `/info` to check bot health and economy stats.

---

## 🤖 How LMSR Works (vs. Betting)
This bot treats outcomes like **stocks**.
*   **Buying:** You buy shares. If "YES" wins, each share is worth 1.0 point. If "YES" loses, it's worth 0.
*   **The Price:** The price (e.g., 0.60) represents the current probability (60%).
*   **Selling:** Unlike a bookie, you can sell your shares back to the bot at the current market price *before* the event ends.

---

## 📁 File Structure
*   `LMSR_v3.py` - The main bot logic.
*   `config.py` - Configuration and settings.
*   `prediction_market.db` - SQLite database (created automatically).
