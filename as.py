import asyncio
from datetime import datetime, timedelta
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiosqlite
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
    InlineKeyboardButton
)
import os
from threading import Thread
from flask import Flask

# ============================================
# CONFIGURATION & INITIALIZATION
# ============================================

BOT_TOKEN = os.environ.get('BOT_TOKEN', '8970788656:AAGmGCBKEAhNSpaW0YTv7zztcLPTTQwYRGo')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6237763207))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

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
    withdrawing = State()
    submitting_task = State()

# ============================================
# ADMIN UPDATE ALL TASKS REWARD
# ============================================

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
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("UPDATE tasks SET reward=?", (new_reward,))
            await db.commit()
        await message.answer(f"💰 **Success!** The reward for **ALL** tasks has been updated to **₹{new_reward:.2f}**.")
    except ValueError:
        await message.answer("❌ Invalid reward amount. Please provide a valid number. (e.g., `/updateallrewards 35`)")
    except Exception as e:
        await message.answer(f"❌ An error occurred: {str(e)}")

# ============================================
# ADMIN REMOVE TASK
# ============================================

@dp.message(Command("removetask"))
async def remove_task(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    if not command.args:
        await message.answer("❌ Missing Task ID!\n\nUsage:\n`/removetask 3`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        task_id = int(command.args.strip())
        async with aiosqlite.connect("bot.db") as db:
            cursor = await db.execute("SELECT id FROM tasks WHERE id=?", (task_id,))
            task = await cursor.fetchone()
            if not task:
                await message.answer(f"❌ Task #{task_id} does not exist in the database.")
                return
            await db.execute("DELETE FROM task_assignments WHERE task_id=?", (task_id,))
            await db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            await db.commit()
        await message.answer(f"🗑️ **Task #{task_id}** has been permanently removed from the bot.")
    except ValueError:
        await message.answer("❌ Invalid Task ID. Please provide a valid number. (e.g., `/removetask 3`)")
    except Exception as e:
        await message.answer(f"❌ An error occurred: {str(e)}")

# ============================================
# DATABASE
# ============================================

async def init_db():
    async with aiosqlite.connect('bot.db') as db:
        await db.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0)')
        await db.execute('CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, amount REAL, note TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
        await db.execute('CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, upi TEXT, status TEXT DEFAULT "pending", created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
        await db.execute('CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, details TEXT, reward REAL, status TEXT DEFAULT "available")')
        await db.execute('CREATE TABLE IF NOT EXISTS task_assignments (task_id INTEGER UNIQUE, user_id INTEGER, assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
        await db.commit()

# ============================================
# HELPERS
# ============================================

async def ensure_user(user_id: int):
    async with aiosqlite.connect("bot.db") as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
        await db.commit()

async def get_balance(user_id: int) -> float:
    await ensure_user(user_id)
    async with aiosqlite.connect("bot.db") as db:
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else 0

# ============================================
# START & GLOBAL CANCEL
# ============================================

@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await ensure_user(message.from_user.id)
    text = (
        "🔥Gmail EarneX Wallet Bot\n\n"
        "📋Commands:\n"
        "/balance - Check balance\n"
        "/sell - Sell old Gmail account\n"
        "/task - Get random task\n"
        "/mytask - Show current task\n"
        "/submit - Submit completed task\n"
        "/cancel_task - Cancel current task\n"
        "/withdraw - Request withdrawal\n"
        "/history - Transaction history\n"
        "/cancel - Cancel current operation"
    )
    await message.answer(text, entities=[MessageEntity(type='custom_emoji', offset=0, length=2, custom_emoji_id='5877680341057015789')])

@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Current operation cancelled.")

# ============================================
# BALANCE
# ============================================

@dp.message(Command("balance"))
async def balance(message: Message):
    bal = await get_balance(message.from_user.id)
    await message.answer(f"💰 Your Balance: ₹{bal:.2f}")

# ============================================
# HISTORY
# ============================================

@dp.message(Command("history"))
async def history(message: Message):
    async with aiosqlite.connect("bot.db") as db:
        cur = await db.execute("SELECT type, amount, note, created_at FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 10", (message.from_user.id,))
        rows = await cur.fetchall()
    if not rows:
        await message.answer("📭 No transactions found.")
        return
    text = "📜 <b>Last Transactions</b>\n\n"
    for t_type, amount, note, created_at in rows:
        sign = "+" if amount >= 0 else ""
        text += f"• {sign}₹{amount:.2f} | {t_type}\n{note}\n{created_at}\n\n"
    await message.answer(text, parse_mode=ParseMode.HTML)

# ============================================
# SELL SYSTEM
# ============================================

@dp.message(Command("sell"))
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
    await message.answer("✅ Your item has been sent for admin review.")
    await state.clear()

@dp.callback_query(F.data.startswith("sellapprove:"))
async def approve_sell(call: CallbackQuery):
    _, user_id, amount = call.data.split(":")
    user_id = int(user_id)
    amount = float(amount)
    async with aiosqlite.connect("bot.db") as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
        await db.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES (?, ?, ?, ?)", (user_id, "sell", amount, "Gmail sell approved"))
        await db.commit()
    await bot.send_message(user_id, f"🎉 Sell approved!\n+₹{amount} added to your balance.")
    await call.message.edit_text("✅ Sell approved and balance credited.")

@dp.callback_query(F.data.startswith("selldecline:"))
async def decline_sell(call: CallbackQuery):
    _, user_id = call.data.split(":")
    user_id = int(user_id)
    await bot.send_message(user_id, "❌ Your sell request was declined.")
    await call.message.edit_text("❌ Sell request declined.")

# ============================================
# WITHDRAWAL
# ============================================

@dp.message(Command("withdraw"))
async def withdraw(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(UserState.withdrawing)
    await message.answer('💸 Withdrawal Request\n\n📌 Minimum Withdrawal: ₹150\n\nSend in this format:\n\nAmount: 150\nUPI: yourupi@upi')

@dp.message(UserState.withdrawing, F.text, ~F.text.startswith("/"))
async def handle_withdraw(message: Message, state: FSMContext):
    try:
        lines = message.text.split('\n')
        amount = float(lines[0].lower().split('amount:')[1].strip())
        upi = lines[1].lower().split('upi:')[1].strip()
    except:
        await message.answer('❌ Invalid format.\n\nUse this format:\nAmount: 150\nUPI: yourupi@upi')
        return

    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute('SELECT balance FROM users WHERE user_id=?', (message.from_user.id,))
        row = await cursor.fetchone()
        balance = row[0] if row else 0

    MIN_WITHDRAW = 150
    if amount < MIN_WITHDRAW:
        await message.answer(f'❌ Minimum withdrawal is ₹{MIN_WITHDRAW}\nPlease withdraw ₹{MIN_WITHDRAW} or more.')
        return
    if amount > balance:
        await message.answer(f'❌ Insufficient balance.\nYour balance: ₹{balance:.2f}')
        return

    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute('INSERT INTO withdrawals(user_id, amount, upi) VALUES (?, ?, ?)', (message.from_user.id, amount, upi))
        withdraw_id = cursor.lastrowid
        await db.commit()

    kb = InlineKeyboardBuilder()
    kb.button(text='💸 Pay', callback_data=f'pay:{withdraw_id}:{message.from_user.id}:{amount}')
    kb.button(text='❌ Reject', callback_data=f'reject:{withdraw_id}:{message.from_user.id}')
    await bot.send_message(ADMIN_ID, f'💰 WITHDRAWAL REQUEST #{withdraw_id}\n\n👤 @{message.from_user.username}\n🆔 {message.from_user.id}\n💵 Amount: ₹{amount:.2f}\n🏦 UPI: {upi}', reply_markup=kb.as_markup())
    await state.clear()
    await message.answer('⏳ Withdrawal request sent to admin.')

@dp.callback_query(F.data.startswith("pay:"))
async def pay_withdraw(call: CallbackQuery):
    _, withdrawal_id, user_id, amount = call.data.split(":")
    withdrawal_id = int(withdrawal_id)
    user_id = int(user_id)
    amount = float(amount)
    async with aiosqlite.connect("bot.db") as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
        await db.execute("UPDATE withdrawals SET status='paid' WHERE id=?", (withdrawal_id,))
        await db.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES (?, ?, ?, ?)", (user_id, "withdrawal", -amount, "Withdrawal paid"))
        await db.commit()
    await bot.send_message(user_id, f"🎉 Withdrawal of ₹{amount} has been paid.")
    await call.message.edit_text("✅ Withdrawal marked as paid.")

@dp.callback_query(F.data.startswith("reject:"))
async def reject_withdraw(call: CallbackQuery):
    _, withdrawal_id, user_id = call.data.split(":")
    withdrawal_id = int(withdrawal_id)
    user_id = int(user_id)
    async with aiosqlite.connect("bot.db") as db:
        await db.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (withdrawal_id,))
        await db.commit()
    await bot.send_message(user_id, "❌ Your withdrawal request was rejected.")
    await call.message.edit_text("❌ Withdrawal rejected.")

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
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
            await db.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES (?, ?, ?, ?)", (user_id, "admin_add", amount, "Admin balance add"))
            await db.commit()
        await message.answer(f"✅ Added ₹{amount} to {user_id}")
        await bot.send_message(user_id, f"💰 Admin added ₹{amount} to your balance.")
    except:
        await message.answer("Usage: /add USER_ID AMOUNT")

@dp.message(Command("cut"))
async def cut_balance(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, user_id, amount = message.text.split()
        user_id = int(user_id)
        amount = float(amount)
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
            await db.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES (?, ?, ?, ?)", (user_id, "admin_cut", -amount, "Admin balance cut"))
            await db.commit()
        await message.answer(f"✅ Cut ₹{amount} from {user_id}")
        await bot.send_message(user_id, f"⚠️ Admin deducted ₹{amount} from your balance.")
    except:
        await message.answer("Usage: /cut USER_ID AMOUNT")

@dp.message(Command("addtask"))
async def add_task(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    if not command.args:
        await message.answer("❌ Missing details!\nUsage: `/addtask username@gmail.com password123`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        args = command.args.split()
        if len(args) < 2:
            await message.answer("❌ You must provide both a Gmail address and a password.")
            return
        username = args[0]
        password = args[1]
        default_reward = 50.0 
        title = f"Login to {username}"
        details = f"Email: {username} | Pass: {password}"
        async with aiosqlite.connect("bot.db") as db:
            await db.execute("INSERT INTO tasks (title, details, reward) VALUES (?, ?, ?)", (title, details, default_reward))
            await db.commit()
        await message.answer(f"✅ **Task Added Successfully!**\n\n💰 **Reward:** ₹{default_reward}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await message.answer(f"❌ Error creating task: {str(e)}")

# ============================================
# TASK ENGINE
# ============================================

@dp.message(Command('task'))
async def get_task(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute('SELECT task_id, assigned_at FROM task_assignments WHERE user_id=?', (user_id,))
        existing = await cursor.fetchone()
        if existing:
            task_id, assigned_at = existing
            try:
                assigned_time = datetime.strptime(assigned_at, '%Y-%m-%d %H:%M:%S.%f')
            except:
                assigned_time = datetime.strptime(assigned_at, '%Y-%m-%d %H:%M:%S')
            expire_time = assigned_time + timedelta(minutes=30)
            remaining = expire_time - datetime.utcnow()
            if remaining.total_seconds() > 0:
                mins = int(remaining.total_seconds() // 60)
                secs = int(remaining.total_seconds() % 60)
                await message.answer(f'⚠️ You already have a task.\n⏰ Time remaining: {mins}m {secs}s\n\nUse /mytask or /submit')
                return
            await db.execute('DELETE FROM task_assignments WHERE user_id=?', (user_id,))
            await db.execute('UPDATE tasks SET status=? WHERE id=?', ('available', task_id))
            await db.commit()

        cursor = await db.execute("SELECT id, title, details, reward FROM tasks WHERE status='available' ORDER BY RANDOM() LIMIT 1")
        task = await cursor.fetchone()
        if not task:
            await message.answer('📭 No tasks available right now.')
            return
        task_id, title, details, reward = task
        await db.execute("UPDATE tasks SET status='assigned' WHERE id=?", (task_id,))
        await db.execute('INSERT INTO task_assignments(task_id, user_id) VALUES (?, ?)', (task_id, user_id))
        await db.commit()

    try:
        parts = details.split(" | ")
        username = parts[0].replace("Email: ", "").strip()
        password = parts[1].replace("Pass: ", "").strip()
    except:
        username = title.replace("Login to ", "")
        password = "See Admin"

    await message.answer(f"🎯 **Task #{task_id}**\n\n📝 **Email:** {username} | **Password:** `{password}`\n💰 **Reward:** ₹{reward}\n\n⏰ You have ONLY 30 MINUTES to complete this task.", parse_mode=ParseMode.MARKDOWN)

@dp.message(Command('mytask'))
async def my_task(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    async with aiosqlite.connect('bot.db') as db:
        cursor = await db.execute('SELECT t.id, t.title, t.details, t.reward, a.assigned_at FROM tasks t JOIN task_assignments a ON t.id = a.task_id WHERE a.user_id=?', (user_id,))
        task = await cursor.fetchone()
    if not task:
        await message.answer('📭 You have no assigned task.')
        return
    task_id, title, details, reward, assigned_at = task
    try:
        assigned_time = datetime.strptime(assigned_at, '%Y-%m-%d %H:%M:%S.%f')
    except:
        assigned_time = datetime.strptime(assigned_at, '%Y-%m-%d %H:%M:%S')
    expire_time = assigned_time + timedelta(minutes=30)
    remaining = expire_time - datetime.utcnow()
    total_seconds = int(remaining.total_seconds())
    if total_seconds <= 0:
        await message.answer('⏰ Your task has expired.')
        return
    mins = total_seconds // 60
    secs = total_seconds % 60
    try:
        parts = details.split(" | ")
        username = parts[0].replace("Email: ", "").strip()
        password = parts[1].replace("Pass: ", "").strip()
    except:
        username = title.replace("Login to ", "")
        password = "See Admin"
    await message.answer(f"🎯 **Your Current Task**\n\n🆔 #{task_id}\n📝 **Email:** {username} | **Password:** `{password}`\n💰 **Reward:** ₹{reward}\n\n⏰ Time Remaining: {mins}m {secs}s", parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("cancel_task"))
async def cancel_task(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    async with aiosqlite.connect("bot.db") as db:
        cursor = await db.execute('SELECT task_id FROM task_assignments WHERE user_id=?', (user_id,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("❌ You don't have any active task to cancel.")
            return
        task_id = row[0]
        await db.execute('DELETE FROM task_assignments WHERE user_id=?', (user_id,))
        await db.execute("UPDATE tasks SET status='available' WHERE id=?", (task_id,))
        await db.commit()
    await message.answer(f"✅ Task #{task_id} has been cancelled and returned to the pool.")

# ============================================
# SUBMIT TASK
# ============================================

@dp.message(Command('submit'))
async def submit_task(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    async with aiosqlite.connect('bot.db') as db:
        cur = await db.execute('SELECT task_id FROM task_assignments WHERE user_id=?', (user_id,))
        row = await cur.fetchone()
    if not row:
        await message.answer('❌ You do not have any active task.')
        return
    await state.set_state(UserState.submitting_task)
    await message.answer('📤 Send screenshot or proof of completed task.')

@dp.message(UserState.submitting_task, ~F.text.startswith("/"))
async def handle_task_submission(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with aiosqlite.connect('bot.db') as db:
        cur = await db.execute('SELECT t.id, t.title, t.reward FROM task_assignments ta JOIN tasks t ON ta.task_id = t.id WHERE ta.user_id=?', (user_id,))
        task = await cur.fetchone()
    if not task:
        await state.clear()
        await message.answer('❌ No active task found.')
        return
    task_id, title, reward = task
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='✅ Approve', callback_data=f'taskapprove:{task_id}:{user_id}:{reward}'),
        InlineKeyboardButton(text='❌ Decline', callback_data=f'taskdecline:{task_id}:{user_id}')
    ]])
    if message.photo:
        await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, caption=f'📤 **Task Submission**\n\n👤 User: @{message.from_user.username}\n🆔 Task #{task_id}\n📌 {title}\n💰 Reward: ₹{reward}', reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await bot.send_message(ADMIN_ID, f'📤 **Task Submission**\n\n👤 User: @{message.from_user.username}\n🆔 Task #{task_id}\n📌 {title}\n💰 Reward: ₹{reward}\n\n📝 Proof: {message.text}', reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    await message.answer('📤 Submission sent for admin review.')
    await state.clear()

@dp.callback_query(F.data.startswith("taskapprove:"))
async def approve_task(call: CallbackQuery):
    _, task_id, user_id, reward = call.data.split(":")
    task_id = int(task_id)
    user_id = int(user_id)
    reward = float(reward)
    async with aiosqlite.connect("bot.db") as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (reward, user_id))
        await db.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES (?, ?, ?, ?)", (user_id, "task", reward, f"Task #{task_id}"))
        await db.execute("DELETE FROM task_assignments WHERE task_id=?", (task_id,))
        await db.execute("UPDATE tasks SET status='completed' WHERE id=?", (task_id,))
        await db.commit()
    await bot.send_message(user_id, f"🎉 Task approved!\n+₹{reward} added to your balance.")
    await call.message.edit_text("✅ Task approved and balance credited.")

@dp.callback_query(F.data.startswith("taskdecline:"))
async def decline_task(call: CallbackQuery):
    _, task_id, user_id = call.data.split(":")
    task_id = int(task_id)
    user_id = int(user_id)
    async with aiosqlite.connect("bot.db") as db:
        await db.execute("DELETE FROM task_assignments WHERE task_id=?", (task_id,))
        await db.execute("UPDATE tasks SET status='available' WHERE id=?", (task_id,))
        await db.commit()
    await bot.send_message(user_id, "❌ Task submission declined. The task has been returned to the pool.")
    await call.message.edit_text("❌ Task declined and unlocked.")

# ============================================
# AUTO EXPIRE TASKS ENGINE (UTC CORRECTED)
# ============================================
async def auto_expire_tasks():
    while True:
        try:
            async with aiosqlite.connect('bot.db') as db:
                cursor = await db.execute('SELECT task_id, user_id, assigned_at FROM task_assignments')
                rows = await cursor.fetchall()
                for task_id, user_id, assigned_at in rows:
                    try:
                        assigned_time = datetime.strptime(assigned_at, '%Y-%m-%d %H:%M:%S.%f')
                    except:
                        assigned_time = datetime.strptime(assigned_at, '%Y-%m-%d %H:%M:%S')
                    if datetime.utcnow() - assigned_time > timedelta(minutes=30):
                        await db.execute('DELETE FROM task_assignments WHERE task_id=?', (task_id,))
                        await db.execute("UPDATE tasks SET status='available' WHERE id=?", (task_id,))
                        await db.commit()
                        try:
                            await bot.send_message(user_id, f'⏰ Task #{task_id} has expired after 30 minutes.\nThe task was returned to the pool.\n\nUse /task to get a new task.')
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
    
    # Start the Flask web server in a background thread to satisfy Render
    server_thread = Thread(target=run_flask)
    server_thread.daemon = True
    server_thread.start()
    
    print('🤖 Bot is polling 24/7 inside Render Free Web Service...')
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
