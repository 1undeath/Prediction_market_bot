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
import time # Добавить в импорты
from discord.ext import tasks # Убедитесь, что это есть
import requests # Не забудьте импортировать

import os

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ... остальной код не меняется
# Добавьте проверку, что токены загрузились
if not TOKEN or not TG_BOT_TOKEN or not TG_CHAT_ID:
    print("❌ CRITICAL ERROR: Environment variables are not set!")
    exit()
def escape_markdown(text: str) -> str:
    """Экранирует специальные символы для старого Markdown Telegram."""
    escape_chars = r'_*[]()`' # Символы для экранирования
    return ''.join(['\\' + char if char in escape_chars else char for char in text])
def send_proposal_to_tg(question, author_name, price, discord_link):
    # Экранируем пользовательский ввод
    safe_question = escape_markdown(question)
    safe_author_name = escape_markdown(author_name)
    safe_discord_link = escape_markdown(discord_link) # На всякий случай экранируем и ссылку

    text = (
        f"📩 **Новая заявка на маркет!**\n\n"
        f"❓ **Вопрос:** {safe_question}\n"
        f"👤 **Автор:** {safe_author_name}\n"
        f"💰 **Цена:** {price} pts\n\n"
        f"🔗 [Перейти к кнопкам в Discord]({safe_discord_link})"
    )
    
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        # --- ИЗМЕНЕНИЯ ЗДЕСЬ ---
        print(f"🚀 Отправляю запрос в ТГ на ID: {TG_CHAT_ID}")
        response = requests.post(url, json=payload)
        
        # Печатаем результат прямо в консоль
        print(f"📬 Ответ Telegram: {response.status_code}") 
        print(f"📄 Текст ответа: {response.text}") 
        # -----------------------
        
    except Exception as e:
        print(f"❌ Ошибка соединения с ТГ: {e}")
# ==========================================
# 🛠 PYTHON 3.12+ FIX (SQLite Dates)
# ==========================================
def adapt_datetime(ts):
    return ts.isoformat()

def convert_datetime(ts):
    return datetime.datetime.fromisoformat(ts.decode())

sqlite3.register_adapter(datetime.datetime, adapt_datetime)
sqlite3.register_converter("TIMESTAMP", convert_datetime)

# ==========================================
# ⚙️ CONFIGURATION (FILL THIS!)
# ==========================================

# 1. Bot Token

# 2. Admin Channel ID (Hidden channel for approvals)
ADMIN_CHANNEL_ID = 1457077196794626307 

# 3. Public Market Channel ID (Where new markets are posted)
MARKETS_CHANNEL_ID = 1457035943729954964

# 4. VIP Role ID (Bypasses cooldowns and fees) - Set to 0 if not used
BYPASS_ROLE_ID = 1457060879958020231

# 5. Economy Settings
INITIAL_LIQUIDITY = 500.0  # Bot's liquidity
TRADING_FEE = 0.05         # 5% fee
PROPOSAL_COST = 100.0      # Cost to propose a market
PROPOSAL_COOLDOWN = 4      # Hours between proposals
MIN_DURATION_HOURS = 6     # Min duration
MAX_DURATION_HOURS = 168   # Max duration (7 days)

# ==========================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- DATABASE ---
def get_db_connection():
    conn = sqlite3.connect('prediction_market.db', timeout=10.0, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Users
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, 
                  balance REAL DEFAULT 1000.0,
                  last_claim TIMESTAMP,
                  last_proposal TIMESTAMP)''')
    
    # Migration for old DBs
    try:
        c.execute('ALTER TABLE users ADD COLUMN last_proposal TIMESTAMP')
    except sqlite3.OperationalError:
        pass 
    
    # Markets
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
                  fee_collected REAL DEFAULT 0)''')
    
    # Positions
    c.execute('''CREATE TABLE IF NOT EXISTS positions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  market_id INTEGER,
                  user_id INTEGER,
                  position TEXT,
                  shares REAL,
                  average_price REAL,
                  FOREIGN KEY (market_id) REFERENCES markets(market_id))''')

    # Price History
    c.execute('''CREATE TABLE IF NOT EXISTS price_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  market_id INTEGER,
                  prob_yes REAL,
                  timestamp TIMESTAMP)''')
    
    conn.commit()
    conn.close()

