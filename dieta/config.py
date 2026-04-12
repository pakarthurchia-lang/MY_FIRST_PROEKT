import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
FATSECRET_CLIENT_ID: str = os.getenv("FATSECRET_CLIENT_ID", "")
FATSECRET_CLIENT_SECRET: str = os.getenv("FATSECRET_CLIENT_SECRET", "")
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "dieta.db")
