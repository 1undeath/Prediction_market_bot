import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import datetime
import math
import io
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import time
from discord.ext import tasks
import requests

# Import configuration
from config import (
    TOKEN, TG_BOT_TOKEN, TG_CHAT_ID,
    ADMIN_CHANNEL_ID, MARKETS_CHANNEL_ID, BYPASS_ROLE_ID,
    LMSR_B, TRADING_FEE, PROPOSAL_COST, STARTING_BALANCE, DAILY_REWARD,
    time_config,
    AUTO_RESOLVE_ENABLED, AUTO_RESOLVE_THRESHOLD, MANUAL_RESOLVE_RANGE,
    APPEAL_WINDOW_HOURS, CHECK_EXPIRED_INTERVAL_MINUTES,
    PROGRESS_BAR_LENGTH, CHART_WIDTH, CHART_HEIGHT, CHART_DPI,
    COLOR_GREEN_THRESHOLD, COLOR_RED_THRESHOLD,
    DB_NAME, DB_TIMEOUT, HEARTBEAT_INTERVAL_SECONDS
)

# ==========================================
# 🛠 DATABASE SETUP
# ==========================================

intents = discord.Intents.default()
intents.message_content = True

def adapt_datetime(ts): 
    return ts.isoformat()

def convert_datetime(ts): 
    return datetime.datetime.fromisoformat(ts.decode())

sqlite3.register_adapter(datetime.datetime, adapt_datetime)
sqlite3.register_converter("TIMESTAMP", convert_datetime)

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f'''CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, 
                  balance REAL DEFAULT {STARTING_BALANCE}, 
                  last_claim TIMESTAMP, 
                  last_proposal TIMESTAMP)''')
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
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  market_id INTEGER, 
                  user_id INTEGER, 
                  position TEXT, 
                  shares REAL, 
                  total_spent REAL,
                  FOREIGN KEY (market_id) REFERENCES markets(market_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS price_history 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  market_id INTEGER, 
                  prob_yes REAL, 
                  timestamp TIMESTAMP)''')
    conn.commit()
    conn.close()

# ==========================================
# 📊 LMSR MATH ENGINE
# ==========================================

def get_lmsr_cost(q1, q2):
    max_val = max(q1, q2)
    cost = max_val + LMSR_B * math.log(
        math.exp((q1 - max_val) / LMSR_B) + 
        math.exp((q2 - max_val) / LMSR_B)
    )
    return cost

def get_prob(q_yes, q_no):
    diff = (q_no - q_yes) / LMSR_B
    if diff > 100: return 0.0
    if diff < -100: return 1.0
    return 1.0 / (1.0 + math.exp(diff))

def calculate_shares_out_lmsr(q_target, q_other, amount_invested):
    amount_net = amount_invested * (1 - TRADING_FEE)
    current_cost = get_lmsr_cost(q_target, q_other)
    new_cost = current_cost + amount_net
    
    term1 = new_cost / LMSR_B
    term2 = q_other / LMSR_B
    
    inner = math.exp(term1) - math.exp(term2)
    if inner <= 0:
        return 0.0, amount_net
        
    new_q_target = LMSR_B * math.log(inner)
    shares_out = new_q_target - q_target
    return shares_out, amount_net

def calculate_cash_out_lmsr(q_target, q_other, shares_to_sell):
    if shares_to_sell > q_target:
        raise ValueError(f"Cannot sell {shares_to_sell:.2f} shares. Only {q_target:.2f} outstanding.")
    if shares_to_sell < 0:
        raise ValueError("Cannot sell negative shares")
    
    current_cost = get_lmsr_cost(q_target, q_other)
    new_q_target = max(0, q_target - shares_to_sell)
    new_cost = get_lmsr_cost(new_q_target, q_other)
    
    gross_payout = current_cost - new_cost
    fee = gross_payout * TRADING_FEE
    net_payout = gross_payout - fee
    return net_payout, fee, gross_payout

# ==========================================
# 🎨 VISUAL UTILITIES
# ==========================================

def create_progress_bar(percent_yes: float, length: int = PROGRESS_BAR_LENGTH) -> str:
    filled = int((percent_yes / 100) * length)
    empty = length - filled
    return "🟩" * filled + "⬛" * empty

def get_market_color(prob_yes: float):
    if prob_yes >= COLOR_GREEN_THRESHOLD: return 0x00FF00
    if prob_yes <= COLOR_RED_THRESHOLD: return 0xFF0000
    return 0xFFD700

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
    embed.set_footer(text=f"ID: {market_id} • Liquidity: {int(LMSR_B)} • Fee: {int(TRADING_FEE*100)}% buy/sell")
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
    fig, ax = plt.subplots(figsize=(CHART_WIDTH, CHART_HEIGHT))
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
    
    if len(dates) > 1 and (dates[-1] - dates[0]).days < 1:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    fig.autofmt_xdate()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=CHART_DPI, facecolor='#2f3136')
    buf.seek(0)
    plt.close()
    return buf

# ==========================================
# 💰 USER UTILITIES
# ==========================================

def get_balance(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    res = c.fetchone()
    conn.close()
    
    if not res:
        update_balance(user_id, 0)
        return STARTING_BALANCE
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
    for char in special_chars:
        text = text.replace(char, "\\" + char)
    return text

def send_proposal_to_tg(question, author_name, price, discord_link):
    safe_q = escape_markdown(question)
    safe_author = escape_markdown(author_name)
    text = (
        f"📩 *New Market Proposal!*\n\n"
        f"❓ *Question:* {safe_q}\n"
        f"👤 *Author:* {safe_author}\n"
        f"💰 *Cost:* {price} pts\n\n"
        f"🔗 [Go to Discord]({discord_link})"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True
            }
        )
    except Exception as e:
        print(f"❌ TG Error: {e}")

# ==========================================
# 💼 PORTFOLIO
# ==========================================

async def show_portfolio(interaction: discord.Interaction):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''SELECT m.market_id, p.shares, p.position, p.total_spent, m.question, m.pool_yes, m.pool_no 
                 FROM positions p JOIN markets m ON p.market_id = m.market_id 
                 WHERE p.user_id = ? AND m.status IN ('active', 'awaiting_resolution') 
                 ORDER BY p.id DESC''', (interaction.user.id,))
    rows = c.fetchall()
    bal = get_balance(interaction.user.id)
    conn.close()
    
    embed = discord.Embed(
        title="💼 Your Portfolio", 
        description=f"Balance: **{int(bal)}** pts", 
        color=discord.Color.blurple()
    )
    
    if not rows:
        embed.add_field(name="Empty", value="You have no active bets.")
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    options = []
    
    for mid, shares, pos, total_spent, q, py, pn in rows:
        try:
            if pos == 'yes':
                payout_now, _, _ = calculate_cash_out_lmsr(py, pn, shares)
            else:
                payout_now, _, _ = calculate_cash_out_lmsr(pn, py, shares)
            
            pnl = payout_now - total_spent
            pnl_percent = (pnl / total_spent * 100) if total_spent > 0 else 0
            pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➖"
            
            label = f"#{mid} {q[:15]}..."
            desc = f"{pos.upper()} {int(shares)} sh | Val: {int(payout_now)} | {pnl_emoji} {int(pnl)} ({pnl_percent:+.0f}%)"
            options.append(discord.SelectOption(label=label, description=desc, value=f"{mid}:{pos}:{shares}"))
            embed.add_field(name=label, value=desc, inline=False)
        except Exception as e:
            print(f"Portfolio error for market {mid}: {e}")
            label = f"#{mid} {q[:15]}..."
            desc = f"{pos.upper()} {int(shares)} shares | Error"
            options.append(discord.SelectOption(label=label, description=desc, value=f"{mid}:{pos}:{shares}"))
            embed.add_field(name=label, value=desc, inline=False)
    
    await interaction.response.send_message(embed=embed, view=PortfolioView(options), ephemeral=True)

