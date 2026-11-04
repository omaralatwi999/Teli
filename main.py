import logging
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from groq import AsyncGroq
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# إعداد السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# نخفي لوقز httpx عشان ما يظهر توكن البوت في الروابط
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# الموديل الأساسي (تقدر تغيره من Environment Variables باسم GROQ_MODEL)
# وإذا فشل يجرب الموديلات الاحتياطية بالترتيب
PRIMARY_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODELS = ["openai/gpt-oss-20b", "qwen/qwen3.6-27b"]
MODELS_TO_TRY = [PRIMARY_MODEL] + [m for m in FALLBACK_MODELS if m != PRIMARY_MODEL]


# خادم ويب وهمي لتلبية متطلبات Render Web Service وبقاء البوت شغالاً
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is active and running!")

    def log_message(self, format, *args):
        pass


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


# مفاتيح التشغيل من المتغيرات البيئية
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# تهيئة عميل Groq (async)
client = AsyncGroq(api_key=GROQ_API_KEY)

# التعليمات الأساسية للبوت
SYSTEM_PROMPT = (
    "أنت مساعد ذكي وودود، تجاوب على أسئلة المستخدم بشكل مفيد وصريح. "
    "تتكلم وتجيب باللهجة العامية، وطريقتك في الكلام عادية وطبيعية وكأنك تسولف مع خويك."
)


async def ask_groq(user_text: str) -> str:
    """يجرب الموديلات بالترتيب لين يشتغل واحد منها."""
    last_error = None
    for model in MODELS_TO_TRY:
        try:
            completion = await client.chat.completions.create(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                model=model,
            )
            return completion.choices[0].message.content or "ما قدرت أرد، جرب مرة ثانية."
        except Exception as e:
            logger.error(f"Model {model} failed: {e}")
            last_error = e
    raise last_error


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    first_name = update.effective_user.first_name if update.effective_user else "يا غالي"
    await update.message.reply_text(f"هلا والله {first_name}! أمرني وش بغيت تسأل عنه اليوم؟")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text

    try:
        bot_response = await ask_groq(user_text)

        # تيليجرام يقبل 4096 حرف كحد أقصى في الرسالة الواحدة
        for i in range(0, len(bot_response), 4000):
            await update.message.reply_text(bot_response[i:i + 4000])

    except Exception as e:
        logger.error(f"All models failed: {e}")
        await update.message.reply_text("حصل مشكلة بسيطة في الاتصال بالذكاء، جرب مرة ثانية.")


def main() -> None:
    if not TELEGRAM_TOKEN or not GROQ_API_KEY:
        print("خطأ: يجب ضبط مفاتيح TELEGRAM_BOT_TOKEN و GROQ_API_KEY")
        sys.exit(1)

    # تشغيل خادم الويب الوهمي في الخلفية لاجتياز اختبار المنصة
    threading.Thread(target=run_web_server, daemon=True).start()
    logger.info("Health check web server started successfully.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("البوت شغال الحين...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