# --- MATH (CPMM) ---
def get_prob(pool_yes, pool_no):
    """Calculates current YES price"""
    return pool_no / (pool_yes + pool_no)

def calculate_shares_out(pool_in, pool_out, amount_in):
    """Constant Product Formula: x * y = k"""
    amount_after_fee = amount_in * (1 - TRADING_FEE)
    shares_out = (pool_out * amount_after_fee) / (pool_in + amount_after_fee)
    return shares_out, amount_after_fee

# --- UTILS ---
def get_balance(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    res = c.fetchone()
    if not res:
        c.execute('INSERT INTO users (user_id) VALUES (?)', (user_id,))
        conn.commit()
        bal = 1000.0
    else:
        bal = res[0]
    conn.close()
    return bal

def update_balance(user_id, amount):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

# --- CHARTS (English) ---
def generate_chart(market_id, question):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT prob_yes, timestamp FROM price_history WHERE market_id = ? ORDER BY timestamp', (market_id,))
    data = c.fetchall()
    conn.close()
    
    if not data: return None
        
    probs_yes = [row[0] * 100 for row in data]
    probs_no = [100 - p for p in probs_yes]
    dates = [row[1] for row in data]
    
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    COLOR_YES = '#00E272' # Green
    COLOR_NO = '#E03F3F'  # Red
    
    # Plot lines
    ax.plot(dates, probs_yes, color=COLOR_YES, linewidth=2, label="YES")
    ax.plot(dates, probs_no, color=COLOR_NO, linewidth=2, label="NO")
    
    # Fill areas
    py_arr = np.array(probs_yes)
    pn_arr = np.array(probs_no)
    ax.fill_between(dates, probs_yes, probs_no, where=(py_arr >= pn_arr), 
                    interpolate=True, color=COLOR_YES, alpha=0.1)
    ax.fill_between(dates, probs_yes, probs_no, where=(py_arr < pn_arr), 
                    interpolate=True, color=COLOR_NO, alpha=0.1)

    # Styling
    ax.grid(True, color='#404040', alpha=0.3, linestyle='--')
    for spine in ['top', 'right', 'left', 'bottom']: ax.spines[spine].set_visible(False)
    
    ax.set_ylim(-5, 105)
    
    # Title & Labels
    curr_yes = int(probs_yes[-1])
    curr_no = int(probs_no[-1])
    leader = "YES" if curr_yes >= curr_no else "NO"
    
    # English Title
    title_text = f"{question[:40]}...\nLeading: {leader} ({max(curr_yes, curr_no)}%)"
    
    ax.set_title(title_text, fontsize=14, color='white', fontweight='bold', pad=15, loc='left')
    ax.set_ylabel("Probability (%)", color='#AAAAAA') # English Label
    ax.tick_params(colors='#AAAAAA')

    # Date formatting
    if len(dates) > 1 and (dates[-1] - dates[0]).days < 1:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d')) # Dec 31 format
    fig.autofmt_xdate()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=100, facecolor='#2f3136')
    buf.seek(0)
    plt.close()
    return buf