# ==========================================
# 🖥️ UI MODALS
# ==========================================

class BuyModal(discord.ui.Modal):
    def __init__(self, market_id, position):
        super().__init__(title=f"Bet on {position.upper()}")
        self.market_id = market_id
        self.position = position
        self.amount_input = discord.ui.TextInput(
            label="Amount (pts)", 
            placeholder="e.g. 100", 
            min_length=1, 
            max_length=6
        )
        self.add_item(self.amount_input)
        
    async def on_submit(self, interaction: discord.Interaction):
        try: 
            amount = int(self.amount_input.value)
        except: 
            return await interaction.response.send_message("❌ Please enter a number!", ephemeral=True)
        
        if amount <= 0:
            return await interaction.response.send_message("❌ Amount must be > 0", ephemeral=True)
        
        user_id = interaction.user.id
        if get_balance(user_id) < amount:
            return await interaction.response.send_message("❌ Insufficient funds", ephemeral=True)
        
        conn = get_db_connection()
        c = conn.cursor()
        
        # --- ИЗМЕНЕНИЕ: Запрашиваем closes_at ---
        c.execute('SELECT status, pool_yes, pool_no, closes_at FROM markets WHERE market_id = ?', (self.market_id,))
        m = c.fetchone()
        
        # --- ИЗМЕНЕНИЕ: Проверяем статус И время ---
        # Если рынок не найден, или статус не active, или ТЕКУЩЕЕ ВРЕМЯ > ВРЕМЕНИ ЗАКРЫТИЯ
        if not m:
            conn.close()
            return await interaction.response.send_message("❌ Market not found", ephemeral=True)

        status, py, pn, closes_at = m[0], m[1], m[2], m[3]
        
        if status != 'active':
            conn.close()
            return await interaction.response.send_message("❌ Market is closed.", ephemeral=True)
            
        if closes_at < datetime.datetime.now():
            conn.close()
            return await interaction.response.send_message("⏳ Market time has expired! Waiting for resolution.", ephemeral=True)
        
        try:
            if self.position == "yes":
                shares, fee_amt = calculate_shares_out_lmsr(py, pn, amount)
                new_py, new_pn = py + shares, pn
            else:
                shares, fee_amt = calculate_shares_out_lmsr(pn, py, amount)
                new_py, new_pn = py, pn + shares
        except Exception as e:
            conn.close()
            return await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            
        if shares <= 0:
            conn.close()
            return await interaction.response.send_message("❌ Amount too small", ephemeral=True)
        
        fee_val = amount - fee_amt
        update_balance(user_id, -amount)
        
        c.execute(
            'UPDATE markets SET pool_yes=?, pool_no=?, fee_collected=fee_collected+? WHERE market_id=?',
            (new_py, new_pn, fee_val, self.market_id)
        )
        
        c.execute(
            'SELECT id, shares, total_spent FROM positions WHERE user_id=? AND market_id=? AND position=?',
            (user_id, self.market_id, self.position)
        )
        ex = c.fetchone()
        
        if ex:
            new_total_shares = ex[1] + shares
            new_total_spent = ex[2] + amount
            c.execute(
                'UPDATE positions SET shares=?, total_spent=? WHERE id=?',
                (new_total_shares, new_total_spent, ex[0])
            )
        else:
            c.execute(
                'INSERT INTO positions (market_id, user_id, position, shares, total_spent) VALUES (?,?,?,?,?)',
                (self.market_id, user_id, self.position, shares, amount)
            )
            
        new_prob = get_prob(new_py, new_pn)
        c.execute(
            'INSERT INTO price_history (market_id, prob_yes, timestamp) VALUES (?, ?, ?)',
            (self.market_id, new_prob, datetime.datetime.now())
        )
        conn.commit()
        conn.close()
        
        avg_price = amount / shares
        
        embed = discord.Embed(title="✅ Bet Accepted", color=discord.Color.green())
        embed.add_field(name="Amount", value=f"{amount} pts on {self.position.upper()}", inline=False)
        embed.add_field(name="Shares Bought", value=f"{shares:.1f}", inline=True)
        embed.add_field(name="Avg Price", value=f"{avg_price:.3f} pts/share", inline=True)
        embed.add_field(name="New YES Chance", value=f"{int(new_prob*100)}%", inline=True)
        embed.set_footer(text=f"Fee paid: {fee_val:.1f} pts ({int(TRADING_FEE*100)}%)")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        try:
            new_embed = await get_market_embed(self.market_id)
            if new_embed:
                await interaction.message.edit(embed=new_embed)
        except:
            pass

