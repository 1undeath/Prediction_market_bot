import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import datetime
import math
import io
import os
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from typing import Optional
import time
from discord.ext import tasks
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================

# 1. TOKENS
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 2. CHANNELS AND ROLES
ADMIN_CHANNEL_ID = 1457077196794626307       
MARKETS_CHANNEL_ID = 1457035943729954964     
BYPASS_ROLE_ID = 1457060879958020231         

# 3. ECONOMY
INITIAL_LIQUIDITY = 500.0
TRADING_FEE = 0.05
PROPOSAL_COST = 100.0
PROPOSAL_COOLDOWN = 4
MIN_DURATION_HOURS = 6
MAX_DURATION_HOURS = 168

# ==========================================
# 🛠 DATABASE & UTILS
# ==========================================

intents = discord.Intents.default()
intents.message_content = True

def adapt_datetime(ts): return ts.isoformat()
def convert_datetime(ts): return datetime.datetime.fromisoformat(ts.decode())
sqlite3.register_adapter(datetime.datetime, adapt_datetime)
sqlite3.register_converter("TIMESTAMP", convert_datetime)

def get_db_connection():
    conn = sqlite3.connect('prediction_market.db', timeout=10.0, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 1000.0, last_claim TIMESTAMP, last_proposal TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS markets
                 (market_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  question TEXT,
                  creator_id INTEGER,
                  created_at TIMESTAMP,
                  closes_at TIMESTAMP,
                  status TEXT DEFAULT 'pending', 
                  result TEXT,
                  pool_yes REAL DEFAULT 0,
                  pool_no REAL DEFAULT 0,
                  fee_collected REAL DEFAULT 0,
                  message_id INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS positions 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, market_id INTEGER, user_id INTEGER, position TEXT, shares REAL, average_price REAL, FOREIGN KEY (market_id) REFERENCES markets(market_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS price_history 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, market_id INTEGER, prob_yes REAL, timestamp TIMESTAMP)''')
    conn.commit()
    conn.close()

# --- VISUALS ---
def create_progress_bar(percent_yes: float, length: int = 12) -> str:
    filled = int((percent_yes / 100) * length)
    empty = length - filled
    return "🟩" * filled + "⬛" * empty

def get_market_color(prob_yes: float):
    if prob_yes >= 65: return 0x00FF00 # Green
    if prob_yes <= 35: return 0xFF0000 # Red
    return 0xFFD700 # Gold

async def get_market_embed(market_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT question, pool_yes, pool_no, closes_at FROM markets WHERE market_id=?', (market_id,))
    res = c.fetchone()
    conn.close()
    
    if not res: return None
    q, py, pn, closes = res
    prob = get_prob(py, pn) * 100
    bar = create_progress_bar(prob)
    color = get_market_color(prob)
    
    embed = discord.Embed(color=color)
    embed.add_field(
        name=f"#{market_id}: {q}", 
        value=f"{bar}\nYES: {int(prob)}% | NO: {int(100-prob)}%\nCloses: <t:{int(closes.timestamp())}:R>", 
        inline=False
    )
    embed.set_footer(text=f"ID: {market_id} • Click buttons below")
    return embed

def generate_chart(market_id, question):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT prob_yes, timestamp FROM price_history WHERE market_id = ? ORDER BY timestamp', (market_id,))
    data = c.fetchall()
    conn.close()
    if not data: return None
    probs_yes = [row[0] * 100 for row in data]
    dates = [row[1] for row in data]
    
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor('#2f3136')
    ax.set_facecolor('#2f3136')
    
    start_p, end_p = probs_yes[0], probs_yes[-1]
    line_color = '#00ff41' if end_p >= start_p else '#ff2975'
    
    ax.plot(dates, probs_yes, color=line_color, linewidth=3, alpha=0.9)
    ax.fill_between(dates, probs_yes, 0, color=line_color, alpha=0.15)
    ax.grid(True, color='white', alpha=0.05, linestyle='--')
    for spine in ax.spines.values(): spine.set_visible(False)
    ax.set_ylim(-5, 105)
    ax.tick_params(colors='#b9bbbe', labelsize=10)
    
    title = f"{question[:45]}...\nCurrent YES: {int(end_p)}%"
    ax.set_title(title, fontsize=16, color='white', fontweight='bold', pad=20, loc='left')
    if len(dates) > 1 and (dates[-1] - dates[0]).days < 1: ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    else: ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    fig.autofmt_xdate()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=120, facecolor='#2f3136')
    buf.seek(0)
    plt.close()
    return buf

# --- MATH ---
def get_prob(pool_yes, pool_no): return pool_no / (pool_yes + pool_no)
def calculate_shares_out(pool_in, pool_out, amount_in):
    amount_after_fee = amount_in * (1 - TRADING_FEE)
    shares_out = (pool_out * amount_after_fee) / (pool_in + amount_after_fee)
    return shares_out, amount_after_fee
def calculate_cash_out(pool_same, pool_other, shares_in):
    payout_raw = (pool_other * shares_in) / (pool_same + shares_in)
    fee = payout_raw * TRADING_FEE
    return payout_raw - fee, fee, payout_raw

# --- UTILS ---
def get_balance(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    res = c.fetchone()
    conn.close()
    if not res:
        update_balance(user_id, 0)
        return 1000.0
    return res[0]

def update_balance(user_id, amount):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def escape_markdown(text: str) -> str:
    if not text: return ""
    special_chars = r"_*[]()~`>#+-=|{}.!"
    for char in special_chars: text = text.replace(char, "\\" + char)
    return text

def send_proposal_to_tg(question, author_name, price, discord_link):
    safe_q = escape_markdown(question)
    safe_author = escape_markdown(author_name)
    text = (f"📩 *New Market Proposal!*\n\n❓ *Question:* {safe_q}\n👤 *Author:* {safe_author}\n💰 *Cost:* {price} pts\n\n🔗 [Go to Discord]({discord_link})")
    try: requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "MarkdownV2", "disable_web_page_preview": True})
    except Exception as e: print(f"❌ TG Error: {e}")

# ==========================================
# 🖥️ UI (VIEWS & MODALS)
# ==========================================

async def show_portfolio(interaction: discord.Interaction):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''SELECT m.market_id, p.shares, p.position, p.average_price, m.question, m.pool_yes, m.pool_no 
                 FROM positions p JOIN markets m ON p.market_id = m.market_id 
                 WHERE p.user_id = ? AND m.status = 'active' ORDER BY p.id DESC''', (interaction.user.id,))
    rows = c.fetchall()
    bal = get_balance(interaction.user.id)
    conn.close()
    embed = discord.Embed(title="💼 Your Portfolio", description=f"Balance: **{int(bal)}** pts", color=discord.Color.blurple())
    if not rows: 
        embed.add_field(name="Empty", value="You have no active bets.")
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    options = []
    for mid, shares, pos, avg, q, py, pn in rows:
        curr_prob = get_prob(py, pn)
        price = curr_prob if pos == 'yes' else (1-curr_prob)
        pnl = (shares * price) - (shares * avg)
        label = f"#{mid} {q[:15]}..."
        desc = f"{pos.upper()} {int(shares)} shares | PnL: {int(pnl)}"
        options.append(discord.SelectOption(label=label, description=desc, value=f"{mid}:{pos}:{shares}"))
        embed.add_field(name=label, value=desc, inline=False)
    await interaction.response.send_message(embed=embed, view=PortfolioView(options), ephemeral=True)

class BuyModal(discord.ui.Modal):
    def __init__(self, market_id, position):
        super().__init__(title=f"Bet on {position.upper()}")
        self.market_id = market_id
        self.position = position
        self.amount_input = discord.ui.TextInput(label="Amount (pts)", placeholder="e.g. 100", min_length=1, max_length=6)
        self.add_item(self.amount_input)
    async def on_submit(self, interaction: discord.Interaction):
        try: amount = int(self.amount_input.value)
        except: return await interaction.response.send_message("❌ Please enter a number!", ephemeral=True)
        if amount <= 0: return await interaction.response.send_message("❌ Amount must be > 0", ephemeral=True)
        user_id = interaction.user.id
        if get_balance(user_id) < amount: return await interaction.response.send_message("❌ Insufficient funds", ephemeral=True)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT status, pool_yes, pool_no FROM markets WHERE market_id = ?', (self.market_id,))
        m = c.fetchone()
        if not m or m[0] != 'active': 
            conn.close()
            return await interaction.response.send_message("❌ Market closed", ephemeral=True)
        py, pn = m[1], m[2]
        update_balance(user_id, -amount)
        fee = amount * TRADING_FEE
        invest = amount - fee
        if self.position == "yes":
            shares, _ = calculate_shares_out(pn, py, amount)
            new_py, new_pn = py - shares, pn + invest
        else:
            shares, _ = calculate_shares_out(py, pn, amount)
            new_py, new_pn = py + invest, pn - shares
        c.execute('UPDATE markets SET pool_yes=?, pool_no=?, fee_collected=fee_collected+? WHERE market_id=?', (new_py, new_pn, fee, self.market_id))
        avg_price = amount / shares
        c.execute('SELECT id, shares, average_price FROM positions WHERE user_id=? AND market_id=? AND position=?', (user_id, self.market_id, self.position))
        ex = c.fetchone()
        if ex:
            total = ex[1] + shares
            new_avg = ((ex[1]*ex[2]) + (shares*avg_price)) / total
            c.execute('UPDATE positions SET shares=?, average_price=? WHERE id=?', (total, new_avg, ex[0]))
        else:
            c.execute('INSERT INTO positions (market_id, user_id, position, shares, average_price) VALUES (?,?,?,?,?)', (self.market_id, user_id, self.position, shares, avg_price))
        new_prob = get_prob(new_py, new_pn)
        c.execute('INSERT INTO price_history (market_id, prob_yes, timestamp) VALUES (?, ?, ?)', (self.market_id, new_prob, datetime.datetime.now()))
        conn.commit()
        conn.close()
        
        embed = discord.Embed(title="✅ Bet Accepted", color=discord.Color.green())
        embed.add_field(name="Amount", value=f"{amount} pts on {self.position.upper()}")
        embed.add_field(name="Shares Bought", value=f"{shares:.1f}")
        embed.add_field(name="New YES Chance", value=f"{int(new_prob*100)}%")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        try:
            new_embed = await get_market_embed(self.market_id)
            if new_embed: await interaction.message.edit(embed=new_embed)
        except: pass

class SellModal(discord.ui.Modal):
    def __init__(self, market_id, position, max_shares):
        super().__init__(title=f"Sell {position.upper()}")
        self.market_id = market_id
        self.position = position
        self.max_shares = max_shares
        self.amount_input = discord.ui.TextInput(label=f"Shares (Max: {int(max_shares)})", placeholder="0 to sell ALL", default="0")
        self.add_item(self.amount_input)
    async def on_submit(self, interaction: discord.Interaction):
        try: amount_shares = float(self.amount_input.value)
        except: return await interaction.response.send_message("❌ Invalid number", ephemeral=True)
        user_id = interaction.user.id
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT status, pool_yes, pool_no, message_id FROM markets WHERE market_id = ?', (self.market_id,))
        market = c.fetchone()
        if not market or market[0] != 'active':
            conn.close()
            return await interaction.response.send_message("❌ Market closed.", ephemeral=True)
        pool_yes, pool_no, msg_id = market[1], market[2], market[3]
        shares_to_sell = amount_shares if amount_shares > 0 else self.max_shares
        if shares_to_sell > self.max_shares:
            conn.close()
            return await interaction.response.send_message("❌ You don't have enough shares.", ephemeral=True)
        if self.position == "yes":
            cash_out, fee, raw_out = calculate_cash_out(pool_yes, pool_no, shares_to_sell)
            new_yes, new_no = pool_yes + shares_to_sell, pool_no - raw_out
        else:
            cash_out, fee, raw_out = calculate_cash_out(pool_no, pool_yes, shares_to_sell)
            new_yes, new_no = pool_yes - raw_out, pool_no + shares_to_sell
        update_balance(user_id, cash_out)
        c.execute('SELECT id FROM positions WHERE user_id=? AND market_id=? AND position=?', (user_id, self.market_id, self.position))
        pos_id = c.fetchone()[0]
        remaining = self.max_shares - shares_to_sell
        if remaining < 0.01: c.execute('DELETE FROM positions WHERE id=?', (pos_id,))
        else: c.execute('UPDATE positions SET shares=? WHERE id=?', (remaining, pos_id))
        c.execute('UPDATE markets SET pool_yes=?, pool_no=?, fee_collected=fee_collected+? WHERE market_id=?', (new_yes, new_no, fee, self.market_id))
        new_prob = get_prob(new_yes, new_no)
        c.execute('INSERT INTO price_history (market_id, prob_yes, timestamp) VALUES (?, ?, ?)', (self.market_id, new_prob, datetime.datetime.now()))
        conn.commit()
        conn.close()
        await interaction.response.send_message(f"💸 Sold {shares_to_sell:.1f} {self.position.upper()}. Received **{int(cash_out)} pts**.", ephemeral=True)
        
        try:
            if msg_id and msg_id != 0:
                channel = interaction.client.get_channel(MARKETS_CHANNEL_ID)
                if channel:
                    msg_obj = await channel.fetch_message(msg_id)
                    new_embed = await get_market_embed(self.market_id)
                    await msg_obj.edit(embed=new_embed)
        except Exception as e: print(f"Update error: {e}")

class ProposeModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Create New Market")
        self.question = discord.ui.TextInput(label="Question", placeholder="Will Bitcoin hit $100k?", min_length=5, max_length=100)
        self.hours = discord.ui.TextInput(label="Duration (hours)", placeholder="48", default="48", min_length=1, max_length=3)
        self.add_item(self.question)
        self.add_item(self.hours)
    async def on_submit(self, interaction: discord.Interaction):
        try: hours_val = int(self.hours.value)
        except: return await interaction.response.send_message("❌ Duration must be a number!", ephemeral=True)
        question_val = self.question.value
        user_id = interaction.user.id
        now = datetime.datetime.now()
        is_vip = False
        if interaction.guild:
            user_roles = [r.id for r in interaction.user.roles]
            if BYPASS_ROLE_ID in user_roles or interaction.user.guild_permissions.administrator: is_vip = True
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT balance, last_proposal FROM users WHERE user_id = ?', (user_id,))
        res = c.fetchone()
        balance = res[0] if res else 1000.0
        last_prop = res[1] if res else None
        if not is_vip:
            if hours_val < MIN_DURATION_HOURS or hours_val > MAX_DURATION_HOURS:
                conn.close()
                return await interaction.response.send_message(f"❌ Duration: {MIN_DURATION_HOURS}-{MAX_DURATION_HOURS} hours.", ephemeral=True)
            if last_prop and now - last_prop < datetime.timedelta(hours=PROPOSAL_COOLDOWN):
                conn.close()
                return await interaction.response.send_message("⏳ Cooldown!", ephemeral=True)
            if balance < PROPOSAL_COST:
                conn.close()
                return await interaction.response.send_message(f"❌ Cost: {PROPOSAL_COST} pts", ephemeral=True)
        cost = 0 if is_vip else PROPOSAL_COST
        try:
            if not is_vip: c.execute('UPDATE users SET balance = balance - ?, last_proposal = ? WHERE user_id = ?', (cost, now, user_id))
            closes = now + datetime.timedelta(hours=hours_val)
            c.execute("INSERT INTO markets (question, creator_id, created_at, closes_at, pool_yes, pool_no, status) VALUES (?,?,?,?,?,?,'pending')", (question_val, user_id, now, closes, INITIAL_LIQUIDITY, INITIAL_LIQUIDITY))
            mid = c.lastrowid
            c.execute('INSERT INTO price_history (market_id, prob_yes, timestamp) VALUES (?, ?, ?)', (mid, 0.5, now))
            conn.commit()
            channel = interaction.client.get_channel(ADMIN_CHANNEL_ID)
            embed = discord.Embed(title="📩 New Proposal", color=discord.Color.orange())
            embed.add_field(name="Question", value=question_val)
            embed.add_field(name="Author", value=interaction.user.mention)
            embed.set_footer(text=f"ID: {mid}")
            view = ApprovalView(market_id=mid, creator_id=user_id, question=question_val, amount=cost)
            msg = await channel.send(embed=embed, view=view)
            send_proposal_to_tg(question_val, interaction.user.name, cost, msg.jump_url)
            await interaction.response.send_message("✅ Proposal sent to moderators!", ephemeral=True)
        except Exception as e: await interaction.response.send_message(f"Error: {e}", ephemeral=True)
        finally: conn.close()

# 3. VIEWS (BUTTONS)
class SellSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="Select position to SELL...", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: discord.Interaction):
        data = self.values[0].split(":")
        mid, pos, shares = int(data[0]), data[1], float(data[2])
        await interaction.response.send_modal(SellModal(mid, pos, shares))

