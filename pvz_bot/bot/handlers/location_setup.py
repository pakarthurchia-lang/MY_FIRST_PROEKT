"""
Управление локациями ПВЗ.
"""
import sqlite3
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from config import OWNER_CHAT_ID
from db.database import (
    create_location, get_all_locations, get_location,
    update_location_name, delete_location,
    set_location_pvzs, get_location_with_pvzs,
)

router = Router()


class LocationSetupState(StatesGroup):
    entering_name = State()
    choosing_pvzs = State()
    confirm_delete = State()


# ── Загрузка доступных ПВЗ ─────────────────────────────────────────────────

async def _load_all_pvzs() -> tuple[list[dict], str | None]:
    """
    Возвращает (pvzs, warning).
    pvzs — список {platform, pvz_id, pvz_name}
    warning — строка предупреждения если ЯМ недоступен, иначе None
    """
    pvzs = []
    warning = None

    # Ozon
    try:
        from ozon.scraper import _get_all_stores
        stores = await _get_all_stores()
        for s in stores:
            pvzs.append({
                "platform": "ozon",
                "pvz_id": str(s["id"]),
                "pvz_name": s.get("name") or str(s["id"]),
            })
    except Exception:
        pass

    # Яндекс Маркет
    try:
        from datetime import date
        from yandex.reports import download_report_xlsx, available_months_for_menu
        from yandex.xlsx_parser import parse_ym_xlsx
        months = available_months_for_menu(1)
        if months:
            m = months[0]
            xlsx_bytes = await download_report_xlsx(m["month"], m["year"])
            ym_data = parse_ym_xlsx(xlsx_bytes)
            for name in ym_data.keys():
                pvzs.append({
                    "platform": "ym",
                    "pvz_id": None,
                    "pvz_name": name,
                })
    except Exception:
        warning = "⚠️ Яндекс Маркет недоступен — показываю только Ozon ПВЗ."

    return pvzs, warning


# ── Клавиатура чекбоксов ───────────────────────────────────────────────────