class SellModal(discord.ui.Modal):
    def __init__(self, market_id, position, max_shares):
        super().__init__(title=f"Sell {position.upper()}")
        self.market_id = market_id
        self.position = position
        self.max_shares = max_shares
        self.amount_input = discord.ui.TextInput(
            label=f"Shares (Max: {int(max_shares)})",
            placeholder="0 to sell ALL",
            default="0"
        )
        self.add_item(self.amount_input)
        
    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount_shares = float(self.amount_input.value)
        except:
            return await interaction.response.send_message("❌ Invalid number", ephemeral=True)
        
        user_id = interaction.user.id
        conn = get_db_connection()
        c = conn.cursor()
        
        # --- ИЗМЕНЕНИЕ: Запрашиваем closes_at ---
        c.execute(
            'SELECT status, pool_yes, pool_no, message_id, closes_at FROM markets WHERE market_id = ?',
            (self.market_id,)
        )
        market = c.fetchone()
        
        if not market:
            conn.close()
            return await interaction.response.send_message("❌ Market not found.", ephemeral=True)
            
        status, pool_yes, pool_no, msg_id, closes_at = market[0], market[1], market[2], market[3], market[4]
        
        # --- ИЗМЕНЕНИЕ: Жесткая проверка времени ---
        # Нельзя продавать, если статус не активен/ожидает решения, ИЛИ если время вышло
        if status not in ['active', 'awaiting_resolution']:
            conn.close()
            return await interaction.response.send_message("❌ Market closed.", ephemeral=True)

        if status == 'active' and closes_at < datetime.datetime.now():
            conn.close()
            return await interaction.response.send_message("⏳ Market expired! Trading halted.", ephemeral=True)

        shares_to_sell = amount_shares if amount_shares > 0 else self.max_shares
        
        if shares_to_sell > self.max_shares:
            conn.close()
            return await interaction.response.send_message("❌ You don't have enough shares.", ephemeral=True)
        
        if shares_to_sell < 0.01:
            conn.close()
            return await interaction.response.send_message("❌ Amount too small.", ephemeral=True)
            
        try:
            if self.position == "yes":
                cash_out, fee, raw_out = calculate_cash_out_lmsr(pool_yes, pool_no, shares_to_sell)
                new_yes, new_no = pool_yes - shares_to_sell, pool_no
            else:
                cash_out, fee, raw_out = calculate_cash_out_lmsr(pool_no, pool_yes, shares_to_sell)
                new_yes, new_no = pool_yes, pool_no - shares_to_sell
        except ValueError as e:
            conn.close()
            return await interaction.response.send_message(f"❌ {str(e)}", ephemeral=True)
        except Exception as e:
            conn.close()
            return await interaction.response.send_message(f"❌ Calculation error: {e}", ephemeral=True)
            
        update_balance(user_id, cash_out)
        
        c.execute(
            'SELECT id, total_spent FROM positions WHERE user_id=? AND market_id=? AND position=?',
            (user_id, self.market_id, self.position)
        )
        pos_data = c.fetchone()
        
        if not pos_data:
            conn.close()
            return await interaction.response.send_message("❌ Position not found", ephemeral=True)
        
        pos_id, total_spent = pos_data
        remaining = self.max_shares - shares_to_sell
        
        if remaining < 0.01:
            c.execute('DELETE FROM positions WHERE id=?', (pos_id,))
        else:
            new_total_spent = total_spent * (remaining / self.max_shares)
            c.execute(
                'UPDATE positions SET shares=?, total_spent=? WHERE id=?',
                (remaining, new_total_spent, pos_id)
            )
        
        c.execute(
            'UPDATE markets SET pool_yes=?, pool_no=?, fee_collected=fee_collected+? WHERE market_id=?',
            (new_yes, new_no, fee, self.market_id)
        )
        new_prob = get_prob(new_yes, new_no)
        c.execute(
            'INSERT INTO price_history (market_id, prob_yes, timestamp) VALUES (?, ?, ?)',
            (self.market_id, new_prob, datetime.datetime.now())
        )
        conn.commit()
        conn.close()
        
        embed = discord.Embed(title="💸 Sale Completed", color=discord.Color.blue())
        embed.add_field(name="Sold", value=f"{shares_to_sell:.1f} {self.position.upper()} shares", inline=False)
        embed.add_field(name="Received", value=f"{int(cash_out)} pts", inline=True)
        embed.add_field(name="Fee", value=f"{fee:.1f} pts ({int(TRADING_FEE*100)}%)", inline=True)
        embed.add_field(name="New YES Chance", value=f"{int(new_prob*100)}%", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        try:
            if msg_id and msg_id != 0 and market[0] == 'active':
                channel = interaction.client.get_channel(MARKETS_CHANNEL_ID)
                if channel:
                    msg_obj = await channel.fetch_message(msg_id)
                    new_embed = await get_market_embed(self.market_id)
                    await msg_obj.edit(embed=new_embed)
        except Exception as e:
            print(f"Update error: {e}")
class ProposeModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Create New Market")
        
        time_label = f"Duration ({time_config.time_unit})"
        time_placeholder = "5" if time_config.debug_mode else "48"
        
        self.question = discord.ui.TextInput(
            label="Question",
            placeholder="Will Bitcoin hit $100k?",
            min_length=5,
            max_length=100
        )
        self.hours = discord.ui.TextInput(
            label=time_label,
            placeholder=time_placeholder,
            default=time_placeholder,
            min_length=1,
            max_length=3
        )
        self.add_item(self.question)
        self.add_item(self.hours)
        
    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration_val = int(self.hours.value)
        except:
            return await interaction.response.send_message("❌ Duration must be a number!", ephemeral=True)
        
        question_val = self.question.value
        user_id = interaction.user.id
        now = datetime.datetime.now()
        is_vip = False
        
        if interaction.guild:
            user_roles = [r.id for r in interaction.user.roles]
            if BYPASS_ROLE_ID in user_roles or interaction.user.guild_permissions.administrator:
                is_vip = True
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT balance, last_proposal FROM users WHERE user_id = ?', (user_id,))
        res = c.fetchone()
        balance = res[0] if res else STARTING_BALANCE
        last_prop = res[1] if res else None
        
        if not is_vip:
            if duration_val < time_config.min_duration or duration_val > time_config.max_duration:
                conn.close()
                return await interaction.response.send_message(
                    f"❌ Duration must be between {time_config.min_duration} and {time_config.max_duration} {time_config.time_unit}.",
                    ephemeral=True
                )
            
            if last_prop and now - last_prop < datetime.timedelta(hours=time_config.proposal_cooldown_hours):
                conn.close()
                return await interaction.response.send_message(
                    f"⏳ Proposal cooldown active!",
                    ephemeral=True
                )
            
            if balance < PROPOSAL_COST:
                conn.close()
                return await interaction.response.send_message(
                    f"❌ Proposing costs {PROPOSAL_COST} pts. Insufficient funds.",
                    ephemeral=True
                )
        
        cost = 0 if is_vip else PROPOSAL_COST
        
        try:
            if not is_vip:
                c.execute(
                    'UPDATE users SET balance = balance - ?, last_proposal = ? WHERE user_id = ?',
                    (cost, now, user_id)
                )
            
            closes = now + time_config.get_timedelta_for_duration(duration_val)
            
            c.execute("""INSERT INTO markets 
                        (question, creator_id, created_at, closes_at, pool_yes, pool_no, status) 
                        VALUES (?,?,?,?,?,?,'pending')""",
                     (question_val, user_id, now, closes, 0.0, 0.0))
            mid = c.lastrowid
            c.execute(
                'INSERT INTO price_history (market_id, prob_yes, timestamp) VALUES (?, ?, ?)',
                (mid, 0.5, now)
            )
            conn.commit()
            
            channel = interaction.client.get_channel(ADMIN_CHANNEL_ID)
            embed = discord.Embed(title="📩 New Proposal", color=discord.Color.orange())
            embed.add_field(name="Question", value=question_val)
            embed.add_field(name="Author", value=interaction.user.mention)
            embed.add_field(name="Duration", value=f"{duration_val} {time_config.time_unit}")
            embed.set_footer(text=f"ID: {mid} | LMSR Model (B={int(LMSR_B)})")
            
            view = ApprovalView(
                market_id=mid,
                creator_id=user_id,
                question=question_val,
                amount=cost
            )
            msg = await channel.send(embed=embed, view=view)
            
            send_proposal_to_tg(question_val, interaction.user.name, cost, msg.jump_url)
            await interaction.response.send_message("✅ Proposal sent to moderators!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)
        finally:
            conn.close()

# ==========================================
# 🎮 VIEWS
# ==========================================

class SellSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Select position to SELL...",
            min_values=1,
            max_values=1,
            options=options
        )
    
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
        c.execute(
            'SELECT position, shares FROM positions WHERE user_id=? AND market_id=?',
            (user_id, self.market_id)
        )
        positions = c.fetchall()
        conn.close()
        
        if not positions:
            return await interaction.response.send_message(
                "❌ You have no shares in this market.",
                ephemeral=True
            )
        
        if len(positions) == 1:
            pos, shares = positions[0]
            if shares <= 0.01:
                return await interaction.response.send_message(
                    "❌ Not enough shares to sell.",
                    ephemeral=True
                )
            await interaction.response.send_modal(SellModal(self.market_id, pos, shares))
        else:
            options = []
            for pos, shares in positions:
                if shares > 0.01:
                    options.append(discord.SelectOption(
                        label=f"Sell {pos.upper()}",
                        description=f"Available: {int(shares)}",
                        value=f"{self.market_id}:{pos}:{shares}"
                    ))
            await interaction.response.send_message(
                "Select which position to sell:",
                view=PortfolioView(options),
                ephemeral=True
            )

    @discord.ui.button(label="📉 Chart", style=discord.ButtonStyle.secondary, row=1)
    async def chart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT question FROM markets WHERE market_id=?', (self.market_id,))
        res = c.fetchone()
        conn.close()
        
        if not res:
            return await interaction.followup.send("Market not found.")
        
        buf = generate_chart(self.market_id, res[0])
        if not buf:
            return await interaction.followup.send("No price history available.")
        
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
        self.market_id = market_id
        self.creator_id = creator_id
        self.question = question
        self.amount = amount
    
    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            'SELECT status FROM markets WHERE market_id=?',
            (self.market_id,)
        )
        res = c.fetchone()
        
        if not res or res[0] != 'pending':
            conn.close()
            return await interaction.response.send_message(
                "⚠️ Already processed.",
                ephemeral=True
            )
        
        c.execute('UPDATE markets SET status="active" WHERE market_id=?', (self.market_id,))
        conn.commit()
        
        new_embed = await get_market_embed(self.market_id)
        public_channel = interaction.client.get_channel(MARKETS_CHANNEL_ID)
        
        if public_channel and new_embed:
            msg = await public_channel.send(embed=new_embed, view=MarketControls(self.market_id))
            c.execute('UPDATE markets SET message_id=? WHERE market_id=?', (msg.id, self.market_id))
            conn.commit()
        
        conn.close()
        
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"✅ **APPROVED** by {interaction.user.mention}",
            view=self
        )
        
        try:
            user = await interaction.client.fetch_user(self.creator_id)
            await user.send(f"🚀 Your market **«{self.question}»** was approved!")
        except:
            pass

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('UPDATE markets SET status="rejected" WHERE market_id=?', (self.market_id,))
        c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (self.amount, self.creator_id))
        conn.commit()
        conn.close()
        
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ **REJECTED**", view=self)

