import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_CREDENTIALS_JSON, GOOGLE_SHEET_ID, TICKET_PRICE

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = ["Time", "Full Name", "Ticket Paid", "Transaction ID", "Alcohol", "Amount (₽)"]


def _get_worksheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(GOOGLE_SHEET_ID)
    return sheet.sheet1


def _ensure_headers(ws):
    """Create headers and summary row if sheet is empty."""
    all_values = ws.get_all_values()
    if not all_values or all_values[0] != HEADERS:
        ws.clear()
        # Header row
        ws.append_row(HEADERS)
        # Format header bold (basic)
        ws.format("A1:F1", {
            "backgroundColor": {"red": 0.267, "green": 0.447, "blue": 0.769},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "horizontalAlignment": "CENTER"
        })
        # Summary row
        ws.append_row(["", "TOTAL revenue:", "", "", "", f"=SUMIF(C3:C10000,\"Yes\",F3:F10000)"])
        ws.format("A2:F2", {"textFormat": {"bold": True}})
        logger.info("Headers created in Google Sheet")


class SheetsClient:
    def __init__(self):
        try:
            ws = _get_worksheet()
            _ensure_headers(ws)
            logger.info("Google Sheet ready: %s", GOOGLE_SHEET_ID)
        except Exception as exc:
            logger.error("Google Sheet init failed: %s", exc)

    def operation_exists(self, operation_id: str) -> bool:
        """Return True if this operation number is already recorded."""
        try:
            ws = _get_worksheet()
            all_values = ws.get_all_values()
            for row in all_values[2:]:  # skip header + summary
                if len(row) > 3 and str(row[3]).strip() == operation_id:
                    return True
        except Exception as exc:
            logger.error("operation_exists check failed: %s", exc)
        return False

    def log_participant(self, full_name: str, operation_id: str, alcohol=None):
        try:
            ws = _get_worksheet()
            _ensure_headers(ws)
            ws.append_row([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                full_name,
                "Yes",
                operation_id,
                alcohol or "—",
                TICKET_PRICE,
            ])
            logger.info("Logged: %s | op=%s | alcohol=%s", full_name, operation_id, alcohol)
        except Exception as exc:
            logger.error("log_participant failed: %s", exc)