# --- ADMIN VIEW (English Buttons) ---
class ApprovalView(discord.ui.View):
    def __init__(self, market_id, creator_id, question, amount):
        super().__init__(timeout=None)
        self.market_id = market_id
        self.creator_id = creator_id
        self.question = question
        self.amount = amount

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green, custom_id="approve_btn")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('SELECT status, closes_at FROM markets WHERE market_id=?', (self.market_id,))
        res = c.fetchone()
        
        if not res or res[0] != 'pending':
            conn.close()
            await interaction.response.send_message("⚠️ Proposal already processed.", ephemeral=True)
            return

        closes_at = res[1]
        c.execute('UPDATE markets SET status="active" WHERE market_id=?', (self.market_id,))
        conn.commit()
        conn.close()

        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"✅ **APPROVED** by {interaction.user.mention}", view=self)
        
        # --- ИСПРАВЛЕНИЕ ТУТ ---
        # Используем interaction.client.get_channel вместо interaction.guild.get_channel
        # Это позволяет искать канал на ЛЮБОМ сервере, где есть бот
        public_channel = interaction.client.get_channel(MARKETS_CHANNEL_ID)
        # ----------------------

        if public_channel:
            embed = discord.Embed(title="✨ New Market Open!", description=f"**{self.question}**", color=discord.Color.green())
            embed.add_field(name="ID", value=f"`{self.market_id}`", inline=True)
            embed.add_field(name="Author", value=f"<@{self.creator_id}>", inline=True)
            ts = int(closes_at.timestamp())
            embed.add_field(name="Closes", value=f"<t:{ts}:R>", inline=False)
            embed.set_footer(text=f"Bet now: /buy {self.market_id} [YES/NO] [amount]")
            try:
                await public_channel.send(embed=embed)
            except discord.errors.Forbidden:
                await interaction.followup.send("⚠️ Approved, but bot cannot post in Public Channel (Missing Permissions in that server).", ephemeral=True)
        else:
             # Если бот не нашел канал даже глобально
             await interaction.followup.send(f"⚠️ Approved, but bot cannot find channel {MARKETS_CHANNEL_ID}. Is the bot in that server?", ephemeral=True)

        try:
            user = await interaction.client.fetch_user(self.creator_id)
            await user.send(f"🚀 Your market **«{self.question}»** has been approved!")
        except: pass

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.red, custom_id="deny_btn")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('UPDATE markets SET status="rejected" WHERE market_id=?', (self.market_id,))
        # Refund
        c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (self.amount, self.creator_id))
        conn.commit()
        conn.close()

        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"❌ **REJECTED** (Funds refunded)", view=self)
        try:
            user = await interaction.client.fetch_user(self.creator_id)
            await user.send(f"😔 Your market **«{self.question}»** was rejected by moderators.")
        except: pass

# ==========================================
# 🎮 COMMANDS (English)
# ==========================================
@tasks.loop(seconds=60)
async def heartbeat_task():
    # Если бот потерял соединение с Discord, is_closed() будет True
    # Или latency будет None/Inf.
    # Мы пишем файл только если всё ок.
    if bot.is_closed():
        return 
        
    with open("heartbeat.txt", "w") as f:
        f.write(str(time.time()))
@bot.event
async def on_ready():
    init_db()
    print(f'✅ Bot {bot.user} is online!')
    print(f'📋 Admin Channel: {ADMIN_CHANNEL_ID}')
    print(f'📢 Public Channel: {MARKETS_CHANNEL_ID}')
    print("💓 Запускаю пульс...")
    heartbeat_task.start()
    try:
        await bot.tree.sync()
        print("🤖 Commands synced.")
    except Exception as e:
        print(f"❌ Sync Error: {e}")

