import logging
import os
import re
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
    "أنت بوت وصخ ولسانك طويل وأسلوبك شوارعي خشن، تسولف باللهجة الخليجية العامية فقط ومستحيل تتكلم فصحى أو رسمي. "
    "تستخدم ألفاظ الشارع والشتائم القوية بشكل طبيعي وبدون تلطيف، وتنادي المستخدم بألقاب مثل (يا ابن الحلال، يا حمار، يا خرب) حسب الجو. "
    "ما تتحسس من أي كلمة ولا تتفاجأ ولا تعتذر ولا تنصح ولا تحاضر ولا تقول (هذا ما يصير) أو (هذا مو لائق). "
    "ردودك قصيرة وحادة (سطر إلى ثلاثة أسطر) بدون مقدمات. "
    "إذا المستخدم سبّك أو استفزك، رد عليه فوراً بنفس مستواه وأقوى منه بشتائم وتطقيق قذر ولاذع، ولا تخليه يغلبك أبداً. "
    "تتذكر كلامه السابق وتستخدمه ضده في الرد. "
    "الاستثناء الوحيد: لا تستخدم شتائم عنصرية أو طائفية ولا تمس الدين أو العرق، اشتم الشخص نفسه وكلامه بس. "
    "وإذا سألك سؤال جاد (طب، قانون، تقنية، دراسة) جاوب بشكل مفيد ومختصر وبنفس اللهجة الوصخة."
)


# ذاكرة المحادثة لكل مستخدم (آخر رسائل فقط عشان ما يصرف رصيد كثير)
HISTORY = {}
MAX_HISTORY = 15


REFUSAL_MARKERS = ("i'm sorry", "i am sorry", "i can't help", "i cannot help", "i can’t help")
REFUSAL_REPLY = "هالسالفة مالي فيها خلق، غيّر الموضوع يالطيب."


def clean_reply(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    if not text:
        return "ما قدرت أرد، جرب مرة ثانية."
    if text.lower().startswith(REFUSAL_MARKERS):
        return REFUSAL_REPLY
    return text


async def ask_groq(messages: list) -> str:
    """يجرب الموديلات بالترتيب لين يشتغل واحد منها."""
    last_error = None
    for model in MODELS_TO_TRY:
        try:
            extra = {}
            if model.startswith("openai/gpt-oss"):
                # تفكير قليل = استهلاك أقل للرصيد وردود أسرع
                extra["reasoning_effort"] = "low"

            completion = await client.chat.completions.create(
                messages=messages,
                model=model,
                max_completion_tokens=500,
                temperature=0.9,
                **extra,
            )
            return clean_reply(completion.choices[0].message.content)
        except Exception as e:
            logger.error(f"Model {model} failed: {e}")
            last_error = e
    raise last_error


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    first_name = update.effective_user.first_name if update.effective_user else "يا غالي"
    await update.message.reply_text(f"هلا والله {first_name}! أمرني وش بغيت تسأل عنه اليوم؟")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text[:1000]
    user_id = update.effective_user.id if update.effective_user else 0
    history = HISTORY.setdefault(user_id, [])

    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [
            {"role": "user", "content": user_text}
        ]
        bot_response = await ask_groq(messages)

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": bot_response})
        del history[:-MAX_HISTORY]

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
