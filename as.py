import asyncio
from datetime import datetime, timedelta
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    MessageEntity,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton
)
import os
from threading import Thread
from flask import Flask

# ============================================
# CONFIGURATION & INITIALIZATION
# ============================================

BOT_TOKEN = os.environ.get('BOT_TOKEN', '8970788656:AAGmGCBKEAhNSpaW0YTv7zztcLPTTQwYRGo')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6237763207))
DATABASE_URL = os.environ.get('DATABASE_URL')

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

db_pool = None

# ============================================
# DUMMY FLASK SERVER FOR RENDER FREE TIER
# ============================================
flask_app = Flask('')

@flask_app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

# ============================================
# STATES
# ============================================

class UserState(StatesGroup):
    selling = State()
    setting_upi = State()
    submitting_task = State()

class AdminState(StatesGroup):
    waiting_for_task_reject_reason = State()
    waiting_for_sell_reject_reason = State()

# ============================================
# DATABASE INITIALIZATION
# ============================================

async def init_db():
    global db_pool
    url = DATABASE_URL
    if not url:
        raise ValueError("DATABASE_URL environment variable is missing!")
        
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
        
    db_pool = await asyncpg.create_pool(dsn=url, ssl='require')
    
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY, 
                balance DOUBLE PRECISION DEFAULT 0,
                upi TEXT DEFAULT 'None'
            )
        ''')
        # Ensure column exists for older table versions
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS upi TEXT DEFAULT 'None'")

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id BIGINT PRIMARY KEY
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY, 
                user_id BIGINT, 
                type TEXT, 
                amount DOUBLE PRECISION, 
                note TEXT, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id SERIAL PRIMARY KEY, 
                user_id BIGINT, 
                amount DOUBLE PRECISION, 
                upi TEXT, 
                status TEXT DEFAULT 'pending', 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY, 
                title TEXT, 
                details TEXT, 
                reward DOUBLE PRECISION, 
                status TEXT DEFAULT 'available'
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS task_assignments (
                task_id INT UNIQUE, 
                user_id BIGINT, 
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

# ============================================
# HELPERS & KEYBOARDS
# ============================================

async def ensure_user(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (user_id, balance, upi) VALUES ($1, 0, 'None') ON CONFLICT (user_id) DO NOTHING", user_id)

async def get_user_data(user_id: int):
    await ensure_user(user_id)
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT balance, upi FROM users WHERE user_id=$1", user_id)

async def get_balance(user_id: int) -> float:
    data = await get_user_data(user_id)
    return data['balance'] if data else 0.0

async def is_banned(user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM banned_users WHERE user_id=$1", user_id)
        return row is not None

def get_main_menu_keyboard():
    kb = ReplyKeyboardBuilder()
    
    # Row 1: Green buttons
    kb.button(text="✍️ Get Task", style="success")
    kb.button(text="😀 Sell Gmail", style="success")
    
    # Row 2: Blue buttons
    kb.button(text="💰 Balance", style="primary")
    kb.button(text="📜 History", style="primary")
    
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)

def get_balance_inline_keyboard(upi_set: bool):
    kb = InlineKeyboardBuilder()
    link_text = "🔗 Change UPI" if upi_set else "🔗 Link UPI"
    kb.button(text=link_text, callback_data="link_upi")
    kb.button(text="💸 Withdraw", callback_data="inline_withdraw")
    kb.adjust(1, 1)
    return kb.as_markup()

def get_task_action_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Submit", callback_data="user_submit_task"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="user_cancel_task")
    ]])

async def edit_admin_message(call: CallbackQuery, new_text: str):
    try:
        if call.message.photo:
            await call.message.edit_caption(caption=new_text, reply_markup=None, parse_mode=ParseMode.HTML)
        else:
            await call.message.edit_text(text=new_text, reply_markup=None, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"Error editing admin message: {e}")

# ============================================
# GLOBAL BAN MIDDLEWARES
# ============================================

@dp.message.outer_middleware()
async def ban_check_message_middleware(handler, event: Message, data):
    if event.from_user and event.from_user.id == ADMIN_ID:
        return await handler(event, data)
        
    if event.from_user and await is_banned(event.from_user.id):
        await event.answer("🚫 You are banned from using this bot.")
        return
        
    return await handler(event, data)

@dp.callback_query.outer_middleware()
async def ban_check_callback_middleware(handler, event: CallbackQuery, data):
    if event.from_user and event.from_user.id == ADMIN_ID:
        return await handler(event, data)
        
    if event.from_user and await is_banned(event.from_user.id):
        await event.answer("🚫 You are banned from using this bot.", show_alert=True)
        return
        
    return await handler(event, data)

# ============================================
# START & GLOBAL CANCEL
# ============================================

@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await ensure_user(message.from_user.id)
    
    text = (
        '<tg-emoji emoji-id="5377548235709619284">🔥</tg-emoji> <b>Gmail EarneX Wallet Bot</b>\n\n'
        '<tg-emoji emoji-id="5287684458881756303">📋</tg-emoji> <b>Use the buttons below to operate the bot:</b>\n\n'
        '• <b>Get Task:</b> Receive a new task (50₹/ Gmail) <tg-emoji emoji-id="5197269100878907942">✍️</tg-emoji>\n'
        '• <b>Sell Gmail:</b> Sell old accounts (30₹/ Gmail) <tg-emoji emoji-id="5008025248314950702">😀</tg-emoji>\n'
        '• <b>Balance:</b> Check wallet balance & withdraw funds <tg-emoji emoji-id="5278467510604160626">💰</tg-emoji>\n'
    )
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())

@dp.message(Command("cancel"))
@dp.message(F.text == "🚫 Cancel")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer('<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> Current operation cancelled.', reply_markup=get_main_menu_keyboard())

# ============================================
# BALANCE & LINK UPI / WITHDRAW SYSTEM
# ============================================

@dp.message(Command("balance"))
@dp.message(F.text == "💰 Balance")
async def balance(message: Message, state: FSMContext):
    await state.clear()
    user_data = await get_user_data(message.from_user.id)
    bal = user_data['balance'] if user_data else 0.0
    upi = user_data['upi'] if user_data and user_data['upi'] else "None"

    upi_set = upi != "None" and upi != ""
    
    text = (
        f"💳 <b>Balance: ₹{bal:.2f}</b>\n"
        f"<b>UPI:</b> {upi}"
    )
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_balance_inline_keyboard(upi_set))

@dp.callback_query(F.data == "link_upi")
async def start_link_upi(call: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.setting_upi)
    await call.message.answer("🔗 Send your UPI ID below:\n\n<i>Example: username@upi or 9876543210@paytm</i>", parse_mode=ParseMode.HTML)
    await call.answer()

@dp.message(UserState.setting_upi, F.text, ~F.text.startswith("/"))
async def process_link_upi(message: Message, state: FSMContext):
    upi_input = message.text.strip()
    if "@" not in upi_input or len(upi_input) < 5:
        await message.answer("❌ Invalid UPI ID format. Please send a valid UPI ID (e.g. `yourname@upi`).", parse_mode=ParseMode.MARKDOWN)
        return

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET upi=$1 WHERE user_id=$2", upi_input, message.from_user.id)

    await message.answer(f"✅ Your UPI ID has been linked to: `{upi_input}`", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard())
    await state.clear()

@dp.callback_query(F.data == "inline_withdraw")
async def inline_withdraw_handler(call: CallbackQuery):
    user_data = await get_user_data(call.from_user.id)
    bal = user_data['balance'] if user_data else 0.0
    upi = user_data['upi'] if user_data else "None"

    if upi == "None" or not upi:
        await call.answer("❌ Please link your UPI ID first before withdrawing!", show_alert=True)
        return

    MIN_WITHDRAW = 150.0
    if bal < MIN_WITHDRAW:
        await call.answer(f"❌ Minimum withdrawal is ₹{MIN_WITHDRAW:.0f}. Current Balance: ₹{bal:.2f}", show_alert=True)
        return

    async with db_pool.acquire() as conn:
        withdraw_id = await conn.fetchval(
            'INSERT INTO withdrawals(user_id, amount, upi) VALUES ($1, $2, $3) RETURNING id',
            call.from_user.id, bal, upi
        )

    kb = InlineKeyboardBuilder()
    kb.button(text='💸 Pay', callback_data=f'pay:{withdraw_id}:{call.from_user.id}:{bal}')
    kb.button(text='❌ Reject', callback_data=f'reject:{withdraw_id}:{call.from_user.id}')
    
    await bot.send_message(
        ADMIN_ID,
        f'💰 <b>WITHDRAWAL REQUEST #{withdraw_id}</b>\n\n'
        f'👤 @{call.from_user.username}\n'
        f'🆔 <code>{call.from_user.id}</code>\n'
        f'💵 Amount: ₹{bal:.2f}\n'
        f'🏦 UPI: <code>{upi}</code>',
        reply_markup=kb.as_markup(),
        parse_mode=ParseMode.HTML
    )

    await call.message.edit_text(f"⏳ Withdrawal request of ₹{bal:.2f} sent to admin using UPI: <code>{upi}</code>", parse_mode=ParseMode.HTML)
    await call.answer()

# ============================================
# HISTORY
# ============================================

@dp.message(Command("history"))
@dp.message(F.text == "📜 History")
async def history(message: Message):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT type, amount, note, created_at FROM transactions WHERE user_id=$1 ORDER BY id DESC LIMIT 10", message.from_user.id)
    if not rows:
        await message.answer("📭 No transactions found.", reply_markup=get_main_menu_keyboard())
        return
    text = '<tg-emoji emoji-id="5197288647275071607">📜</tg-emoji> <b>Last Transactions</b>\n\n'
    for r in rows:
        sign = "+" if r['amount'] >= 0 else ""
        text += f"• {sign}₹{r['amount']:.2f} | {r['type']}\n{r['note']}\n{r['created_at'].strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())

# ============================================
# SELL SYSTEM
# ============================================

@dp.message(Command("sell"))
@dp.message(F.text == "😀 Sell Gmail")
async def sell(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(UserState.selling)
    await message.answer("📦 Send item details in this format:\n\n📧 Username: example@gmail.com\n🔑 Password: example@123\n💸 Rate: 30₹ Per Gmail !\n⚠️ Note: Logout After Submitting")

@dp.message(UserState.selling, F.text, ~F.text.startswith("/"))
async def handle_sell(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Approve", callback_data=f"sellapprove:{message.from_user.id}:30"),
        InlineKeyboardButton(text="❌ Decline", callback_data=f"selldecline:{message.from_user.id}")
    ]])
    await bot.send_message(ADMIN_ID, f"📦 <b>New Sell Request</b>\n\n👤 User: @{message.from_user.username}\n🆔 ID: {message.from_user.id}\n\n{message.text}", reply_markup=kb, parse_mode=ParseMode.HTML)
    await message.answer("✅ Your item has been sent for admin review.", reply_markup=get_main_menu_keyboard())
    await state.clear()

@dp.callback_query(F.data.startswith("sellapprove:"))
async def approve_sell(call: CallbackQuery):
    _, user_id, amount = call.data.split(":")
    user_id = int(user_id)
    amount = float(amount)
    
    await edit_admin_message(call, "✅ Processing Sell Approval...")
    
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id=$2", amount, user_id)
            await conn.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1, $2, $3, $4)", user_id, "sell", amount, "Gmail sell approved")
    
    try:
        await bot.send_message(user_id, f"🎉 Sell approved!\n+₹{amount} added to your balance.")
    except:
        pass
    await edit_admin_message(call, "✅ Sell approved and balance credited.")

@dp.callback_query(F.data.startswith("selldecline:"))
async def decline_sell(call: CallbackQuery, state: FSMContext):
    _, user_id = call.data.split(":")
    user_id = int(user_id)
    
    await state.set_state(AdminState.waiting_for_sell_reject_reason)
    await state.update_data(
        user_id=user_id, 
        admin_msg_id=call.message.message_id,
        is_photo=bool(call.message.photo)
    )
    await call.message.answer("❓ **Please reply with the reason for declining this sell request:**", parse_mode=ParseMode.MARKDOWN)
    await call.answer()

@dp.message(AdminState.waiting_for_sell_reject_reason, ~F.text.startswith("/"))
async def process_sell_reject_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = data['user_id']
    admin_msg_id = data['admin_msg_id']
    is_photo = data['is_photo']
    reason = message.text.strip()

    new_text = f"❌ <b>Sell request declined.</b>\n<b>Reason:</b> {reason}"
    try:
        if is_photo:
            await bot.edit_message_caption(chat_id=message.chat.id, message_id=admin_msg_id, caption=new_text, reply_markup=None, parse_mode=ParseMode.HTML)
        else:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=admin_msg_id, text=new_text, reply_markup=None, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"Error editing admin msg: {e}")

    try:
        await bot.send_message(user_id, f"❌ <b>Your sell request was declined.</b>\n\n💬 <b>Reason:</b> {reason}", parse_mode=ParseMode.HTML)
    except:
        pass

    await message.answer("✅ Rejection reason sent to user.")
    await state.clear()

# ============================================
# WITHDRAWAL CALLBACKS (ADMIN SIDE)
# ============================================

@dp.callback_query(F.data.startswith("pay:"))
async def pay_withdraw(call: CallbackQuery):
    _, withdrawal_id, user_id, amount = call.data.split(":")
    withdrawal_id = int(withdrawal_id)
    user_id = int(user_id)
    amount = float(amount)
    
    async with db_pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM withdrawals WHERE id=$1", withdrawal_id)
        if status != 'pending':
            await call.answer("⚠️ This withdrawal request has already been processed!", show_alert=True)
            return

        async with conn.transaction():
            await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id=$2", amount, user_id)
            await conn.execute("UPDATE withdrawals SET status='paid' WHERE id=$1", withdrawal_id)
            await conn.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1, $2, $3, $4)", user_id, "withdrawal", -amount, "Withdrawal paid")
            
    await edit_admin_message(call, "✅ Withdrawal marked as paid.")
    try:
        await bot.send_message(user_id, f"🎉 Withdrawal of ₹{amount} has been paid.")
    except:
        pass

@dp.callback_query(F.data.startswith("reject:"))
async def reject_withdraw(call: CallbackQuery):
    _, withdrawal_id, user_id = call.data.split(":")
    withdrawal_id = int(withdrawal_id)
    user_id = int(user_id)
    
    async with db_pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM withdrawals WHERE id=$1", withdrawal_id)
        if status != 'pending':
            await call.answer("⚠️ This withdrawal request has already been processed!", show_alert=True)
            return

        await conn.execute("UPDATE withdrawals SET status='rejected' WHERE id=$1", withdrawal_id)
        
    await edit_admin_message(call, "❌ Withdrawal rejected.")
    try:
        await bot.send_message(user_id, "❌ Your withdrawal request was rejected.")
    except:
        pass

# ============================================
# ADMIN CONTROLS
# ============================================

@dp.message(Command("add"))
async def add_balance(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, user_id, amount = message.text.split()
        user_id = int(user_id)
        amount = float(amount)
        await ensure_user(user_id)
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id=$2", amount, user_id)
                await conn.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1, $2, $3, $4)", user_id, "admin_add", amount, "Admin balance add")
        await message.answer(f"✅ Added ₹{amount} to {user_id}")
        await bot.send_message(user_id, f"💰 Admin added ₹{amount} to your balance.")
    except Exception as e:
        await message.answer(f"Usage: /add USER_ID AMOUNT\nError: {e}")

@dp.message(Command("cut"))
async def cut_balance(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, user_id, amount = message.text.split()
        user_id = int(user_id)
        amount = float(amount)

        current_balance = await get_balance(user_id)
        if amount > current_balance:
            await message.answer(f"❌ Cannot cut ₹{amount}. User's current balance is only ₹{current_balance:.2f}.")
            return

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id=$2", amount, user_id)
                await conn.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1, $2, $3, $4)", user_id, "admin_cut", -amount, "Admin balance cut")

        await message.answer(f"✅ Cut ₹{amount} from {user_id}")
        await bot.send_message(user_id, f"⚠️ Admin deducted ₹{amount} from your balance.")
    except Exception as e:
        await message.answer(f"Usage: /cut USER_ID AMOUNT\nError: {e}")

@dp.message(Command("checkbal"))
async def check_user_balance(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    if not command.args:
        await message.answer("❌ Missing User ID!\n\nUsage: `/checkbal 123456789`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        target_id = int(command.args.strip())
        user_data = await get_user_data(target_id)
        bal = user_data['balance'] if user_data else 0.0
        upi = user_data['upi'] if user_data else "None"
        banned = await is_banned(target_id)
        status = "🔴 Banned" if banned else "🟢 Active"
        await message.answer(f"👤 **User ID:** `{target_id}`\n💰 **Balance:** ₹{bal:.2f}\n🏦 **UPI:** `{upi}`\n📌 **Status:** {status}", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await message.answer("❌ Invalid User ID. Please provide a valid numeric ID.")

@dp.message(Command("top"))
async def top_balances(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10")

    if not rows:
        await message.answer("📭 No users found in database.")
        return

    text = "🏆 **Top 10 Balance Holders**\n\n"
    for idx, r in enumerate(rows, start=1):
        text += f"**{idx}.** User ID: `{r['user_id']}` — **₹{r['balance']:.2f}**\n"

    await message.answer(text, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("ban"))
async def ban_user(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    if not command.args:
        await message.answer("❌ Missing User ID!\n\nUsage: `/ban 123456789`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        target_id = int(command.args.strip())
        if target_id == ADMIN_ID:
            await message.answer("❌ You cannot ban yourself!")
            return

        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO banned_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", target_id)

        await message.answer(f"🚫 **User `{target_id}` has been banned.**", parse_mode=ParseMode.MARKDOWN)
        try:
            await bot.send_message(target_id, "🚫 You have been banned from using this bot.")
        except:
            pass
    except ValueError:
        await message.answer("❌ Invalid User ID. Provide a numeric ID.")

@dp.message(Command("unban"))
async def unban_user(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    if not command.args:
        await message.answer("❌ Missing User ID!\n\nUsage: `/unban 123456789`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        target_id = int(command.args.strip())
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM banned_users WHERE user_id=$1", target_id)

        await message.answer(f"✅ **User `{target_id}` has been unbanned.**", parse_mode=ParseMode.MARKDOWN)
        try:
            await bot.send_message(target_id, "🎉 Your ban has been lifted! You can now use the bot again.")
        except:
            pass
    except ValueError:
        await message.answer("❌ Invalid User ID. Provide a numeric ID.")

@dp.message(Command("broadcast"))
async def broadcast_message(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return

    if not message.reply_to_message and not command.args:
        await message.answer(
            "📢 **How to Broadcast:**\n\n"
            "1. Reply to any message/photo/file with `/broadcast`\n"
            "2. Or type `/broadcast Your message here`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    async with db_pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users")

    if not users:
        await message.answer("📭 No users found in the database.")
        return

    status_msg = await message.answer(f"🚀 **Starting Broadcast** to {len(users)} users...")

    success = 0
    failed = 0

    for r in users:
        uid = r['user_id']
        try:
            if message.reply_to_message:
                await message.reply_to_message.copy_to(chat_id=uid)
            else:
                await bot.send_message(chat_id=uid, text=command.args)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"📢 **Broadcast Finished!**\n\n"
        f"✅ **Sent:** {success}\n"
        f"❌ **Failed/Blocked:** {failed}\n"
        f"👥 **Total:** {len(users)}"
    )

@dp.message(Command("addtask"))
async def add_task(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    if not command.args:
        await message.answer("❌ Missing username!\n\nUsage: `/addtask philibertg1286`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        username_input = command.args.strip()
        
        # Append @gmail.com automatically if not provided
        if "@" not in username_input:
            username = f"{username_input}@gmail.com"
        else:
            username = username_input

        # Default password and reward
        password = "TaskVerse@#"
        default_reward = 50.0 
        
        title = f"Login to {username}"
        details = f"Email: {username} | Pass: {password}"
        
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO tasks (title, details, reward) VALUES ($1, $2, $3)", title, details, default_reward)
            
        await message.answer(
            f"✅ **Task Added Successfully!**\n\n"
            f"📧 **Email:** `{username}`\n"
            f"🔑 **Password:** `{password}`\n"
            f"💰 **Reward:** ₹{default_reward}", 
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await message.answer(f"❌ Error creating task: {str(e)}")

@dp.message(Command("updateallrewards"))
async def update_all_rewards(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    if not command.args:
        await message.answer(
            "❌ Missing reward amount!\n\nUsage:\n`/updateallrewards [New Reward]`\nExample: `/updateallrewards 40.0`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    try:
        new_reward = float(command.args.strip())
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE tasks SET reward=$1", new_reward)
        await message.answer(f"💰 **Success!** The reward for **ALL** tasks has been updated to **₹{new_reward:.2f}**.")
    except ValueError:
        await message.answer("❌ Invalid reward amount. Please provide a valid number. (e.g., `/updateallrewards 35`)")
    except Exception as e:
        await message.answer(f"❌ An error occurred: {str(e)}")

@dp.message(Command("removetask"))
async def remove_task(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    if not command.args:
        await message.answer("❌ Missing Task ID!\n\nUsage:\n`/removetask 3`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        task_id = int(command.args.strip())
        async with db_pool.acquire() as conn:
            task = await conn.fetchrow("SELECT id FROM tasks WHERE id=$1", task_id)
            if not task:
                await message.answer(f"❌ Task #{task_id} does not exist in the database.")
                return
            async with conn.transaction():
                await conn.execute("DELETE FROM task_assignments WHERE task_id=$1", task_id)
                await conn.execute("DELETE FROM tasks WHERE id=$1", task_id)
        await message.answer(f"🗑️ **Task #{task_id}** has been permanently removed from the bot.")
    except ValueError:
        await message.answer("❌ Invalid Task ID. Please provide a valid number. (e.g., `/removetask 3`)")
    except Exception as e:
        await message.answer(f"❌ An error occurred: {str(e)}")

# ============================================
# TASK ENGINE
# ============================================

@dp.message(Command('task'))
@dp.message(F.text == "✍️ Get Task")
async def get_task(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        
        # Check for existing assigned task, bringing in task details
        existing = await conn.fetchrow('''
            SELECT t.id, t.title, t.details, t.reward, t.status, a.assigned_at 
            FROM task_assignments a
            JOIN tasks t ON a.task_id = t.id
            WHERE a.user_id=$1
        ''', user_id)
        
        if existing:
            task_id = existing['id']
            assigned_time = existing['assigned_at']
            task_status = existing['status']
            
            # Check if task is currently under admin review
            if task_status == 'pending_review':
                await message.answer("⏳ Your task submission is currently under admin review. Please wait for the admin to check it.", reply_markup=get_main_menu_keyboard())
                return

            expire_time = assigned_time + timedelta(minutes=30)
            remaining = expire_time - datetime.utcnow()
            total_seconds = int(remaining.total_seconds())
            
            if total_seconds > 0:
                mins = total_seconds // 60
                secs = total_seconds % 60
                
                # Extract details
                try:
                    parts = existing['details'].split(" | ")
                    username = parts[0].replace("Email: ", "").strip()
                    password = parts[1].replace("Pass: ", "").strip()
                except:
                    username = existing['title'].replace("Login to ", "")
                    password = "See Admin"

                await message.answer(
                    f"⚠️ **You already have an active task.**\n\n"
                    f"🎯 **Your Current Task**\n\n"
                    f"🆔 #{task_id}\n"
                    f"📝 **Email:** {username} | **Password:** `{password}`\n"
                    f"💰 **Reward:** ₹{existing['reward']}\n\n"
                    f"⏰ Time Remaining: {mins}m {secs}s", 
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=get_task_action_keyboard()
                )
                return
            else:
                # Expired scenario cleanup
                async with conn.transaction():
                    await conn.execute('DELETE FROM task_assignments WHERE user_id=$1', user_id)
                    await conn.execute('UPDATE tasks SET status=$1 WHERE id=$2', 'available', task_id)

        # Proceed to fetch new task if none exists or it was expired
        task = await conn.fetchrow("SELECT id, title, details, reward FROM tasks WHERE status='available' ORDER BY RANDOM() LIMIT 1")
        if not task:
            await message.answer('📭 No tasks available right now.', reply_markup=get_main_menu_keyboard())
            return
        
        task_id = task['id']
        title = task['title']
        details = task['details']
        reward = task['reward']
        
        async with conn.transaction():
            await conn.execute("UPDATE tasks SET status='assigned' WHERE id=$1", task_id)
            await conn.execute('INSERT INTO task_assignments(task_id, user_id) VALUES ($1, $2)', task_id, user_id)

    try:
        parts = details.split(" | ")
        username = parts[0].replace("Email: ", "").strip()
        password = parts[1].replace("Pass: ", "").strip()
    except:
        username = title.replace("Login to ", "")
        password = "See Admin"

    await message.answer(
        f"🎯 **Task #{task_id}**\n\n"
        f"📝 **Email:** {username} | **Password:** `{password}`\n"
        f"💰 **Reward:** ₹{reward}\n\n"
        f"⏰ You have ONLY 30 MINUTES to complete this task.", 
        parse_mode=ParseMode.MARKDOWN, 
        reply_markup=get_task_action_keyboard()
    )

# ============================================
# USER INLINE SUBMIT & CANCEL SYSTEM
# ============================================

@dp.callback_query(F.data == "user_submit_task")
async def inline_submit_task(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT ta.task_id, t.status FROM task_assignments ta JOIN tasks t ON ta.task_id = t.id WHERE ta.user_id=$1', user_id)
    
    if not row:
        await call.answer('❌ You do not have any active task.', show_alert=True)
        return
    if row['status'] == 'pending_review':
        await call.answer('⏳ You have already submitted this task.', show_alert=True)
        return
        
    await state.set_state(UserState.submitting_task)
    
    # Remove inline buttons so they can't be spammed
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer('📤 Send screenshot or proof of completed task.')
    await call.answer()

@dp.callback_query(F.data == "user_cancel_task")
async def inline_cancel_task(call: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = call.from_user.id
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT ta.task_id, t.status FROM task_assignments ta JOIN tasks t ON ta.task_id = t.id WHERE ta.user_id=$1', user_id)
        if not row:
            await call.answer("❌ You don't have any active task to cancel.", show_alert=True)
            return
        
        if row['status'] == 'pending_review':
            await call.answer("❌ Cannot cancel a task already submitted for admin review.", show_alert=True)
            return

        task_id = row['task_id']
        async with conn.transaction():
            await conn.execute('DELETE FROM task_assignments WHERE user_id=$1', user_id)
            await conn.execute("UPDATE tasks SET status='available' WHERE id=$1", task_id)
            
    # Modify the original message to show it was cancelled
    await call.message.edit_text(f"✅ Task #{task_id} has been cancelled and returned to the pool.")
    await call.answer()

# Fallbacks for commands if users manually type them out 
@dp.message(Command('submit'))
async def fallback_submit(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT ta.task_id, t.status FROM task_assignments ta JOIN tasks t ON ta.task_id = t.id WHERE ta.user_id=$1', user_id)
    if not row:
        await message.answer('❌ You do not have any active task.', reply_markup=get_main_menu_keyboard())
        return
    if row['status'] == 'pending_review':
        await message.answer('⏳ You have already submitted this task. Please wait for admin review.', reply_markup=get_main_menu_keyboard())
        return
    await state.set_state(UserState.submitting_task)
    await message.answer('📤 Send screenshot or proof of completed task.')

@dp.message(Command("cancel_task"))
async def fallback_cancel(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT ta.task_id, t.status FROM task_assignments ta JOIN tasks t ON ta.task_id = t.id WHERE ta.user_id=$1', user_id)
        if not row:
            await message.answer("❌ You don't have any active task to cancel.", reply_markup=get_main_menu_keyboard())
            return
        
        if row['status'] == 'pending_review':
            await message.answer("❌ You cannot cancel a task that has already been submitted for admin review.", reply_markup=get_main_menu_keyboard())
            return

        task_id = row['task_id']
        async with conn.transaction():
            await conn.execute('DELETE FROM task_assignments WHERE user_id=$1', user_id)
            await conn.execute("UPDATE tasks SET status='available' WHERE id=$1", task_id)
    await message.answer(f"✅ Task #{task_id} has been cancelled and returned to the pool.", reply_markup=get_main_menu_keyboard())

# ============================================
# SUBMISSION PHOTO/TEXT HANDLER
# ============================================
@dp.message(UserState.submitting_task, ~F.text.startswith("/"))
async def handle_task_submission(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        task = await conn.fetchrow('SELECT t.id, t.title, t.reward FROM task_assignments ta JOIN tasks t ON ta.task_id = t.id WHERE ta.user_id=$1', user_id)
    if not task:
        await state.clear()
        await message.answer('❌ No active task found.', reply_markup=get_main_menu_keyboard())
        return
    
    task_id = task['id']
    title = task['title']
    reward = task['reward']
    
    # Mark task as pending_review so auto_expire won't expire it or release it back to pool
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE tasks SET status='pending_review' WHERE id=$1", task_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='✅ Approve', callback_data=f'taskapprove:{task_id}:{user_id}:{reward}'),
        InlineKeyboardButton(text='❌ Decline', callback_data=f'taskdecline:{task_id}:{user_id}')
    ]])
    if message.photo:
        await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, caption=f'📤 **Task Submission**\n\n👤 User: @{message.from_user.username}\n🆔 Task #{task_id}\n📌 {title}\n💰 Reward: ₹{reward}', reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await bot.send_message(ADMIN_ID, f'📤 **Task Submission**\n\n👤 User: @{message.from_user.username}\n🆔 Task #{task_id}\n📌 {title}\n💰 Reward: ₹{reward}\n\n📝 Proof: {message.text}', reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    await message.answer('📤 Submission sent for admin review.', reply_markup=get_main_menu_keyboard())
    await state.clear()

@dp.callback_query(F.data.startswith("taskapprove:"))
async def approve_task(call: CallbackQuery):
    _, task_id, user_id, reward = call.data.split(":")
    task_id = int(task_id)
    user_id = int(user_id)
    reward = float(reward)
    
    async with db_pool.acquire() as conn:
        # Check task status to prevent double processing
        current_status = await conn.fetchval("SELECT status FROM tasks WHERE id=$1", task_id)
        if current_status != 'pending_review':
            await call.answer("⚠️ Task has already been processed!", show_alert=True)
            return

        async with conn.transaction():
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id=$2", reward, user_id)
            await conn.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1, $2, $3, $4)", user_id, "task", reward, f"Task #{task_id}")
            await conn.execute("DELETE FROM task_assignments WHERE task_id=$1", task_id)
            # Mark as completed so it NEVER goes back to the pool
            await conn.execute("UPDATE tasks SET status='completed' WHERE id=$1", task_id)
            
    await edit_admin_message(call, "✅ Task approved and balance credited.")
    try:
        await bot.send_message(user_id, f"🎉 Task approved!\n+₹{reward} added to your balance.")
    except:
        pass

@dp.callback_query(F.data.startswith("taskdecline:"))
async def decline_task(call: CallbackQuery, state: FSMContext):
    _, task_id, user_id = call.data.split(":")
    task_id = int(task_id)
    user_id = int(user_id)
    
    async with db_pool.acquire() as conn:
        # Check task status to prevent double processing
        current_status = await conn.fetchval("SELECT status FROM tasks WHERE id=$1", task_id)
        if current_status != 'pending_review':
            await call.answer("⚠️ Task has already been processed!", show_alert=True)
            return

    await state.set_state(AdminState.waiting_for_task_reject_reason)
    await state.update_data(
        task_id=task_id, 
        user_id=user_id, 
        admin_msg_id=call.message.message_id,
        is_photo=bool(call.message.photo)
    )
    await call.message.answer(f"❓ **Please reply with the reason for declining Task #{task_id}:**", parse_mode=ParseMode.MARKDOWN)
    await call.answer()

@dp.message(AdminState.waiting_for_task_reject_reason, ~F.text.startswith("/"))
async def process_task_reject_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data['task_id']
    user_id = data['user_id']
    admin_msg_id = data['admin_msg_id']
    is_photo = data['is_photo']
    reason = message.text.strip()

    async with db_pool.acquire() as conn:
        current_status = await conn.fetchval("SELECT status FROM tasks WHERE id=$1", task_id)
        if current_status == 'pending_review':
            async with conn.transaction():
                await conn.execute("DELETE FROM task_assignments WHERE task_id=$1", task_id)
                await conn.execute("UPDATE tasks SET status='available' WHERE id=$1", task_id)

    new_text = f"❌ <b>Task #{task_id} declined.</b>\n<b>Reason:</b> {reason}"
    try:
        if is_photo:
            await bot.edit_message_caption(chat_id=message.chat.id, message_id=admin_msg_id, caption=new_text, reply_markup=None, parse_mode=ParseMode.HTML)
        else:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=admin_msg_id, text=new_text, reply_markup=None, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"Error editing admin msg: {e}")

    try:
        await bot.send_message(user_id, f"❌ <b>Your submission for Task #{task_id} was declined.</b>\n\n💬 <b>Reason:</b> {reason}\n\n🔄 The task has been returned to the pool.", parse_mode=ParseMode.HTML)
    except:
        pass

    await message.answer("✅ Rejection reason recorded and user notified.")
    await state.clear()

# ============================================
# AUTO EXPIRE TASKS ENGINE
# ============================================

async def auto_expire_tasks():
    while True:
        try:
            async with db_pool.acquire() as conn:
                # Exclude tasks that are pending admin review
                rows = await conn.fetch('''
                    SELECT ta.task_id, ta.user_id, ta.assigned_at 
                    FROM task_assignments ta
                    JOIN tasks t ON ta.task_id = t.id
                    WHERE t.status != 'pending_review'
                ''')
                for r in rows:
                    task_id = r['task_id']
                    user_id = r['user_id']
                    assigned_time = r['assigned_at']
                    
                    if datetime.utcnow() - assigned_time > timedelta(minutes=30):
                        async with conn.transaction():
                            await conn.execute('DELETE FROM task_assignments WHERE task_id=$1', task_id)
                            await conn.execute("UPDATE tasks SET status='available' WHERE id=$1", task_id)
                        try:
                            await bot.send_message(user_id, f'⏰ Task #{task_id} has expired after 30 minutes.\nThe task was returned to the pool.\n\nUse "✍️ Get Task" to get a new task.', reply_markup=get_main_menu_keyboard())
                        except:
                            pass
        except Exception as e:
            print(f"Error in background task: {e}")
        await asyncio.sleep(60)

# ============================================
# LONG POLLING INITIALIZER WITH FLASK THREAD
# ============================================

async def main():
    await init_db()
    asyncio.create_task(auto_expire_tasks())
    
    server_thread = Thread(target=run_flask)
    server_thread.daemon = True
    server_thread.start()
    
    print('🤖 Bot connected to Supabase PostgreSQL and polling 24/7 on Render...')
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