class PortfolioView(discord.ui.View):
    def __init__(self, options):
        super().__init__()
        self.add_item(SellSelect(options))

class MarketControls(discord.ui.View):
    def __init__(self, market_id):
        super().__init__(timeout=None)
        self.market_id = market_id
        self.buy_yes.custom_id = f"buy_yes:{market_id}"
        self.buy_no.custom_id = f"buy_no:{market_id}"
        self.sell_btn.custom_id = f"sell:{market_id}"
        self.chart_btn.custom_id = f"chart:{market_id}"
        
    @discord.ui.button(label="🟢 BUY YES", style=discord.ButtonStyle.success, row=0)
    async def buy_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BuyModal(self.market_id, "yes"))
    @discord.ui.button(label="🔴 BUY NO", style=discord.ButtonStyle.danger, row=0)
    async def buy_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BuyModal(self.market_id, "no"))
    
    @discord.ui.button(label="💰 Sell", style=discord.ButtonStyle.primary, row=1)
    async def sell_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT position, shares FROM positions WHERE user_id=? AND market_id=?', (user_id, self.market_id))
        positions = c.fetchall()
        conn.close()
        if not positions:
            return await interaction.response.send_message("❌ You have no shares in this market.", ephemeral=True)
        if len(positions) == 1:
            pos, shares = positions[0]
            if shares <= 0.01: return await interaction.response.send_message("❌ Not enough shares.", ephemeral=True)
            await interaction.response.send_modal(SellModal(self.market_id, pos, shares))
        else:
            options = []
            for pos, shares in positions:
                if shares > 0.01:
                    options.append(discord.SelectOption(label=f"Sell {pos.upper()}", description=f"Available: {int(shares)}", value=f"{self.market_id}:{pos}:{shares}"))
            await interaction.response.send_message("Select what to sell:", view=PortfolioView(options), ephemeral=True)

    @discord.ui.button(label="📉 Chart", style=discord.ButtonStyle.secondary, row=1)
    async def chart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT question FROM markets WHERE market_id=?', (self.market_id,))
        res = c.fetchone()
        conn.close()
        if not res: return await interaction.followup.send("Market not found")
        buf = generate_chart(self.market_id, res[0])
        if not buf: return await interaction.followup.send("No data")
        file = discord.File(buf, filename="chart.png")
        await interaction.followup.send(file=file, ephemeral=True)

class DashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="📝 Propose Market", style=discord.ButtonStyle.primary, custom_id="dash_propose")
    async def propose_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ProposeModal())
    @discord.ui.button(label="💼 Portfolio / Sell", style=discord.ButtonStyle.secondary, custom_id="dash_portfolio")
    async def portfolio_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await show_portfolio(interaction)

class ApprovalView(discord.ui.View):
    def __init__(self, market_id, creator_id, question, amount):
        super().__init__(timeout=None)
        self.market_id, self.creator_id, self.question, self.amount = market_id, creator_id, question, amount
    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT status, closes_at, pool_yes, pool_no FROM markets WHERE market_id=?', (self.market_id,))
        res = c.fetchone()
        if not res or res[0] != 'pending':
            conn.close()
            return await interaction.response.send_message("⚠️ Already processed.", ephemeral=True)
        closes_at = res[1]
        c.execute('UPDATE markets SET status="active" WHERE market_id=?', (self.market_id,))
        conn.commit()
        new_embed = await get_market_embed(self.market_id)
        public_channel = interaction.client.get_channel(MARKETS_CHANNEL_ID)
        if public_channel and new_embed:
            msg = await public_channel.send(embed=new_embed, view=MarketControls(self.market_id))
            c.execute('UPDATE markets SET message_id=? WHERE market_id=?', (msg.id, self.market_id))
            conn.commit()
        conn.close()
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"✅ **APPROVED** by {interaction.user.mention}", view=self)
        try:
            user = await interaction.client.fetch_user(self.creator_id)
            await user.send(f"🚀 Your market **«{self.question}»** was approved!")
        except: pass

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('UPDATE markets SET status="rejected" WHERE market_id=?', (self.market_id,))
        c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (self.amount, self.creator_id))
        conn.commit()
        conn.close()
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"❌ **REJECTED**", view=self)

