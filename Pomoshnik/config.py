import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
WATER_LOGIN = os.environ["WATER_LOGIN"]
WATER_PASSWORD = os.environ["WATER_PASSWORD"]
WATER_CITY = os.getenv("WATER_CITY", "Ростов-на-Дону")
WATER_HEADLESS = os.getenv("WATER_HEADLESS", "true").lower() == "true"