class AppealView(discord.ui.View):
    def __init__(self, market_id, auto_result):
        super().__init__(timeout=None)
        self.market_id = market_id
        self.auto_result = auto_result
    
    @discord.ui.button(label="🔄 Override Result", style=discord.ButtonStyle.danger)
    async def override_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admins only", ephemeral=True)
        
        conn = get_db_connection()
        c = conn.cursor()
        
        # Revert auto-resolution: restore positions and refund
        c.execute('SELECT result FROM markets WHERE market_id=?', (self.market_id,))
        res = c.fetchone()
        if not res:
            conn.close()
            return await interaction.response.send_message("❌ Market not found", ephemeral=True)
        
        # Note: Reverting payouts is complex. For simplicity, just change status
        c.execute('UPDATE markets SET status=? WHERE market_id=?', ('awaiting_resolution', self.market_id))
        conn.commit()
        conn.close()
        
        for child in self.children:
            child.disabled = True
        
        await interaction.response.edit_message(
            content=f"⚠️ Auto-resolution OVERRIDDEN by {interaction.user.mention}\nUse `/resolve {self.market_id}` to set correct result",
            view=self
        )
    
    @discord.ui.button(label="✅ Confirm Correct", style=discord.ButtonStyle.green)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admins only", ephemeral=True)
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('UPDATE markets SET status=? WHERE market_id=?', ('closed', self.market_id))
        conn.commit()
        conn.close()
        
        for child in self.children:
            child.disabled = True
        
        await interaction.response.edit_message(
            content=f"✅ Result CONFIRMED by {interaction.user.mention}",
            view=self
        )