# ==========================================
# 🚀 BOT SETUP
# ==========================================

class PredictionBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
    async def setup_hook(self):
        init_db()
        self.add_view(DashboardView())
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT market_id FROM markets WHERE status='active'")
        active_markets = c.fetchall()
        conn.close()
        print(f"🔄 Restoring buttons for {len(active_markets)} active markets...")
        for (mid,) in active_markets:
            self.add_view(MarketControls(mid))

bot = PredictionBot()

@tasks.loop(seconds=60)
async def heartbeat_task():
    if bot.is_closed(): return 
    with open("heartbeat.txt", "w") as f: f.write(str(time.time()))

@bot.event
async def on_ready():
    print(f'✅ Bot {bot.user} is online!')
    if not heartbeat_task.is_running():
        heartbeat_task.start()
    try: await bot.tree.sync()
    except Exception as e: print(f"❌ Sync Error: {e}")

# COMMANDS

@bot.tree.command(name="setup_dashboard", description="[ADMIN] Create dashboard")
async def setup_dashboard(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admins only", ephemeral=True)
    embed = discord.Embed(title="🔮 Prediction Market", description="Manage your bets and propose new events!", color=discord.Color.blurple())
    embed.add_field(name="How it works", value="1. Propose an event (costs points)\n2. Others bet (YES/NO)\n3. Win and climb the leaderboard!", inline=False)
    await interaction.channel.send(embed=embed, view=DashboardView())
    await interaction.response.send_message("Dashboard created!", ephemeral=True)

@bot.tree.command(name="top", description="Rich List")
async def top(interaction: discord.Interaction):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10')
    rows = c.fetchall()
    conn.close()
    if not rows: 
        embed = discord.Embed(title="📭 List is empty", description="Type **/daily** to get on the list!", color=discord.Color.red())
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    desc = ""
    for i, (uid, bal) in enumerate(rows, 1):
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
        desc += f"**{medal}** <@{uid}> — **{int(bal)} pts**\n"
    embed = discord.Embed(title="🏆 Hall of Fame", description=desc, color=discord.Color.purple())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="markets", description="List of active markets")
