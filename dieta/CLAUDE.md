# Толстый Секрет — бот-дневник питания

---

## О проекте

Telegram-бот — более удобная и усовершенствованная альтернатива приложению Fat Secret.
Целевая аудитория: спортсмены и люди, следящие за фигурой.

**Проблема, которую решает:**
Fat Secret и аналоги требуют постоянно заходить в приложение, вручную выбирать продукты
и указывать вес — это дополнительная дисциплина поверх тренировок. Бот убирает это трение:
достаточно одного голосового сообщения.

---

## Ключевые функции

### 1. Голосовой ввод
Пользователь записывает голосовое сообщение — что собирается съесть.
Бот расшифровывает его (Groq Whisper), ищет продукт в базе и предлагает результат
в том же формате, что Fat Secret. **КБЖУ должно точно совпадать с данными производителя** —
никакого округления или пересчёта по своему усмотрению.

### 2. Текстовый ввод
То же самое, но текстом. Поддерживается ввод нескольких продуктов за раз
(«съел 3 яйца и хлеб 50г»).

### 3. Сканирование штрихкода
Пользователь отправляет фото штрихкода — бот определяет конкретный товар
(вплоть до граммовки производителя) и сразу выдаёт точные данные КБЖУ.
Работает через FatSecret `food.find_id_for_barcode`.

### 4. Дневник питания
Подсчёт калорий, белков, жиров и углеводов за день с разбивкой по приёмам пищи
(завтрак / обед / ужин / перекус). Поддерживается редактирование и удаление записей
голосом и кнопками.

### 5. Цели и рекомендации
Пользователь ставит суточную цель по ккал и нутриентам (с валидацией согласованности).
При достижении 50% от цели бот рекомендует блюда или продукты, которые помогут
добрать оставшееся КБЖУ до нормы.

---

## Принцип точности данных

Данные КБЖУ берутся из FatSecret — той же базы, что использует оригинальное приложение.
Claude используется только как **fallback** когда FatSecret не нашёл продукт.
Это критически важно: пользователь должен получать те же цифры, что указаны на упаковке.

---

## Платформа

Telegram Bot (aiogram 3). Деплой: Railway.
Репо: github.com/pakarthurchia-lang/Fat_Secret

---

## Стек

| Слой | Технология |
|---|---|
| Бот | aiogram 3.15, Python 3.11 |
| STT | Groq Whisper large-v3 |
| ИИ | Claude Haiku (интент, парсинг запросов) + Sonnet (расчёт КБЖУ) |
| База продуктов | FatSecret Platform API v2 (OAuth2) |
| БД | SQLite + aiosqlite |
| Штрихкод | pyzbar + Pillow |

---

## Структура файлов

```
dieta/
├── main.py                        # точка входа, регистрация роутеров
├── config.py                      # переменные окружения
├── requirements.txt
├── Procfile                       # worker: python main.py
├── nixpacks.toml                  # aptPkgs = ["libzbar0"]
├── runtime.txt                    # python-3.11.9
│
├── db/
│   └── database.py                # вся работа с SQLite
│
└── bot/
    ├── handlers/
    │   ├── start.py               # /start, /help
    │   ├── food_input.py          # голос/текст → интент → КБЖУ → сохранение
    │   ├── diary.py               # /diary, редактирование записей
    │   ├── journal.py             # КБЖУ-карточка, начало/конец дня, журнал
    │   ├── settings.py            # цели по КБЖУ (FSM)
    │   └── stats.py               # статистика за 7 дней
    ├── services/
    │   ├── stt.py                 # Groq Whisper → текст
    │   ├── intent.py              # Claude Haiku → интент + параметры
    │   ├── nutrition.py           # FatSecret + Claude → КБЖУ продукта
    │   ├── fatsecret.py           # FatSecret API (поиск, штрихкод, OAuth)
    │   └── barcode.py             # pyzbar → строка штрихкода
    └── keyboards/
        └── menus.py               # reply и inline клавиатуры
```

---

## База данных

### Таблицы

**`users`** — профили и цели
```
user_id | username | goal_kcal | goal_protein | goal_fat | goal_carbs | created_at
```
Дефолты: 2000 ккал / 150г белок / 67г жир / 250г углеводы

**`food_entries`** — записи дневника
```
id | user_id | entry_date | meal_type | food_name | weight_g | kcal | protein | fat | carbs | created_at
```
meal_type: breakfast / lunch / dinner / snack / other

**`day_log`** — метаданные дня
```
id | user_id | log_date | started_at | closed_at | note
```
UNIQUE(user_id, log_date)

---

## Роутеры (порядок важен)

```
start → settings → diary → journal → stats → food_input
```
Settings и diary регистрируются до food_input чтобы FSM-состояния
(ввод целей, редактирование) не перехватывал catch-all обработчик.

---

## Главный флоу: голос → дневник

```
Голосовое/текстовое сообщение
    ↓
[stt.py] Groq Whisper → текст
    ↓
[food_input.py] _process()
    ├─ Regex pre-check (delete_all, show_kbju)
    └─ [intent.py] Claude Haiku → интент
         ↓
    Роутинг по интенту:
    ├─ add_food → [nutrition.py] → карточка → выбор приёма → сохранение
    ├─ delete / delete_all → подтверждение → удаление
    ├─ edit_weight/meal/name → подтверждение → обновление
    ├─ show_kbju → КБЖУ-карточка
    ├─ start_day / close_day → дневник дня
    └─ show_journal → журнал 14 дней
```