class ManualResolveView(discord.ui.View):
    def __init__(self, market_id):
        super().__init__(timeout=None)
        self.market_id = market_id
    
    @discord.ui.button(label="✅ YES Won", style=discord.ButtonStyle.green)
    async def yes_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admins only", ephemeral=True)
        await self.resolve_market(interaction, "yes")
    
    @discord.ui.button(label="❌ NO Won", style=discord.ButtonStyle.red)
    async def no_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admins only", ephemeral=True)
        await self.resolve_market(interaction, "no")
    
    async def resolve_market(self, interaction, winner):
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('SELECT status FROM markets WHERE market_id=?', (self.market_id,))
        res = c.fetchone()
        
        if not res or res[0] not in ['awaiting_resolution', 'active']:
            conn.close()
            return await interaction.response.send_message("❌ Market already resolved", ephemeral=True)
        
        c.execute('UPDATE markets SET status=?, result=? WHERE market_id=?', ('closed', winner, self.market_id))
        
        c.execute('SELECT user_id, shares FROM positions WHERE market_id=? AND position=?', (self.market_id, winner))
        winners = c.fetchall()
        
        total_paid = 0.0
        for uid, shares in winners:
            payout = shares * (1 - TRADING_FEE)
            c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (uid,))
            c.execute('UPDATE users SET balance = balance + ? WHERE user_id=?', (payout, uid))
            total_paid += payout
        
        c.execute('DELETE FROM positions WHERE market_id=?', (self.market_id,))
        conn.commit()
        conn.close()
        
        for child in self.children:
            child.disabled = True
        
        await interaction.response.edit_message(
            content=f"✅ Market #{self.market_id} resolved: **{winner.upper()}** won\nPaid: {int(total_paid)} pts to {len(winners)} winner(s)\nBy: {interaction.user.mention}",
            view=self
        )

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