async def markets(interaction: discord.Interaction):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT market_id FROM markets WHERE status="active" ORDER BY created_at DESC LIMIT 5')
    rows = c.fetchall()
    conn.close()
    if not rows: return await interaction.response.send_message("No active markets.", ephemeral=True)
    await interaction.response.send_message("🔥 **Active Markets**", ephemeral=True)
    for (mid,) in rows:
        embed = await get_market_embed(mid)
        if embed: await interaction.followup.send(embed=embed, view=MarketControls(mid), ephemeral=True)

@bot.tree.command(name="portfolio", description="Portfolio")
async def portfolio_cmd(interaction: discord.Interaction):
    await show_portfolio(interaction)

@bot.tree.command(name="daily", description="Daily Reward")
async def daily(interaction: discord.Interaction):
    uid = interaction.user.id
    now = datetime.datetime.now()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT last_claim FROM users WHERE user_id = ?', (uid,))
    res = c.fetchone()
    if res and res[0] and now - res[0] < datetime.timedelta(hours=24):
        nxt = int((res[0] + datetime.timedelta(hours=24)).timestamp())
        conn.close()
        return await interaction.response.send_message(f"⏳ Wait <t:{nxt}:R>", ephemeral=True)
    update_balance(uid, 100)
    c.execute('UPDATE users SET last_claim = ? WHERE user_id = ?', (now, uid))
    conn.commit()
    conn.close()
    await interaction.response.send_message("💰 +100 points!", ephemeral=True)