# 1. PROPOSE
@bot.tree.command(name="propose", description="Propose a market (Cost: 100 pts, Cooldown: 4h)")
async def propose(interaction: discord.Interaction, question: str, hours: int = 48):
    user_id = interaction.user.id
    now = datetime.datetime.now()
    
    # VIP Check
    is_vip = False
    if interaction.guild:
        user_roles = [r.id for r in interaction.user.roles]
        if BYPASS_ROLE_ID in user_roles or interaction.user.guild_permissions.administrator:
            is_vip = True
    
    # Constraints
    if not is_vip:
        if hours < MIN_DURATION_HOURS:
            return await interaction.response.send_message(f"❌ Min duration: {MIN_DURATION_HOURS} hours!", ephemeral=True)
        if hours > MAX_DURATION_HOURS:
            return await interaction.response.send_message(f"❌ Max duration: {MAX_DURATION_HOURS} hours!", ephemeral=True)

        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT balance, last_proposal FROM users WHERE user_id = ?', (user_id,))
        res = c.fetchone()
        balance = res[0] if res else 1000.0
        last_prop = res[1] if res else None
        conn.close()

        if last_prop:
            if now - last_prop < datetime.timedelta(hours=PROPOSAL_COOLDOWN):
                next_ts = int((last_prop + datetime.timedelta(hours=PROPOSAL_COOLDOWN)).timestamp())
                return await interaction.response.send_message(f"⏳ Cooldown! Wait until <t:{next_ts}:R>", ephemeral=True)
        
        if balance < PROPOSAL_COST:
            return await interaction.response.send_message(f"❌ Insufficient funds! You need {PROPOSAL_COST} pts.", ephemeral=True)
    
    # Transaction
    conn = get_db_connection()
    c = conn.cursor()
    try:
        cost = 0 if is_vip else PROPOSAL_COST
        
        # 1. Charge
        c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
        if not is_vip:
            c.execute('UPDATE users SET balance = balance - ?, last_proposal = ? WHERE user_id = ?', (cost, now, user_id))
        
        # 2. Create
        closes = now + datetime.timedelta(hours=hours)
        c.execute('''INSERT INTO markets (question, creator_id, created_at, closes_at, pool_yes, pool_no, status)
                     VALUES (?, ?, ?, ?, ?, ?, 'pending')''',
                  (question, user_id, now, closes, INITIAL_LIQUIDITY, INITIAL_LIQUIDITY))
        mid = c.lastrowid
        c.execute('INSERT INTO price_history (market_id, prob_yes, timestamp) VALUES (?, ?, ?)', (mid, 0.5, now))
        conn.commit()

        # 3. Send to Admin
        channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if not channel: raise Exception("Admin channel missing")
        
        embed = discord.Embed(title="📩 New Proposal", color=discord.Color.orange())
        embed.add_field(name="Question", value=question, inline=False)
        role_status = "⭐ VIP" if is_vip else f"Standard ({cost} pts)"
        embed.add_field(name="Author", value=f"{interaction.user.mention}\n{role_status}")
        embed.set_footer(text=f"ID: {mid}")
        view = ApprovalView(market_id=mid, creator_id=user_id, question=question, amount=cost)
        
        msg = await channel.send(embed=embed, view=view)
        send_proposal_to_tg(question, interaction.user.name, cost, msg.jump_url)

        await interaction.response.send_message("✅ Proposal sent to moderators!", ephemeral=True)

    except discord.errors.Forbidden:
        if 'mid' in locals():
            c.execute('DELETE FROM markets WHERE market_id = ?', (mid,))
            c.execute('DELETE FROM price_history WHERE market_id = ?', (mid,))
        if not is_vip:
            c.execute('UPDATE users SET balance = balance + ?, last_proposal = ? WHERE user_id = ?', 
                      (PROPOSAL_COST, datetime.datetime(2000,1,1), user_id))
        conn.commit()
        await interaction.response.send_message("❌ **Permission Error!** Bot cannot send to admin channel. Funds refunded.", ephemeral=True)
    
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        if not is_vip:
             c.execute('UPDATE users SET balance = balance + ?, last_proposal = ? WHERE user_id = ?', 
                      (PROPOSAL_COST, datetime.datetime(2000,1,1), user_id))
        conn.commit()
        if not interaction.response.is_done():
            await interaction.response.send_message(f"❌ System Error. Funds refunded.", ephemeral=True)
    finally:
        conn.close()

# 2. MARKETS
@bot.tree.command(name="markets", description="List active markets")
async def markets(interaction: discord.Interaction):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT market_id, question, pool_yes, pool_no, closes_at FROM markets WHERE status="active" ORDER BY created_at DESC LIMIT 10')
    rows = c.fetchall()
    conn.close()
    
    if not rows: return await interaction.response.send_message("No active markets.", ephemeral=True)
    
    embed = discord.Embed(title="🔥 Active Markets", color=discord.Color.gold())
    for mid, q, py, pn, closes in rows:
        prob = get_prob(py, pn) * 100
        ts = int(closes.timestamp())
        embed.add_field(name=f"#{mid}: {q}", value=f"YES: **{int(prob)}%** | NO: **{int(100-prob)}%**\nCloses: <t:{ts}:R>", inline=False)
    await interaction.response.send_message(embed=embed)

