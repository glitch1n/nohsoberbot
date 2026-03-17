import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN                = os.getenv("BOT_TOKEN", "")
CARD_NUMBER              = os.getenv("CARD_NUMBER", "2200701988165251")
PHONE_NUMBER             = os.getenv("PHONE_NUMBER", "89099514973")
TICKET_PRICE             = int(os.getenv("TICKET_PRICE", "2200"))
ANTHROPIC_API_KEY        = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_CREDENTIALS_JSON  = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
GOOGLE_SHEET_ID          = os.getenv("GOOGLE_SHEET_ID", "1bDGSnQBPzsC9HS-nWcmkVjcYE3L3D6_FMD13dTcgcj8")
