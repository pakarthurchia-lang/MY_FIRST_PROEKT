"""Speech-to-text via Groq Whisper API (free tier)."""
import config

if not config.GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY не задан. Получи бесплатный ключ на https://console.groq.com "
        "и добавь в .env"
    )

from groq import AsyncGroq

_client = AsyncGroq(api_key=config.GROQ_API_KEY)


async def transcribe(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe OGG audio bytes to Russian text using Groq Whisper."""
    transcript = await _client.audio.transcriptions.create(
        file=(filename, audio_bytes),
        model="whisper-large-v3",
        language="ru",
        response_format="text",
    )
    return str(transcript).strip()