@bot.tree.command(name="balance", description="Balance")
async def bal(interaction: discord.Interaction):
    await interaction.response.send_message(f"💳 Your Balance: **{int(get_balance(interaction.user.id))}** pts", ephemeral=True)

@bot.tree.command(name="resolve", description="[ADMIN] Resolve Market")
@app_commands.choices(winner=[app_commands.Choice(name="YES", value="yes"), app_commands.Choice(name="NO", value="no")])
async def resolve(interaction: discord.Interaction, market_id: int, winner: app_commands.Choice[str]):
    if not interaction.user.guild_permissions.administrator: return await interaction.response.send_message("Admins only", ephemeral=True)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT status FROM markets WHERE market_id=?', (market_id,))
    st = c.fetchone()
    if not st or st[0] != 'active': 
        conn.close()
        return await interaction.response.send_message("Cannot resolve this market.", ephemeral=True)
    win = winner.value
    c.execute('UPDATE markets SET status=?, result=? WHERE market_id=?', ('closed', win, market_id))
    c.execute('SELECT user_id, shares FROM positions WHERE market_id=? AND position=?', (market_id, win))
    winners = c.fetchall()
    total = 0
    for uid, shares in winners:
        pay = shares * 1.0
        update_balance(uid, pay)
        total += pay
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ Market #{market_id} resolved. Winner: {win.upper()}. Payout: {int(total)} pts.")

if __name__ == "__main__":
    if not TOKEN:
        print("❌ CRITICAL ERROR: TOKEN not found! Check .env file.")
    else:
        bot.run(TOKEN)