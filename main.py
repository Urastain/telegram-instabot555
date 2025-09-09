import os
import re
import logging
import asyncio
import threading
import requests
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN не найден в переменных окружения Render")

MAX_FILE_SIZE = 50 * 1024 * 1024

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is running on Render free tier!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)

class InstagramDownloaderPlaywright:
    async def extract_video_url(self, url: str) -> str | None:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-setuid-sandbox"
                    ]
                )
                page = await browser.new_page()
                await page.goto(url, timeout=20000)
                await page.wait_for_selector("video", timeout=15000)
                video_url = await page.eval_on_selector("video", "el => el.src")
                await browser.close()
                return video_url
        except Exception as e:
            logger.error(f"Ошибка Playwright: {e}")
            return None

def download_video(video_url, temp_file):
    with requests.get(video_url, stream=True, timeout=30) as r:
        r.raise_for_status()
        file_size = int(r.headers.get("Content-Length", 0))
        if file_size and file_size > MAX_FILE_SIZE:
            raise ValueError("Видео слишком большое для Telegram (>50MB).")
        with open(temp_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)
    return os.path.getsize(temp_file)

async def handle_instagram_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    url = update.message.text.strip()
    chat_id = update.effective_chat.id
    logger.info(f"Получена ссылка: {url}")

    pattern = r"^https?://(www\.)?instagram\.com/(p|reel)/[a-zA-Z0-9_-]+/?(\?.*)?$"
    if not re.match(pattern, url):
        return

    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение: {e}")

    status_msg = await update.effective_chat.send_message("🔄 Загружаю видео...")

    downloader = InstagramDownloaderPlaywright()
    video_url = await downloader.extract_video_url(url)

    if not video_url:
        await status_msg.edit_text("❌ Не удалось найти видео. Убедитесь, что пост общедоступен.")
        return

    temp_file = f"video_{chat_id}.mp4"

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, download_video, video_url, temp_file)

        file_size = os.path.getsize(temp_file)
        if file_size > MAX_FILE_SIZE:
            await status_msg.edit_text("❌ Видео слишком большое для Telegram (>50MB).")
        else:
            with open(temp_file, "rb") as video_file:
                await update.effective_chat.send_video(
                    video=video_file,
                    caption="✅ Вот ваше видео из Instagram.",
                    supports_streaming=True
                )
            await status_msg.delete()

    except ValueError as ve:
        await status_msg.edit_text(str(ve))
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        await status_msg.edit_text("❌ Произошла ошибка при загрузке видео.")
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)

def run_bot():
    app = Application.builder().token(TOKEN).build()
    filters_combined = filters.TEXT & filters.Regex(r"^https?://(www\.)?instagram\.com/(p|reel)/[a-zA-Z0-9_-]+")
    app.add_handler(MessageHandler(filters_combined, handle_instagram_link))
    logger.info("🤖 Бот запущен на Render (Web Service)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    run_bot()
