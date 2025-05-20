import os
import logging
import asyncio
import json
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler
)
from functools import partial

# Carga .env y configura logging
load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.info(f"GEMINI_API_KEY cargada: {os.getenv('GEMINI_API_KEY')[:6]}…")

# Carga FAQs
with open("faqs.json") as f:
    FAQS = json.load(f)

# Configura Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.0-flash')

# Token de Telegram y chat de soporte
TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPPORT_CHAT_ID = os.getenv("SUPPORT_CHAT_ID")


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
    logging.info(f"[Gemini] Prompt: {prompt!r}")
    response = model.generate_content(prompt)
    logging.info(f"[Gemini] Response: {response!r}")
    return response.text


def needs_human_escalation(response: str) -> bool:
    triggers = ["transferir", "humano", "agente", "no sé", "no puedo"]
    return any(t in response.lower() for t in triggers)


async def escalate_to_human(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    user = update.effective_user
    message = (
        f"🚨 Escalamiento requerido\n"
        f"Usuario: {user.mention_markdown()}\n"
        f"Consulta: {query}"
    )
    await context.bot.send_message(
        chat_id=SUPPORT_CHAT_ID,
        text=message,
        parse_mode="Markdown"
    )
    await update.message.reply_text("🔹 Tu consulta ha sido elevada a nuestro equipo de soporte.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.lower()
    response = check_faqs(user_input)

    if not response:
        try:
            # Obtengo el bucle activo en lugar de context.application.loop
            loop = asyncio.get_running_loop()  
            # Ejecuto la función bloqueante en un thread pool
            response = await loop.run_in_executor(
                None, 
                lambda: generate_gemini_response(user_input)
            )
            if needs_human_escalation(response):
                await escalate_to_human(update, context, user_input)
                response = "⏳ Un agente humano se contactará contigo en breve."
        except Exception:
            logging.exception("Error llamando a Gemini")
            response = "⚠️ Lo siento, estoy teniendo dificultades técnicas."

    await update.message.reply_text(response)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("📌 FAQs", callback_data="faqs"),
            InlineKeyboardButton("🧑💻 Agente Humano", callback_data="human")
        ]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    text = (
        "¡Hola! Soy tu asistente virtual. ¿En qué puedo ayudarte?\n"
        "- Escribe tu pregunta\n"
        "- Usa /help para ayuda\n"
        "- Usa /human para hablar con un agente"
    )
    await update.message.reply_text(text, reply_markup=markup)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "human":
        await escalate_to_human(update, context, "Solicitud directa de agente")
    elif query.data == "faqs":
        faq_list = "\n".join(f"• {faq['question']}" for faq in FAQS)
        await query.message.edit_text(f"📚 FAQs Disponibles:\n{faq_list}")


if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("human", escalate_to_human))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()
