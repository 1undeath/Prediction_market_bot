# ==========================================
# ⚙️ PREDICTION MARKET BOT CONFIGURATION
# ==========================================

import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# 🔑 API TOKENS
# ==========================================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ==========================================
# 📢 DISCORD IDS
# ==========================================
ADMIN_CHANNEL_ID = 1457077196794626307       # Admin channel for proposals
MARKETS_CHANNEL_ID = 1457480054735896676     # Public markets channel
BYPASS_ROLE_ID = 1457060879958020231         # VIP role (bypasses costs/cooldowns)

# ==========================================
# 💰 ECONOMY SETTINGS (LMSR)
# ==========================================
LMSR_B = 300.0              # Liquidity parameter (max platform loss ≈ B × ln(2) ≈ 208 pts)
TRADING_FEE = 0.05          # 5% fee on buy and sell
PROPOSAL_COST = 100.0       # Cost to propose a market
STARTING_BALANCE = 1000.0   # Default user balance
DAILY_REWARD = 100.0        # Daily claim reward

# ==========================================
# ⏰ TIME SETTINGS
# ==========================================
class TimeConfig:
    """Time configuration that changes based on debug mode"""
    def __init__(self):
        self.debug_mode = False
        self._update_settings()
    
    def _update_settings(self):
        if self.debug_mode:
            # Debug mode - minutes
            self.time_unit = "minutes"
            self.min_duration = 1
            self.max_duration = 120
            self.proposal_cooldown_hours = 0.1      # ~6 seconds
            self.daily_cooldown_hours = 0.016       # ~1 minute
        else:
            # Production mode - hours
            self.time_unit = "hours"
            self.min_duration = 6
            self.max_duration = 168                 # 1 week
            self.proposal_cooldown_hours = 4
            self.daily_cooldown_hours = 24
    
    def enable_debug(self):
        """Enable debug mode (time in minutes)"""
        self.debug_mode = True
        self._update_settings()
    
    def disable_debug(self):
        """Disable debug mode (time in hours)"""
        self.debug_mode = False
        self._update_settings()
    
    def get_timedelta_for_duration(self, value):
        """Returns timedelta for market duration"""
        import datetime
        if self.debug_mode:
            return datetime.timedelta(minutes=value)
        else:
            return datetime.timedelta(hours=value)

# Global time config instance
time_config = TimeConfig()

# ==========================================
# 🤖 AUTO-RESOLUTION SETTINGS
# ==========================================
AUTO_RESOLVE_ENABLED = True         # Enable/disable auto-resolution
AUTO_RESOLVE_THRESHOLD = 70         # Auto-resolve if YES >= 70% or NO >= 70%
MANUAL_RESOLVE_RANGE = (30, 70)     # 30-70% requires manual resolution
APPEAL_WINDOW_HOURS = 24            # Hours to appeal auto-resolution
CHECK_EXPIRED_INTERVAL_MINUTES = 1 # How often to check for expired markets

# ==========================================
# 🎨 VISUAL SETTINGS
# ==========================================
PROGRESS_BAR_LENGTH = 12            # Length of progress bar in embeds
CHART_WIDTH = 10                    # Chart width in inches
CHART_HEIGHT = 5                    # Chart height in inches
CHART_DPI = 120                     # Chart resolution

# Color thresholds for market embeds
COLOR_GREEN_THRESHOLD = 65          # YES >= 65% = green
COLOR_RED_THRESHOLD = 35            # YES <= 35% = red
# Between thresholds = gold

# ==========================================
# 🔧 SYSTEM SETTINGS
# ==========================================
DB_NAME = 'prediction_market.db'
DB_TIMEOUT = 10.0                   # Database timeout in seconds
HEARTBEAT_INTERVAL_SECONDS = 60     # Heartbeat file update interval