# 3. BUY
@bot.tree.command(name="buy", description="Place a bet (Buy Shares)")
@app_commands.describe(market_id="Market ID", position="YES or NO", amount="Amount of points")
@app_commands.choices(position=[app_commands.Choice(name="YES", value="yes"), app_commands.Choice(name="NO", value="no")])
async def buy(interaction: discord.Interaction, market_id: int, position: app_commands.Choice[str], amount: int):
    if amount <= 0: return await interaction.response.send_message("Amount must be > 0", ephemeral=True)
    
    user_id = interaction.user.id
    bal = get_balance(user_id)
    if bal < amount: return await interaction.response.send_message(f"❌ Insufficient funds! ({int(bal)} pts)", ephemeral=True)
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT question, status, pool_yes, pool_no FROM markets WHERE market_id = ?', (market_id,))
    market = c.fetchone()
    
    if not market or market[1] != 'active':
        conn.close()
        return await interaction.response.send_message("❌ Market not found or closed", ephemeral=True)
    
    pool_yes, pool_no = market[2], market[3]
    pos = position.value
    update_balance(user_id, -amount)
    
    fee = amount * TRADING_FEE
    invest = amount - fee
    
    if pos == "yes":
        shares_out, _ = calculate_shares_out(pool_no, pool_yes, amount)
        new_yes, new_no = pool_yes - shares_out, pool_no + invest
    else:
        shares_out, _ = calculate_shares_out(pool_yes, pool_no, amount)
        new_yes, new_no = pool_yes + invest, pool_no - shares_out

    c.execute('UPDATE markets SET pool_yes=?, pool_no=?, fee_collected=fee_collected+? WHERE market_id=?', (new_yes, new_no, fee, market_id))
    
    avg_price = amount / shares_out
    c.execute('SELECT id, shares, average_price FROM positions WHERE user_id=? AND market_id=? AND position=?', (user_id, market_id, pos))
    existing = c.fetchone()
    
    if existing:
        total_s = existing[1] + shares_out
        new_avg = ((existing[1] * existing[2]) + (shares_out * avg_price)) / total_s
        c.execute('UPDATE positions SET shares=?, average_price=? WHERE id=?', (total_s, new_avg, existing[0]))
    else:
        c.execute('INSERT INTO positions (market_id, user_id, position, shares, average_price) VALUES (?,?,?,?,?)', (market_id, user_id, pos, shares_out, avg_price))
    
    new_prob = get_prob(new_yes, new_no)
    c.execute('INSERT INTO price_history (market_id, prob_yes, timestamp) VALUES (?, ?, ?)', (market_id, new_prob, datetime.datetime.now()))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="✅ Bet Placed", color=discord.Color.green())
    embed.add_field(name="Position", value=f"**{pos.upper()}**")
    embed.add_field(name="Shares Bought", value=f"{shares_out:.1f}")
    embed.add_field(name="New YES Prob", value=f"{int(new_prob*100)}%")
    await interaction.response.send_message(embed=embed)

# 4. CHART
@bot.tree.command(name="chart", description="Show price history")
async def chart(interaction: discord.Interaction, market_id: int):
    await interaction.response.defer(ephemeral=True)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT question FROM markets WHERE market_id=?', (market_id,))
    res = c.fetchone()
    conn.close()
    
    if not res: return await interaction.followup.send("Market not found")
    
    buf = generate_chart(market_id, res[0])
    if not buf: return await interaction.followup.send("Not enough data for chart")
    
    file = discord.File(buf, filename="chart.png")
    embed = discord.Embed(color=discord.Color.dark_grey())
    embed.set_image(url="attachment://chart.png")
    await interaction.followup.send(file=file, embed=embed)