# ==========================================
# ⏰ BACKGROUND TASKS
# ==========================================

@tasks.loop(seconds=HEARTBEAT_INTERVAL_SECONDS)
async def heartbeat_task():
    if bot.is_closed():
        return
    with open("heartbeat.txt", "w") as f:
        f.write(str(time.time()))

@tasks.loop(minutes=CHECK_EXPIRED_INTERVAL_MINUTES)
async def check_expired_markets():
    if bot.is_closed():
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.datetime.now()
    
    # Ищем рынки, которые все еще 'active', но время (closes_at) уже прошло
    c.execute('''SELECT market_id, question, creator_id, pool_yes, pool_no, closes_at 
                 FROM markets 
                 WHERE status='active' AND closes_at <= ?''', (now,))
    expired = c.fetchall()
    
    if not expired:
        conn.close()
        return
    
    print(f"🔄 Processing {len(expired)} expired markets...") # Лог в консоль
    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    
    for mid, question, creator_id, pool_yes, pool_no, closes_at in expired:
        prob_yes = get_prob(pool_yes, pool_no) * 100
        
        # Определяем победителя математически
        if prob_yes >= AUTO_RESOLVE_THRESHOLD:
            predicted_winner = "yes"
            confidence = "High"
        elif prob_yes <= (100 - AUTO_RESOLVE_THRESHOLD):
            predicted_winner = "no"
            confidence = "High"
        else:
            predicted_winner = None
            confidence = "Low"
        
        print(f"   Market #{mid}: Prob={prob_yes:.1f}%. Predicted: {predicted_winner}. Auto-Resolve: {AUTO_RESOLVE_ENABLED}")

        # ЕСЛИ авто-резолв включен И есть четкий победитель -> Платим
        if AUTO_RESOLVE_ENABLED and predicted_winner and confidence == "High":
            # 1. Меняем статус на auto_resolved
            c.execute('UPDATE markets SET status=?, result=? WHERE market_id=?', 
                     ('auto_resolved', predicted_winner, mid))
            
            # 2. Находим победителей
            c.execute('SELECT user_id, shares FROM positions WHERE market_id=? AND position=?',
                     (mid, predicted_winner))
            winners = c.fetchall()
            
            total_paid = 0.0
            # 3. Начисляем деньги
            for uid, shares in winners:
                payout = shares * (1 - TRADING_FEE) # Выплата = кол-во акций * 1 (цена победы) минус комиссия
                c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (uid,))
                c.execute('UPDATE users SET balance = balance + ? WHERE user_id=?', (payout, uid))
                total_paid += payout
            
            # 4. Удаляем позиции (так как они закрыты)
            c.execute('DELETE FROM positions WHERE market_id=?', (mid,))
            
            print(f"   ✅ Auto-resolved #{mid}. Paid {total_paid} to {len(winners)} users.")

            # 5. Уведомляем в админку (для логов)
            if admin_channel:
                embed = discord.Embed(
                    title="🤖 Auto-Resolved Market",
                    description=f"**#{mid}**: {question}",
                    color=discord.Color.green()
                )
                embed.add_field(name="Result", value=f"**{predicted_winner.upper()}** won", inline=True)
                embed.add_field(name="Confidence", value=f"{int(prob_yes if predicted_winner=='yes' else 100-prob_yes)}%", inline=True)
                embed.add_field(name="Payout", value=f"{int(total_paid)} pts to {len(winners)} winner(s)", inline=True)
                embed.set_footer(text=f"Can be appealed within {APPEAL_WINDOW_HOURS}h")
                await admin_channel.send(embed=embed, view=AppealView(mid, predicted_winner))
            
        else:
            # ИНАЧЕ (Авто-резолв выключен ИЛИ результат слишком спорный) -> Зовем админа
            c.execute('UPDATE markets SET status=? WHERE market_id=?', ('awaiting_resolution', mid))
            print(f"   ⚠️ Sent #{mid} to manual review.")
            
            if admin_channel:
                embed = discord.Embed(
                    title="⚠️ Manual Resolution Required",
                    description=f"**#{mid}**: {question}",
                    color=discord.Color.orange()
                )
                embed.add_field(name="Reason", value="Market odds too close OR Auto-resolve disabled", inline=False)
                embed.add_field(name="Current odds", value=f"YES: {int(prob_yes)}% | NO: {int(100-prob_yes)}%", inline=False)
                embed.add_field(name="Action needed", value=f"Use `/resolve {mid} [YES/NO]` or buttons below", inline=False)
                await admin_channel.send(embed=embed, view=ManualResolveView(mid))
    
    conn.commit()
    conn.close()