def _pvz_checkbox_keyboard(all_pvzs: list[dict], selected: list[int]) -> InlineKeyboardMarkup:
    buttons = []
    for idx, pvz in enumerate(all_pvzs):
        checked = idx in selected
        prefix = "✅" if checked else "◻️"
        platform_label = pvz["platform"].upper()
        text = f"{prefix} {pvz['pvz_name']} ({platform_label})"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"toggle:{idx}")])
    buttons.append([
        InlineKeyboardButton(text="✅ Сохранить", callback_data="loc:save"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="loc:cancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Список локаций ─────────────────────────────────────────────────────────

async def show_locations_list(target, text: str = "📍 <b>Локации ПВЗ</b>"):
    """Показывает список локаций. target — Message или CallbackQuery."""
    locations = await get_all_locations()
    buttons = []
    for loc in locations:
        buttons.append([
            InlineKeyboardButton(text=loc["name"], callback_data=f"loc:edit:{loc['id']}"),
            InlineKeyboardButton(text="✏️", callback_data=f"loc:edit:{loc['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"loc:del:{loc['id']}"),
        ])
    buttons.append([InlineKeyboardButton(text="➕ Добавить локацию", callback_data="loc:new")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:back")])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup)


# ── Хендлеры ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "loc:list")
async def cb_loc_list(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await state.clear()
    await show_locations_list(call)


@router.callback_query(F.data == "loc:new")
async def cb_loc_new(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await state.set_state(LocationSetupState.entering_name)
    await state.update_data(edit_location_id=None)
    await call.message.answer("Введи название локации (например: «Внуково» или «Ростов»):")


@router.callback_query(F.data.startswith("loc:edit:"))
async def cb_loc_edit(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    location_id = int(call.data.split(":")[2])
    loc = await get_location_with_pvzs(location_id)
    if not loc:
        await call.message.answer("Локация не найдена.")
        return

    all_pvzs, warning = await _load_all_pvzs()

    # Pre-fill: ищем совпадения по platform+pvz_name
    existing_keys = {(p["platform"], p["pvz_name"]) for p in loc["pvzs"]}
    selected = [
        idx for idx, pvz in enumerate(all_pvzs)
        if (pvz["platform"], pvz["pvz_name"]) in existing_keys
    ]

    await state.update_data(
        edit_location_id=location_id,
        location_name=loc["name"],
        all_pvzs=all_pvzs,
        selected=selected,
    )
    await state.set_state(LocationSetupState.choosing_pvzs)

    msg = f"✏️ <b>Редактирование: {loc['name']}</b>\n\nВыбери ПВЗ:"
    if warning:
        msg += f"\n{warning}"
    await call.message.answer(
        msg,
        parse_mode="HTML",
        reply_markup=_pvz_checkbox_keyboard(all_pvzs, selected),
    )


@router.callback_query(F.data.startswith("loc:del:"))
async def cb_loc_del_confirm(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    location_id = int(call.data.split(":")[2])
    loc = await get_location(location_id)
    if not loc:
        await call.message.answer("Локация не найдена.")
        return

    await state.set_state(LocationSetupState.confirm_delete)
    await state.update_data(delete_location_id=location_id)

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"loc:del_ok:{location_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="loc:cancel"),
        ]
    ])
    await call.message.answer(
        f"🗑 Удалить локацию <b>{loc['name']}</b>?",
        parse_mode="HTML",
        reply_markup=markup,
    )


@router.callback_query(F.data.startswith("loc:del_ok:"))
async def cb_loc_del_ok(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    location_id = int(call.data.split(":")[2])
    await delete_location(location_id)
    await state.clear()
    await call.message.answer("✅ Локация удалена.")
    await show_locations_list(call)


@router.callback_query(F.data == "loc:cancel")
async def cb_loc_cancel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    await state.clear()
    await show_locations_list(call)


@router.message(LocationSetupState.entering_name)
async def fsm_entering_name(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_CHAT_ID:
        return
    name = message.text.strip() if message.text else ""
    if not name:
        await message.answer("Название не может быть пустым. Введи название локации:")
        return

    await state.update_data(location_name=name)

    all_pvzs, warning = await _load_all_pvzs()
    await state.update_data(all_pvzs=all_pvzs, selected=[])
    await state.set_state(LocationSetupState.choosing_pvzs)

    msg = f"📍 <b>Локация: {name}</b>\n\nВыбери ПВЗ которые входят в эту локацию:"
    if warning:
        msg += f"\n{warning}"
    await message.answer(
        msg,
        parse_mode="HTML",
        reply_markup=_pvz_checkbox_keyboard(all_pvzs, []),
    )


@router.callback_query(F.data.startswith("toggle:"), LocationSetupState.choosing_pvzs)
async def fsm_toggle_pvz(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    idx = int(call.data.split(":")[1])
    data = await state.get_data()
    all_pvzs = data.get("all_pvzs", [])
    selected: list = list(data.get("selected", []))

    if idx in selected:
        selected.remove(idx)
    else:
        selected.append(idx)

    await state.update_data(selected=selected)
    await call.message.edit_reply_markup(
        reply_markup=_pvz_checkbox_keyboard(all_pvzs, selected)
    )


@router.callback_query(F.data == "loc:save", LocationSetupState.choosing_pvzs)
async def fsm_save_location(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_CHAT_ID:
        return
    await call.answer()
    data = await state.get_data()
    all_pvzs: list = data.get("all_pvzs", [])
    selected: list = data.get("selected", [])
    name: str = data.get("location_name", "")
    edit_location_id = data.get("edit_location_id")

    if not selected:
        await call.message.answer("⚠️ Выбери хотя бы один ПВЗ перед сохранением.")
        return

    chosen_pvzs = [all_pvzs[i] for i in selected if i < len(all_pvzs)]

    try:
        if edit_location_id is None:
            location_id = await create_location(name)
        else:
            location_id = edit_location_id
            await update_location_name(location_id, name)
        await set_location_pvzs(location_id, chosen_pvzs)
    except Exception as e:
        err_str = str(e)
        if "UNIQUE" in err_str or "unique" in err_str:
            await call.message.answer("⚠️ Такое название уже есть. Введи другое название:")
            await state.set_state(LocationSetupState.entering_name)
        else:
            await call.message.answer(f"❌ Ошибка сохранения: {e}")
        return

    await state.clear()
    await call.message.answer("✅ Сохранено!")
    await show_locations_list(call)