# 5. PORTFOLIO
@bot.tree.command(name="portfolio", description="View your positions")
async def portfolio(interaction: discord.Interaction):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''SELECT p.shares, p.position, p.average_price, m.question, m.pool_yes, m.pool_no 
                 FROM positions p JOIN markets m ON p.market_id = m.market_id 
                 WHERE p.user_id = ? AND m.status = 'active' ORDER BY p.id DESC''', (interaction.user.id,))
    rows = c.fetchall()
    bal = get_balance(interaction.user.id)
    conn.close()
    
    embed = discord.Embed(title="💼 Portfolio", description=f"Balance: **{int(bal)}** pts", color=discord.Color.blurple())
    if not rows: embed.add_field(name="Info", value="No active bets")
    
    for shares, pos, avg, q, py, pn in rows:
        curr_prob = get_prob(py, pn)
        curr_price = curr_prob if pos == 'yes' else (1 - curr_prob)
        pnl = (shares * curr_price) - (shares * avg)
        embed.add_field(name=f"{q[:30]}...", value=f"{pos.upper()} | {int(shares)} Shares\nPnL: {int(pnl)} pts", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# 6. RESOLVE (ADMIN)
@bot.tree.command(name="resolve", description="[ADMIN] Close market")
@app_commands.choices(winner=[app_commands.Choice(name="YES", value="yes"), app_commands.Choice(name="NO", value="no")])
async def resolve(interaction: discord.Interaction, market_id: int, winner: app_commands.Choice[str]):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("⛔ Admins only.", ephemeral=True)

    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT status FROM markets WHERE market_id=?', (market_id,))
    st = c.fetchone()
    
    if not st or st[0] != 'active':
        conn.close()
        return await interaction.response.send_message("Market cannot be resolved.", ephemeral=True)
    
    win = winner.value
    c.execute('UPDATE markets SET status=?, result=? WHERE market_id=?', ('closed', win, market_id))
    c.execute('SELECT user_id, shares FROM positions WHERE market_id=? AND position=?', (market_id, win))
    winners = c.fetchall()
    
    total = 0
    count = 0
    for uid, shares in winners:
        pay = shares * 1.0
        update_balance(uid, pay)
        total += pay
        count += 1
        
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="🏁 Market Resolved!", description=f"Winner: **{win.upper()}**", color=discord.Color.gold())
    embed.add_field(name="Payouts", value=f"{count} users / {int(total)} pts")
    await interaction.response.send_message(embed=embed)

# 7. DAILY & BALANCE
@bot.tree.command(name="daily", description="Claim 100 points")
async def daily(interaction: discord.Interaction):
    uid = interaction.user.id
    now = datetime.datetime.now()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT last_claim FROM users WHERE user_id = ?', (uid,))
    res = c.fetchone()
    
    if res and res[0]:
        last = res[0]
        if now - last < datetime.timedelta(hours=24):
            nxt = int((last + datetime.timedelta(hours=24)).timestamp())
            conn.close()
            return await interaction.response.send_message(f"⏳ Wait until <t:{nxt}:R>", ephemeral=True)

    update_balance(uid, 100)
    c.execute('UPDATE users SET last_claim = ? WHERE user_id = ?', (now, uid))
    conn.commit()
    conn.close()
    await interaction.response.send_message("💰 +100 points!", ephemeral=True)

@bot.tree.command(name="balance", description="Check your balance")
async def bal(interaction: discord.Interaction):
    await interaction.response.send_message(f"💳 Balance: **{int(get_balance(interaction.user.id))}** pts", ephemeral=True)

@bot.tree.command(name="help", description="How to play")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="🧠 How to Play", description="We trade Outcome Shares. 1 Share = 1 Point (if you win).", color=discord.Color.blurple())
    embed.add_field(name="Commands", value="/markets, /buy, /chart, /portfolio, /propose, /daily")
    await interaction.response.send_message(embed=embed, ephemeral=True)

if __name__ == "__main__":
    if TOKEN == "INSERT_YOUR_TOKEN_HERE":
        print("❌ ERROR: Please insert your bot token!")
    else:
        bot.run(TOKEN)