# ==========================================
# 🎯 BOT EVENTS
# ==========================================

@bot.event
async def on_ready():
    print(f'✅ Bot {bot.user} is online!')
    print(f'📊 LMSR B: {LMSR_B}')
    print(f'💰 Trading fee: {int(TRADING_FEE*100)}%')
    print(f'🕐 Mode: {"DEBUG (minutes)" if time_config.debug_mode else "PRODUCTION (hours)"}')
    
    if not heartbeat_task.is_running():
        heartbeat_task.start()
    if not check_expired_markets.is_running():
        check_expired_markets.start()
    
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced")
    except Exception as e:
        print(f"❌ Command sync error: {e}")

# ==========================================
# 💬 SLASH COMMANDS
# ==========================================

@bot.tree.command(name="setup_dashboard", description="[ADMIN] Create main dashboard")
async def setup_dashboard(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admins only", ephemeral=True)
    
    embed = discord.Embed(
        title="🔮 Prediction Market",
        description="Manage your bets and propose new events!",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="How it works",
        value="1. Propose an event (costs points)\n2. Others bet (YES/NO)\n3. Win points and climb the leaderboard!",
        inline=False
    )
    embed.add_field(
        name="⚡ Powered by LMSR",
        value=f"• Guaranteed liquidity\n• Fair pricing\n• {int(TRADING_FEE*100)}% fee on buy/sell",
        inline=False
    )
    
    await interaction.channel.send(embed=embed, view=DashboardView())
    await interaction.response.send_message("✅ Dashboard created!", ephemeral=True)

@bot.tree.command(name="top", description="View leaderboard")
async def top(interaction: discord.Interaction):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10')
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        embed = discord.Embed(
            title="📭 Leaderboard Empty",
            description="Type **/daily** to get started!",
            color=discord.Color.red()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    desc = ""
    for i, (uid, bal) in enumerate(rows, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        desc += f"**{medal}** <@{uid}> — **{int(bal)} pts**\n"
    
    embed = discord.Embed(title="🏆 Hall of Fame", description=desc, color=discord.Color.purple())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="markets", description="List active markets")
async def markets(interaction: discord.Interaction):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT market_id FROM markets WHERE status="active" ORDER BY created_at DESC LIMIT 5')
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return await interaction.response.send_message("❌ No active markets.", ephemeral=True)
    
    await interaction.response.send_message("🔥 **Active Markets**", ephemeral=True)
    
    for (mid,) in rows:
        embed = await get_market_embed(mid)
        if embed:
            await interaction.followup.send(embed=embed, view=MarketControls(mid), ephemeral=True)

@bot.tree.command(name="portfolio", description="View your positions")
async def portfolio_cmd(interaction: discord.Interaction):
    await show_portfolio(interaction)

@bot.tree.command(name="daily", description="Claim daily reward")
async def daily(interaction: discord.Interaction):
    uid = interaction.user.id
    now = datetime.datetime.now()
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT last_claim FROM users WHERE user_id = ?', (uid,))
    res = c.fetchone()
    
    cooldown_hours = time_config.daily_cooldown_hours
    
    if res and res[0] and now - res[0] < datetime.timedelta(hours=cooldown_hours):
        next_claim = int((res[0] + datetime.timedelta(hours=cooldown_hours)).timestamp())
        conn.close()
        return await interaction.response.send_message(
            f"⏳ Next claim <t:{next_claim}:R>",
            ephemeral=True
        )
    
    update_balance(uid, DAILY_REWARD)
    c.execute('UPDATE users SET last_claim = ? WHERE user_id = ?', (now, uid))
    conn.commit()
    conn.close()
    
    await interaction.response.send_message(
        f"💰 +{int(DAILY_REWARD)} points claimed!",
        ephemeral=True
    )

@bot.tree.command(name="balance", description="Check your balance")
async def bal(interaction: discord.Interaction):
    balance = get_balance(interaction.user.id)
    await interaction.response.send_message(
        f"💳 Your Balance: **{int(balance)}** pts",
        ephemeral=True
    )

@bot.tree.command(name="debug", description="[ADMIN] Toggle debug mode")
@app_commands.choices(mode=[
    app_commands.Choice(name="Enable (minutes)", value="on"),
    app_commands.Choice(name="Disable (hours)", value="off")
])
async def debug_mode(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admins only", ephemeral=True)
    
    if mode.value == "on":
        time_config.enable_debug()
        msg = (
            "🧪 **DEBUG MODE ENABLED**\n"
            f"• Time unit: **{time_config.time_unit}**\n"
            f"• Duration range: **{time_config.min_duration}-{time_config.max_duration}** min\n"
            f"• Daily cooldown: **~1 minute**\n"
            f"• Proposal cooldown: **~6 seconds**"
        )
    else:
        time_config.disable_debug()
        msg = (
            "🚀 **PRODUCTION MODE**\n"
            f"• Time unit: **{time_config.time_unit}**\n"
            f"• Duration range: **{time_config.min_duration}-{time_config.max_duration}** hours\n"
            f"• Daily cooldown: **24 hours**\n"
            f"• Proposal cooldown: **4 hours**"
        )
    
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="status", description="Show current bot settings")
async def status(interaction: discord.Interaction):
    embed = discord.Embed(title="⚙️ Bot Configuration", color=discord.Color.blue())
    
    mode_emoji = "🧪" if time_config.debug_mode else "🚀"
    mode_name = "DEBUG (Testing)" if time_config.debug_mode else "PRODUCTION"
    
    embed.add_field(name=f"{mode_emoji} Mode", value=mode_name, inline=False)
    embed.add_field(
        name="Time Settings",
        value=(
            f"• Unit: **{time_config.time_unit}**\n"
            f"• Duration: **{time_config.min_duration}-{time_config.max_duration}** {time_config.time_unit}\n"
            f"• Daily cooldown: **{time_config.daily_cooldown_hours * 60:.0f} min**"
        ),
        inline=False
    )
    embed.add_field(
        name="Economy",
        value=f"• LMSR B: **{int(LMSR_B)}**\n• Fee: **{int(TRADING_FEE*100)}%**\n• Proposal cost: **{int(PROPOSAL_COST)}** pts",
        inline=False
    )
    embed.add_field(
        name="Auto-Resolution",
        value=f"• Enabled: **{AUTO_RESOLVE_ENABLED}**\n• Threshold: **{AUTO_RESOLVE_THRESHOLD}%**",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="resolve", description="[ADMIN] Resolve a market")
@app_commands.choices(winner=[
    app_commands.Choice(name="YES", value="yes"),
    app_commands.Choice(name="NO", value="no")
])
async def resolve(interaction: discord.Interaction, market_id: int, winner: app_commands.Choice[str]):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admins only", ephemeral=True)

    conn = get_db_connection()
    c = conn.cursor()

    c.execute('SELECT status FROM markets WHERE market_id=?', (market_id,))
    row = c.fetchone()

    if not row or row[0] not in ['active', 'awaiting_resolution']:
        conn.close()
        return await interaction.response.send_message("❌ Market not active.", ephemeral=True)

    win = winner.value

    c.execute('UPDATE markets SET status="closed", result=? WHERE market_id=?', (win, market_id))

    c.execute('SELECT user_id, shares FROM positions WHERE market_id=? AND position=?', (market_id, win))
    winners = c.fetchall()

    total_paid = 0.0

    for uid, shares in winners:
        payout = shares * (1 - TRADING_FEE)
        c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (uid,))
        c.execute('UPDATE users SET balance = balance + ? WHERE user_id=?', (payout, uid))
        total_paid += payout

    c.execute('DELETE FROM positions WHERE market_id=?', (market_id,))

    conn.commit()
    conn.close()

    await interaction.response.send_message(
        f"✅ Market #{market_id} resolved.\n"
        f"Winner: **{win.upper()}**\n"
        f"Total payout: **{int(total_paid)} pts** to {len(winners)} winner(s)"
    )

@bot.tree.command(name="info", description="Show market info and statistics")
async def info(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 Market Information", color=discord.Color.blue())
    
    embed.add_field(
        name="🔬 Market Model",
        value=(
            f"**LMSR** (Logarithmic Market Scoring Rule)\n"
            f"• Guaranteed liquidity\n"
            f"• Fair automated pricing\n"
            f"• Max platform loss: ~{int(LMSR_B * 0.693)} pts per market"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⚙️ Parameters",
        value=(
            f"• Liquidity (B): **{int(LMSR_B)}**\n"
            f"• Trading fee: **{int(TRADING_FEE*100)}%**\n"
            f"• Proposal cost: **{int(PROPOSAL_COST)}** pts"
        ),
        inline=False
    )
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM markets WHERE status="active"')
    active = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM users WHERE balance > 0')
    users = c.fetchone()[0]
    c.execute('SELECT SUM(fee_collected) FROM markets')
    total_fees = c.fetchone()[0] or 0
    conn.close()
    
    embed.add_field(
        name="📈 Current Stats",
        value=f"• Active markets: **{active}**\n• Active traders: **{users}**\n• Total fees: **{int(total_fees)}** pts",
        inline=False
    )
    
    embed.set_footer(text="Use /markets to see active markets")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ==========================================
# 🏃 MAIN ENTRY POINT
# ==========================================

if __name__ == "__main__":
    if not TOKEN:
        print("❌ CRITICAL ERROR: DISCORD_BOT_TOKEN not found! Check .env file.")
    else:
        bot.run(TOKEN)