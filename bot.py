import os
import logging
import asyncio
import json

from flask import Flask, request, abort     # ← importa request y abort
from dotenv import load_dotenv

import google.generativeai as genai
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# ─── 1. Carga de configuración ───────────────────────────────────────
load_dotenv()
TOKEN           = os.getenv("TELEGRAM_TOKEN")
SUPPORT_CHAT_ID = os.getenv("SUPPORT_CHAT_ID")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")

# Puerto y URL base para webhooks
PORT     = int(os.environ.get("PORT", 5000))
# RENDER_EXTERNAL_URL la inyecta Render; si no existe, usamos localhost
BASE_URL = os.getenv("RENDER_EXTERNAL_URL") or f"http://localhost:{PORT}"

# Sólo comprobamos las tres variables críticas aquí
if not all([TOKEN, SUPPORT_CHAT_ID, GEMINI_API_KEY]):
    raise RuntimeError("Faltan variables: TELEGRAM_TOKEN, SUPPORT_CHAT_ID o GEMINI_API_KEY")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ─── 2. Carga de FAQs ────────────────────────────────────────────────
with open(os.path.join(os.path.dirname(__file__), "faqs.json"), encoding="utf-8") as f:
    FAQS = json.load(f)

# ─── 3. Configuración de Gemini ──────────────────────────────────────
genai.configure(api_key=GEMINI_API_KEY)
gi_model = genai.GenerativeModel('gemini-2.0-flash')

def check_faqs(query: str) -> str:
    for faq in FAQS:
        if any(kw in query for kw in faq["keywords"]):
            return faq["answer"]
    return ""

def generate_gemini_response(query: str) -> str:
    prompt = (
        "Eres un asistente virtual de servicio al cliente.\n"
        "Responde de manera clara y concisa en español.\n"
        "Si no sabes la respuesta, di que transferirás a un humano.\n\n"
        f"Pregunta: {query}\n"
        "Respuesta:"
    )
    logger.info(f"[Gemini] Prompt: {prompt!r}")
    resp = gi_model.generate_content(prompt)
    logger.info(f"[Gemini] Response: {resp!r}")
    return resp.text

def needs_human_escalation(response: str) -> bool:
    triggers = ["transferir", "humano", "agente", "no sé", "no puedo"]
    return any(t in response.lower() for t in triggers)

# ─── 4. Creación de la aplicación PTB ────────────────────────────────
application = (
    ApplicationBuilder()
    .token(TOKEN)
    .build()
)

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[
        InlineKeyboardButton("📌 FAQs", callback_data="faqs"),
        InlineKeyboardButton("🧑💻 Agente Humano", callback_data="human")
    ]]
    await update.message.reply_text(
        "¡Hola! Soy tu asistente virtual. ¿En qué puedo ayudarte?\n"
        "- Escribe tu pregunta\n"
        "- Usa /help para ayuda\n"
        "- Usa /human para hablar con un agente",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def escalate_to_human(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str = None):
    user = update.effective_user
    msg = (
        f"🚨 Escalamiento requerido\n"
        f"Usuario: {user.mention_markdown()}\n"
        f"Consulta: {query or update.message.text}"
    )
    await context.bot.send_message(
        chat_id=SUPPORT_CHAT_ID,
        text=msg,
        parse_mode="Markdown"
    )
    await update.message.reply_text("🔹 Tu consulta ha sido elevada a nuestro equipo de soporte.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.lower()
    response = check_faqs(user_input)
    if not response:
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, lambda: generate_gemini_response(user_input)
            )
            if needs_human_escalation(response):
                await escalate_to_human(update, context, user_input)
                response = "⏳ Un agente humano se contactará contigo en breve."
        except Exception:
            logger.exception("Error llamando a Gemini")
            response = "⚠️ Lo siento, estoy teniendo dificultades técnicas."
    await update.message.reply_text(response)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "human":
        await escalate_to_human(update, context, None)
    else:  # "faqs"
        faq_list = "\n".join(f"• {f['question']}" for f in FAQS)
        await query.message.edit_text(f"📚 FAQs Disponibles:\n{faq_list}")

# Registrar handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("human", escalate_to_human))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(CallbackQueryHandler(button_handler))

# ─── 5. Flask y webhook ────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/", methods=["GET"])
def health_check():
    return "🤖 Bot en línea", 200


@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") != "application/json":
        abort(400)
    update = Update.de_json(request.get_json(force=True), application.bot)
    # Procesar sin bloquear el hilo de Flask
    asyncio.create_task(application.process_update(update))
    return "OK"

# ─── 6. Inicio local y configuración del webhook ────────────────────────
if __name__ == "__main__":
    # Registro del webhook en Telegram
    webhook_url = f"{BASE_URL}/webhook/{TOKEN}"
    application.bot.set_webhook(webhook_url)  # PTB v20→ .bot.set_webhook() :contentReference[oaicite:0]{index=0}
    
    # Ejecuta Flask en el puerto asignado por Render o 5000 local
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
