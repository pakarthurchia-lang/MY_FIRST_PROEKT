"""
Парсит PDF-отчёт Ozon ПВЗ и извлекает выручку по каждой точке.
Раздел: «Сумма вознаграждения по пунктам выдачи»
Каждая ПВЗ заканчивается строкой «Итого по СД: <сумма>»
"""
import io
import re
import pdfplumber
from ozon.http_client import get_access_token, _get_cookies, HEADERS_BASE


PDF_DOWNLOAD_URL = (
    "https://turbo-pvz.ozon.ru/api2/reports/agent/{report_id}"
    "/documents/downloadV2?fileFormat=Pdf&printingForm=Report"
)

SECTION_HEADER = "Сумма вознаграждения по пунктам выдачи"
TOTAL_PATTERN = re.compile(r"Итого по СД:\s*([\d\s]+[.,]\d{2})")


def _parse_amount(s: str) -> float:
    """'292 051,83' → 292051.83"""
    s = s.replace(" ", "").replace(",", ".")
    return float(s)


def parse_pvz_revenue(pdf_bytes: bytes, total_revenue: float = None) -> dict:
    """
    Принимает PDF как bytes, возвращает {pvz_name: amount}.
    total_revenue — известная сумма из API для проверки корректности.
    """
    result = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    section_start = full_text.find(SECTION_HEADER)
    if section_start == -1:
        return result

    section_text = full_text[section_start:]

    # Обрезаем секцию по первому маркеру конца (итоги по всем ПВЗ / начало нового раздела)
    stop_markers = ["Итого по всем пунктам", "Итого по договору", "Оборот по пунктам",
                    "Оборот товаров", "Количество отправлений"]
    end_pos = len(section_text)
    for marker in stop_markers:
        pos = section_text.find(marker)
        if 0 < pos < end_pos:
            end_pos = pos
    section_text = section_text[:end_pos]

    lines = section_text.split("\n")
    current_pvz = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Новая ПВЗ — переключаемся
        pvz_match = re.match(r"^([А-ЯЁа-яёA-Z\w\-]+_\d+)[,\s]", line, re.IGNORECASE)
        if pvz_match:
            current_pvz = pvz_match.group(1)
            continue

        # «Итого по СД:» — суммируем подитоги (основное + доставка)
        m = TOTAL_PATTERN.search(line)
        if m and current_pvz:
            amount = _parse_amount(m.group(1))
            if total_revenue and amount > total_revenue:
                continue  # одиночное значение явно из другого раздела
            result[current_pvz] = result.get(current_pvz, 0) + amount
            # Если сумма всех ПВЗ превысила известную выручку — вышли за пределы секции
            if total_revenue and sum(result.values()) > total_revenue * 1.05:
                result[current_pvz] -= amount  # отменяем последнее добавление
                break

    return result


async def download_and_parse_pdf(report_id: str, total_revenue: float = None) -> dict:
    """
    Скачивает PDF-отчёт и возвращает выручку по ПВЗ.
    """
    import aiohttp

    url = PDF_DOWNLOAD_URL.format(report_id=report_id)
    token = await get_access_token()
    cookies = _get_cookies()
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}

    async with aiohttp.ClientSession(cookies=cookies) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Не удалось скачать PDF: {resp.status} {text[:200]}")
            pdf_bytes = await resp.read()

    return parse_pvz_revenue(pdf_bytes, total_revenue=total_revenue)
