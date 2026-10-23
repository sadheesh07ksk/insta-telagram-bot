import os
import shutil
import zipfile
import logging
import asyncio
import instaloader
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

user_state = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Instagram Downloader Bot!\n\n"
        "Send me a public Instagram username\n"
        "Example: natgeo  or  @natgeo"
    )


async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.message.text.strip().lstrip("@")
    user_state[chat_id] = {"username": username}

    keyboard = [
        [InlineKeyboardButton("📷 Photos Only", callback_data="photos")],
        [InlineKeyboardButton("🎬 Reels Only", callback_data="reels")],
        [InlineKeyboardButton("✨ Highlights Only", callback_data="highlights_menu")],
        [InlineKeyboardButton("📖 Stories Only", callback_data="stories")],
        [InlineKeyboardButton("📦 Download Everything", callback_data="all")],
    ]
    await update.message.reply_text(
        f"✅ Username: *@{username}*\n\nWhat do you want to download?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def make_progress_bar(current, total, width=18):
    filled = int(width * current / total) if total > 0 else 0
    bar = "█" * filled + "░" * (width - filled)
    percent = int(100 * current / total) if total > 0 else 0
    return f"[{bar}] {percent}% ({current}/{total})"


def get_highlight_names(username: str):
    L = instaloader.Instaloader(download_videos=False, save_metadata=False)
    try:
        profile = instaloader.Profile.from_username(L.context, username)
        return [h.title for h in L.get_highlights(profile)]
    except Exception:
        return []


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    choice = query.data

    if chat_id not in user_state:
        await query.edit_message_text("⚠️ Session expired. Please send the username again.")
        return

    username = user_state[chat_id]["username"]

    # Show highlight submenu
    if choice == "highlights_menu":
        await query.edit_message_text("⏳ Fetching highlight list...")
        highlights = get_highlight_names(username)

        if not highlights:
            await query.edit_message_text(
                "❌ No highlights found.\n"
                "Account may be private or has no highlights."
            )
            return

        keyboard = [
            [InlineKeyboardButton(f"🔖 {h}", callback_data=f"hl::{h}")]
            for h in highlights
        ]
        keyboard.append([InlineKeyboardButton("📦 All Highlights", callback_data="highlights")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back")])

        await query.edit_message_text(
            f"✨ Found *{len(highlights)}* highlights.\nChoose one to download:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # Back button
    if choice == "back":
        keyboard = [
            [InlineKeyboardButton("📷 Photos Only", callback_data="photos")],
            [InlineKeyboardButton("🎬 Reels Only", callback_data="reels")],
            [InlineKeyboardButton("✨ Highlights Only", callback_data="highlights_menu")],
            [InlineKeyboardButton("📖 Stories Only", callback_data="stories")],
            [InlineKeyboardButton("📦 Download Everything", callback_data="all")],
        ]
        await query.edit_message_text(
            f"✅ Username: *@{username}*\n\nWhat do you want to download?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # Single highlight selected
    if choice.startswith("hl::"):
        highlight_name = choice.split("hl::")[1]
        user_state[chat_id]["highlight_name"] = highlight_name
        choice = "single_highlight"

    user_state[chat_id]["choice"] = choice
    await query.edit_message_text(
        f"⏳ Starting download for *@{username}*...",
        parse_mode="Markdown"
    )

    await run_download(chat_id, username, choice, context)


async def run_download(chat_id, username, choice, context):
    base_dir = f"/tmp/{chat_id}_{username}"
    if os.path.exists(base_dir):
        shutil.rmtree(base_dir)
    os.makedirs(base_dir)

    folders = {
        "photos":     os.path.join(base_dir, "photos"),
        "reels":      os.path.join(base_dir, "reels"),
        "stories":    os.path.join(base_dir, "stories"),
        "highlights": os.path.join(base_dir, "highlights"),
    }
    for f in folders.values():
        os.makedirs(f, exist_ok=True)

    L = instaloader.Instaloader(
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        filename_pattern="{date_utc:%Y%m%d_%H%M%S}",
    )

    progress_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="📊 Preparing..."
    )

    async def update_progress(text):
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_msg.message_id,
                text=text,
                parse_mode="Markdown"
            )
        except Exception:
            pass

    try:
        await update_progress("🔍 Looking up profile...")
        profile = instaloader.Profile.from_username(L.context, username)
    except instaloader.exceptions.ProfileNotExistsException:
        await update_progress(f"❌ @{username} not found or is a private account.")
        return
    except Exception as e:
        await update_progress(f"❌ Error: {str(e)}")
        return

    highlight_name = user_state.get(chat_id, {}).get("highlight_name")

    try:
        # ── PHOTOS ──────────────────────────────────────
        if choice in ("photos", "all"):
            await update_progress("📷 Counting photos...")
            posts = [p for p in profile.get_posts()
                     if not (p.is_video and p.typename == "GraphVideo")]
            total = len(posts)
            if total == 0:
                await update_progress("📷 No photos found.")
                await asyncio.sleep(1)
            else:
                for i, post in enumerate(posts, 1):
                    bar = make_progress_bar(i, total)
                    await update_progress(f"📷 *Downloading Photos...*\n{bar}")
                    L.dirname_pattern = folders["photos"]
                    L.download_post(post, target=folders["photos"])
                    await asyncio.sleep(2)

        # ── REELS ────────────────────────────────────────
        if choice in ("reels", "all"):
            await update_progress("🎬 Counting reels...")
            reels = [p for p in profile.get_posts()
                     if p.is_video and p.typename == "GraphVideo"]
            total = len(reels)
            if total == 0:
                await update_progress("🎬 No reels found.")
                await asyncio.sleep(1)
            else:
                for i, post in enumerate(reels, 1):
                    bar = make_progress_bar(i, total)
                    await update_progress(f"🎬 *Downloading Reels...*\n{bar}")
                    L.dirname_pattern = folders["reels"]
                    L.download_post(post, target=folders["reels"])
                    await asyncio.sleep(2)

        # ── HIGHLIGHTS ───────────────────────────────────
        if choice in ("highlights", "all", "single_highlight"):
            await update_progress("✨ Loading highlights...")
            all_highlights = list(L.get_highlights(profile))
            for i, highlight in enumerate(all_highlights, 1):
                if choice == "single_highlight" and highlight.title != highlight_name:
                    continue
                hl_dir = os.path.join(folders["highlights"], highlight.title)
                os.makedirs(hl_dir, exist_ok=True)
                items = list(highlight.get_items())
                total = len(items)
                for j, item in enumerate(items, 1):
                    bar = make_progress_bar(j, total)
                    await update_progress(
                        f"✨ *Highlight: {highlight.title}*\n{bar}"
                    )
                    L.download_storyitem(item, target=hl_dir)
                    await asyncio.sleep(1)

        # ── STORIES ──────────────────────────────────────
        if choice in ("stories", "all"):
            await update_progress("📖 Fetching stories...")
            try:
                story_items = []
                for story in L.get_stories(userids=[profile.userid]):
                    story_items.extend(list(story.get_items()))
                total = len(story_items)
                if total == 0:
                    await update_progress("📖 No active stories found.")
                    await asyncio.sleep(1)
                else:
                    for i, item in enumerate(story_items, 1):
                        bar = make_progress_bar(i, total)
                        await update_progress(f"📖 *Downloading Stories...*\n{bar}")
                        L.download_storyitem(item, target=folders["stories"])
                        await asyncio.sleep(1)
            except Exception:
                await update_progress("⚠️ Stories unavailable (login required).")
                await asyncio.sleep(2)

        # ── CREATE ZIP ───────────────────────────────────
        await update_progress("📦 Creating ZIP file...")
        zip_path = f"{base_dir}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(base_dir):
                for file in files:
                    filepath = os.path.join(root, file)
                    arcname = os.path.relpath(filepath, os.path.dirname(base_dir))
                    zf.write(filepath, arcname)

        zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)

        if zip_size_mb > 50:
            await update_progress(
                f"⚠️ ZIP is *{zip_size_mb:.1f} MB* — too large for Telegram.\n"
                f"Please try downloading a specific section instead of Everything."
            )
        else:
            await update_progress(
                f"✅ *Done!* ZIP size: {zip_size_mb:.1f} MB\n📤 Sending file..."
            )
            with open(zip_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=f"{username}_instagram.zip",
                    caption=f"✅ @{username} — Download complete!\n📦 {zip_size_mb:.1f} MB"
                )

    except Exception as e:
        await update_progress(f"❌ Something went wrong:\n{str(e)}")

    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
        if os.path.exists(f"{base_dir}.zip"):
            os.remove(f"{base_dir}.zip")


# ── App Setup ─────────────────────────────────────────────────
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(handle_choice))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

print("✅ Bot is running...")
app.run_polling()
