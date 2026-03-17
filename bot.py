import base64
import json
import logging

import anthropic
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    ANTHROPIC_API_KEY,
    BOT_TOKEN,
    CARD_NUMBER,
    PHONE_NUMBER,
    TICKET_PRICE,
)
from sheets import SheetsClient

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

sheets = SheetsClient()

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------
NAME, PAID_CHECK, RECEIPT, ALCOHOL, ALCOHOL_TEXT = range(5)


# ---------------------------------------------------------------------------
# Receipt verification via Claude Vision
# ---------------------------------------------------------------------------

def _check_receipt(image_bytes: bytes, mime: str = "image/jpeg") -> dict:
    """
    Returns a dict:
      valid        – bool
      reason       – human-readable error if not valid
      operation_id – transaction/operation number string
    """
    if not ANTHROPIC_API_KEY:
        return {"valid": False, "reason": "Проверка чеков не настроена. Обратись к организатору.", "operation_id": ""}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    b64 = base64.standard_b64encode(image_bytes).decode()

    prompt = f"""You are verifying a bank payment receipt. Carefully examine the image and answer the questions.

Return ONLY JSON, no extra text:
{{
  "is_receipt": true/false,
  "amount_ok": true/false,
  "recipient_ok": true/false,
  "amount_found": number,
  "operation_id": "transaction/document/operation number or empty string"
}}

Verification rules:
1. is_receipt = true if this is a screenshot of a bank transfer or payment receipt
2. amount_ok = true if the transfer amount is >= {TICKET_PRICE} rubles
3. recipient_ok = true if the recipient is a card ending in 5251 (full number {CARD_NUMBER}) OR phone number {PHONE_NUMBER} (may appear as +7 909 951-49-73 or 8-909-951-49-73)
4. amount_found = transfer amount in rubles (0 if not visible)
5. operation_id = operation number (may be labeled: operation number, ID, receipt #, document number, transaction)"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = response.content[0].text.strip()
        logger.info("Receipt response: %s", raw)

        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

    except Exception as exc:
        logger.error("Receipt check failed: %s", exc)
        return {"valid": False, "reason": "Не удалось прочитать чек. Пришли более чёткий скриншот.", "operation_id": ""}

    if not data.get("is_receipt"):
        return {"valid": False, "reason": "Это не похоже на чек банковского перевода. Пришли скриншот из банковского приложения.", "operation_id": ""}

    if not data.get("amount_ok"):
        amount = int(data.get("amount_found", 0))
        return {"valid": False, "reason": f"Сумма в чеке ({amount}₽) меньше стоимости билета ({TICKET_PRICE}₽).", "operation_id": ""}

    if not data.get("recipient_ok"):
        return {
            "valid": False,
            "reason": (
                f"Перевод сделан не на тот реквизит.\n\n"
                f"Переводи на карту:\n<code>{CARD_NUMBER}</code>\n"
                f"или на телефон:\n<code>{PHONE_NUMBER}</code>"
            ),
            "operation_id": "",
        }

    operation_id = data.get("operation_id", "").strip() or "UNKNOWN"
    return {"valid": True, "reason": "", "operation_id": operation_id}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Привет! Добро пожаловать!\n\n"
        "Введи своё имя и фамилию:"
    )
    return NAME


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["name"] = update.message.text.strip()

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, оплатил(а)", callback_data="paid_yes"),
        InlineKeyboardButton("❌ Нет, ещё нет",   callback_data="paid_no"),
    ]])
    await update.message.reply_text(
        f"Привет, <b>{context.user_data['name']}</b>! 😊\n\n"
        f"Ты уже оплатил(а) билет (<b>{TICKET_PRICE}₽</b>)?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return PAID_CHECK


async def cb_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "paid_yes":
        await query.edit_message_text("Отлично! 🙌 Пришли скриншот чека об оплате.")
    else:
        await query.edit_message_text(
            f"Переведи <b>{TICKET_PRICE}₽</b> на карту:\n"
            f"<code>{CARD_NUMBER}</code>\n\n"
            f"или по номеру телефона:\n"
            f"<code>{PHONE_NUMBER}</code>\n\n"
            "После оплаты пришли сюда скриншот чека — и мы всё оформим! 👇",
            parse_mode="HTML",
        )
    return RECEIPT


async def got_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        mime = "image/jpeg"
    elif update.message.document and update.message.document.mime_type.startswith("image/"):
        file = await update.message.document.get_file()
        mime = update.message.document.mime_type
    else:
        await update.message.reply_text("Пожалуйста, пришли скриншот (фото) чека об оплате.")
        return RECEIPT

    image_bytes = await file.download_as_bytearray()
    checking_msg = await update.message.reply_text("⏳ Проверяю чек…")

    result = _check_receipt(bytes(image_bytes), mime)

    if not result["valid"]:
        await checking_msg.edit_text(f"❌ {result['reason']}", parse_mode="HTML")
        return RECEIPT

    operation_id = result["operation_id"]

    # Check for duplicate operation number
    if operation_id != "UNKNOWN" and sheets.operation_exists(operation_id):
        await checking_msg.edit_text(
            "❌ Этот чек уже был использован для другой регистрации.\n"
            "Если ты считаешь, что это ошибка — обратись к организатору."
        )
        return RECEIPT

    context.user_data["operation_id"] = operation_id
    await checking_msg.delete()

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🍾 Да",  callback_data="alc_yes"),
        InlineKeyboardButton("🚫 Нет", callback_data="alc_no"),
    ]])
    await update.message.reply_text(
        f"✅ Чек принят! Оплата подтверждена.\n"
        f"Номер операции: <code>{operation_id}</code>\n\n"
        "Хочешь взять алкоголь для себя лично?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return ALCOHOL


async def cb_alcohol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "alc_yes":
        await query.edit_message_text(
            "Отлично! По вопросу алкоголя пиши сюда: @abbasov_rr 🍾\n\n"
            "Спасибо за покупку билета! 🎉 До встречи на мероприятии!"
        )
        sheets.log_participant(context.user_data["name"], context.user_data["operation_id"], alcohol="хочет (написал @abbasov_rr)")
        return ConversationHandler.END

    # No alcohol
    sheets.log_participant(context.user_data["name"], context.user_data["operation_id"], alcohol=None)
    await query.edit_message_text("Спасибо за покупку билета! 🎉 До встречи на мероприятии!")
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено. Напиши /start чтобы начать заново.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            NAME:         [MessageHandler(filters.TEXT & ~filters.COMMAND, got_name)],
            PAID_CHECK:   [CallbackQueryHandler(cb_paid, pattern="^paid_")],
            RECEIPT:      [MessageHandler(filters.PHOTO | filters.Document.IMAGE, got_receipt)],
            ALCOHOL:      [CallbackQueryHandler(cb_alcohol, pattern="^alc_")],
            ALCOHOL_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: ConversationHandler.END)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("Bot is running…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