### Флоу добавления продукта

```
Текст продукта
    ↓
[nutrition.py] _split_foods() — Claude Haiku разбивает на отдельные продукты
    ↓
Для каждого продукта параллельно:
    [nutrition.py] _extract_query() — Claude Haiku → food_name_ru + query_en + weight_g
        ↓
    [fatsecret.py] search_food(food_name_ru) — сначала по-русски
        ↓ (если не нашёл)
    [fatsecret.py] search_food(query_en) — потом по-английски
        ↓ (если не нашёл)
    [nutrition.py] _claude_parse() — Claude Sonnet рассчитывает КБЖУ
    ↓
Карточка с КБЖУ → [Добавить] → выбор приёма пищи → database.add_entry()
    ↓
При пересечении 50% ккал → _halfway_tip() — рекомендация что доесть
```

---

## Интенты (intent.py)

Claude Haiku получает дневник за сегодня + фразу пользователя и возвращает JSON:

| Интент | Параметры | Пример фразы |
|---|---|---|
| `add_food` | — | «съел рис 200г» |
| `delete` | entry_id | «удали рис» |
| `delete_all` | — | «удали все записи» |
| `edit_weight` | entry_id, new_weight_g | «рис было не 200 а 150» |
| `edit_meal` | entry_id, new_meal_type | «рис перенеси на завтрак» |
| `edit_name` | entry_id, new_name | «переименуй рис в бурый рис» |
| `show_kbju` | — | «сколько калорий» |
| `start_day` | — | «начать день» |
| `close_day` | — | «закрыть день» |
| `show_journal` | — | «журнал» |
| `unknown` | — | fallback |

Перед вызовом Claude — regex pre-check для `delete_all` и `show_kbju`
чтобы не тратить токены на очевидные команды.

---

## Подтверждения (_pending)

Все действия требуют подтверждения кнопкой. Состояние хранится в памяти:
```python
_pending: dict[str, dict]  # key = MD5(data + user_id)
```
Типы: `add`, `add_multi`, `delete`, `delete_all`, `edit_weight`, `edit_meal`, `edit_name`

---

## Штрихкод

```
Фото → pyzbar.decode() → строка штрихкода
    ↓
[fatsecret.py] food.find_id_for_barcode → food_id → get_food_by_id()
    ↓
Показывает КБЖУ/100г → запрашивает граммы (текстом или голосом, FSM)
    ↓
Рассчитывает итоговое КБЖУ → карточка → сохранение
```

---

## Цели КБЖУ (settings.py)

FSM: ккал → белки → жиры → углеводы

**Валидация:** `белки×4 + жиры×9 + углеводы×4 = ккал ±50`
Если не сходится — бот показывает разбивку и подсказывает правильное количество углеводов.

---

## Переменные окружения

```env
BOT_TOKEN=
ANTHROPIC_API_KEY=
GROQ_API_KEY=
FATSECRET_CLIENT_ID=
FATSECRET_CLIENT_SECRET=
DATABASE_PATH=dieta.db          # на Railway: /data/dieta.db (нужен Volume)
```

---

## Деплой (Railway)

1. Пуш в Fat_Secret репо: `git subtree push --prefix=dieta git@github.com:pakarthurchia-lang/Fat_Secret.git main`
2. Railway автодеплоит из main ветки
3. Переменные: добавить все 6 в Variables
4. Для персистентности БД: добавить Volume → `/data`, поменять DATABASE_PATH

---

## Известные проблемы / что можно улучшить

### Архитектура
- `food_input.py` стал слишком большим (700+ строк) — стоит разбить:
  - `handlers/voice.py` — только STT и роутинг
  - `handlers/add_food.py` — флоу добавления
  - `handlers/edit_food.py` — флоу редактирования/удаления
- `_pending` в памяти — теряется при рестарте бота. Лучше хранить в Redis или SQLite

### Стоимость / скорость
- 3-4 вызова Claude Haiku на один запрос (split → extract → intent)
  → можно объединить split + extract в один промпт
- Claude Sonnet используется только когда FatSecret не нашёл — это правильно,
  но FatSecret часто не находит → Sonnet вызывается слишком часто
- Кэш результатов FatSecret для частых продуктов сократил бы расходы

### FatSecret
- `_parse_description()` парсит строку вида "Per 100g - Calories: Xkcal | ..."
  Хрупкий regex — если FatSecret изменит формат, всё сломается
- `max_results=3` — берём только первый результат, хотя могут быть более
  подходящие варианты. Нет логики выбора "лучшего" из 3

### UX
- Нет возможности выбрать конкретный бренд из FatSecret (только первый результат)
- Нет истории поиска / избранных продуктов
- Нет уведомлений (напоминание записать обед и т.д.)
- SQLite на Railway без Volume = данные теряются при рестарте

### Безопасность
- `_pending` не имеет TTL — старые неподтверждённые действия накапливаются
- Нет rate limiting на голосовые запросы
