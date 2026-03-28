from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))

OZON_PHONE = os.getenv("OZON_PHONE")
OZON_EMAIL = os.getenv("OZON_EMAIL")
OZON_STORE_UUID = os.getenv("OZON_STORE_UUID")  # UUID страницы магазина в turbo-pvz.ozon.ru
OZON_URL = "https://turbo-pvz.ozon.ru"
OZON_CLAIMS_URL = f"{OZON_URL}/claims/list"
OZON_SESSION_FILE = "data/ozon_session.json"

TAX_RATE = float(os.getenv("TAX_RATE", "0.12"))  # УСН + НДС + пенсионный (с 2026)

TAX_RATES_BY_YEAR = {
    2025: 0.07,   # УСН 6% + 1% пенсионный
    2026: 0.12,   # УСН 6% + НДС 5% + 1% пенсионный
}

def get_tax_rate(year: int) -> float:
    """Возвращает налоговую ставку для конкретного года."""
    return TAX_RATES_BY_YEAR.get(year, TAX_RATE)

CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "120"))
ALERT_BEFORE_HOURS = int(os.getenv("ALERT_BEFORE_HOURS", "24"))
ALERT_URGENT_HOURS = int(os.getenv("ALERT_URGENT_HOURS", "2"))

DB_PATH = "data/pvz_bot.db"
