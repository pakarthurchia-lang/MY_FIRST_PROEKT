"""
Voice transcription (faster-whisper, local) + natural-language order parser.
Model is loaded once at first use and reused across calls.
Requires: pip install faster-whisper
          system ffmpeg  (brew install ffmpeg on macOS)
"""

import re
import os
import asyncio
import logging
import urllib.request

# Windows читает системный SOCKS4-прокси (VPN-клиент) из реестра через
# urllib.request.getproxies(), и httpx (используется huggingface_hub при
# первой проверке модели) не умеет работать со схемой socks4 — падает с
# "Unknown scheme for proxy URL". Отключаем определение прокси полностью.
urllib.request.getproxies = lambda: {}
for _v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "*"

from faster_whisper import WhisperModel
from products import find_product

log = logging.getLogger(__name__)

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        log.info("Loading Whisper model 'small' (first run may take a moment)…")
        _model = WhisperModel("small", device="cpu", compute_type="int8")
        log.info("Whisper model loaded.")
    return _model


async def transcribe(audio_path: str) -> str:
    """Transcribe an audio file (ogg/mp3/wav/…) to Russian text."""
    loop = asyncio.get_event_loop()

    def _run() -> str:
        model = _get_model()
        segments, _ = model.transcribe(audio_path, language="ru", beam_size=5)
        return " ".join(s.text.strip() for s in segments).strip()

    return await loop.run_in_executor(None, _run)


# ── order intent parser ────────────────────────────────────────────────────

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_MONTH_PAT = "|".join(_MONTHS)


def parse_water_command(text: str) -> dict:
    """
    Extract qty, date, and product from a voice command like:
      "Закажи 2 бутылки Элитная 19 литров завтра"

    Returns {"qty": int, "date": str | None, "product_key": str, "product": dict}
    """
    t = text.lower().strip()

    # ── quantity ──────────────────────────────────────────────────────────
    qty = 1
    m = re.search(r"(\d+)\s*(?:бутыл|шт\.?|штук)", t)
    if m:
        qty = int(m.group(1))
    else:
        m = re.search(r"(?:воды?|воду)\s+(\d+)|(\d+)\s+(?:воды?|воду)", t)
        if m:
            qty = int(m.group(1) or m.group(2))

    # ── date ─────────────────────────────────────────────────────────────
    date = None
    if "послезавтра" in t:
        date = "послезавтра"
    elif "завтра" in t:
        date = "завтра"
    elif "сегодня" in t:
        date = "сегодня"
    else:
        m = re.search(rf"(\d{{1,2}})\s+({_MONTH_PAT})", t)
        if m:
            date = f"{m.group(1)} {m.group(2)}"
        else:
            m = re.search(r"\b(\d{1,2})[./](\d{1,2})\b", t)
            if m:
                date = f"{m.group(1)}.{m.group(2)}"

    # ── product ───────────────────────────────────────────────────────────
    product_key, product = find_product(text)

    return {
        "qty": max(1, min(qty, 10)),
        "date": date,
        "product_key": product_key,
        "product": product,
    }
