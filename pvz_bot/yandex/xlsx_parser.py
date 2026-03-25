"""
Парсер XLSX-отчёта Яндекс Маркет (раздел month-reports, вкладка MONTH_CLOSING_BILLING).

Структура файла:
  Столбцы: ID ПВЗ | Название Г | Услуга | Сумма
  Строки: по одной позиции на каждую услугу/ПВЗ
  Сумма может быть положительной (выплата) или отрицательной (удержание).
"""

import io
from typing import Dict


def parse_ym_xlsx(file_bytes: bytes) -> Dict[str, float]:
    """
    Принимает байты XLSX-файла.
    Возвращает словарь {pvz_name: total_amount} —
    суммарное вознаграждение по каждому ПВЗ.
    """
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)

    # Пробуем найти нужный лист по названию, иначе берём первый
    ws = None
    for name in wb.sheetnames:
        if "billing" in name.lower() or "закрыт" in name.lower() or "month" in name.lower():
            ws = wb[name]
            break
    if ws is None:
        ws = wb.active

    # Найдём заголовки (первая строка с данными)
    header_row = None
    col_pvz_name = None
    col_amount = None

    for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
        if row is None:
            continue
        row_lower = [str(c).lower().strip() if c is not None else "" for c in row]
        # Ищем столбец с названием ПВЗ
        for i, cell in enumerate(row_lower):
            if "назван" in cell or "название" in cell:
                col_pvz_name = i
            if "сумм" in cell or "amount" in cell:
                col_amount = i
        if col_pvz_name is not None and col_amount is not None:
            header_row = row
            break

    # Если заголовки не найдены — фолбэк: стандартный порядок столбцов
    # ID ПВЗ(0) | Название Г(1) | Услуга(2) | Сумма(3)
    if col_pvz_name is None:
        col_pvz_name = 1
    if col_amount is None:
        col_amount = 3

    result: Dict[str, float] = {}

    # Если заголовок не найден, начинаем с первой строки
    data_start_row = 2 if header_row is not None else 1

    for row in ws.iter_rows(min_row=data_start_row, values_only=True):
        if row is None:
            continue
        # Строка слишком короткая
        if len(row) <= max(col_pvz_name, col_amount):
            continue

        pvz_name = row[col_pvz_name]
        amount = row[col_amount]

        if pvz_name is None or pvz_name == "":
            continue
        pvz_name = str(pvz_name).strip()
        if not pvz_name:
            continue

        try:
            amount = float(amount) if amount is not None else 0.0
        except (ValueError, TypeError):
            continue

        result[pvz_name] = result.get(pvz_name, 0.0) + amount

    return result
