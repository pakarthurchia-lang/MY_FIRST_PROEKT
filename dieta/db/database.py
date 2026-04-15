from __future__ import annotations
import aiosqlite
from datetime import date
from typing import Optional
import config


async def init_db() -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS day_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                log_date   TEXT NOT NULL,
                started_at TEXT,
                closed_at  TEXT,
                note       TEXT,
                UNIQUE(user_id, log_date)
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                goal_kcal  INTEGER DEFAULT 2000,
                goal_protein INTEGER DEFAULT 150,
                goal_fat   INTEGER DEFAULT 67,
                goal_carbs INTEGER DEFAULT 250,
                created_at TEXT DEFAULT (date('now'))
            );

            CREATE TABLE IF NOT EXISTS food_entries (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                entry_date TEXT NOT NULL,
                meal_type  TEXT NOT NULL DEFAULT 'other',
                food_name  TEXT NOT NULL,
                weight_g   REAL NOT NULL,
                kcal       REAL NOT NULL,
                protein    REAL NOT NULL,
                fat        REAL NOT NULL,
                carbs      REAL NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)
        await db.commit()


async def ensure_user(user_id: int, username: Optional[str] = None) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        await db.commit()


async def add_entry(
    user_id: int,
    entry_date: str,
    meal_type: str,
    food_name: str,
    weight_g: float,
    kcal: float,
    protein: float,
    fat: float,
    carbs: float,
) -> int:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO food_entries
               (user_id, entry_date, meal_type, food_name, weight_g, kcal, protein, fat, carbs)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, entry_date, meal_type, food_name, weight_g, kcal, protein, fat, carbs),
        )
        await db.commit()
        return cursor.lastrowid


async def get_entry(entry_id: int, user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM food_entries WHERE id = ? AND user_id = ?",
            (entry_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_entry_weight(entry_id: int, user_id: int, new_weight_g: float) -> bool:
    """Recalculate КБЖУ proportionally for new weight and update."""
    entry = await get_entry(entry_id, user_id)
    if not entry or entry["weight_g"] == 0:
        return False
    ratio = new_weight_g / entry["weight_g"]
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cursor = await db.execute(
            """UPDATE food_entries
               SET weight_g=?, kcal=?, protein=?, fat=?, carbs=?
               WHERE id=? AND user_id=?""",
            (
                new_weight_g,
                round(entry["kcal"]    * ratio, 1),
                round(entry["protein"] * ratio, 1),
                round(entry["fat"]     * ratio, 1),
                round(entry["carbs"]   * ratio, 1),
                entry_id, user_id,
            ),
        )
        await db.commit()
        return cursor.rowcount > 0


async def update_entry_meal_type(entry_id: int, user_id: int, meal_type: str) -> bool:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cursor = await db.execute(
            "UPDATE food_entries SET meal_type=? WHERE id=? AND user_id=?",
            (meal_type, entry_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def update_entry_name(entry_id: int, user_id: int, food_name: str) -> bool:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cursor = await db.execute(
            "UPDATE food_entries SET food_name=? WHERE id=? AND user_id=?",
            (food_name, entry_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_entry(entry_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM food_entries WHERE id = ? AND user_id = ?",
            (entry_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_all_entries(user_id: int, entry_date: str) -> int:
    """Delete all entries for a given date. Returns count of deleted rows."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM food_entries WHERE user_id = ? AND entry_date = ?",
            (user_id, entry_date),
        )
        await db.commit()
        return cursor.rowcount


async def get_day_entries(user_id: int, entry_date: str) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, meal_type, food_name, weight_g, kcal, protein, fat, carbs, created_at
               FROM food_entries
               WHERE user_id = ? AND entry_date = ?
               ORDER BY created_at""",
            (user_id, entry_date),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_day_totals(user_id: int, entry_date: str) -> dict:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cursor = await db.execute(
            """SELECT COALESCE(SUM(kcal),0) as kcal,
                      COALESCE(SUM(protein),0) as protein,
                      COALESCE(SUM(fat),0) as fat,
                      COALESCE(SUM(carbs),0) as carbs
               FROM food_entries
               WHERE user_id = ? AND entry_date = ?""",
            (user_id, entry_date),
        )
        row = await cursor.fetchone()
        return {
            "kcal": round(row[0], 1),
            "protein": round(row[1], 1),
            "fat": round(row[2], 1),
            "carbs": round(row[3], 1),
        }


async def get_week_stats(user_id: int) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT entry_date,
                      ROUND(SUM(kcal),1) as kcal,
                      ROUND(SUM(protein),1) as protein,
                      ROUND(SUM(fat),1) as fat,
                      ROUND(SUM(carbs),1) as carbs
               FROM food_entries
               WHERE user_id = ?
                 AND entry_date >= date('now', '-6 days')
               GROUP BY entry_date
               ORDER BY entry_date DESC""",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_user_goals(user_id: int) -> dict:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT goal_kcal, goal_protein, goal_fat, goal_carbs FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {"goal_kcal": 2000, "goal_protein": 150, "goal_fat": 67, "goal_carbs": 250}


async def day_start(user_id: int, log_date: str) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO day_log (user_id, log_date, started_at)
               VALUES (?, ?, datetime('now','localtime'))
               ON CONFLICT(user_id, log_date) DO UPDATE
               SET started_at = COALESCE(started_at, datetime('now','localtime'))""",
            (user_id, log_date),
        )
        await db.commit()


async def day_close(user_id: int, log_date: str, note: str = "") -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO day_log (user_id, log_date, closed_at, note)
               VALUES (?, ?, datetime('now','localtime'), ?)
               ON CONFLICT(user_id, log_date) DO UPDATE
               SET closed_at = datetime('now','localtime'),
                   note = CASE WHEN ? != '' THEN ? ELSE note END""",
            (user_id, log_date, note, note, note),
        )
        await db.commit()


async def get_day_log(user_id: int, log_date: str) -> Optional[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM day_log WHERE user_id=? AND log_date=?",
            (user_id, log_date),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_journal(user_id: int, limit: int = 14) -> list[dict]:
    """Return last N days that have food entries, with totals and log status."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT
                 fe.entry_date,
                 ROUND(SUM(fe.kcal), 0)    AS kcal,
                 ROUND(SUM(fe.protein), 1) AS protein,
                 ROUND(SUM(fe.fat), 1)     AS fat,
                 ROUND(SUM(fe.carbs), 1)   AS carbs,
                 COUNT(*)                  AS entries_count,
                 dl.started_at,
                 dl.closed_at,
                 dl.note
               FROM food_entries fe
               LEFT JOIN day_log dl
                 ON dl.user_id = fe.user_id AND dl.log_date = fe.entry_date
               WHERE fe.user_id = ?
               GROUP BY fe.entry_date
               ORDER BY fe.entry_date DESC
               LIMIT ?""",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def update_user_goals(
    user_id: int, goal_kcal: int, goal_protein: int, goal_fat: int, goal_carbs: int
) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """UPDATE users
               SET goal_kcal=?, goal_protein=?, goal_fat=?, goal_carbs=?
               WHERE user_id=?""",
            (goal_kcal, goal_protein, goal_fat, goal_carbs, user_id),
        )
        await db.commit()
