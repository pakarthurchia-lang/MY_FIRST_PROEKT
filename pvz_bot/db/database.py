import aiosqlite
from typing import Optional
from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY,
                pvz TEXT NOT NULL,
                claim_type TEXT,
                reason TEXT,
                amount REAL,
                date_issued TEXT,
                deadline TEXT,
                status TEXT,
                alerted_24h INTEGER DEFAULT 0,
                alerted_2h INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT,
                pvz TEXT,
                total_reward REAL,
                details TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS turnover (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pvz TEXT NOT NULL,
                month INTEGER NOT NULL,
                year INTEGER NOT NULL,
                amount REAL NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(pvz, month, year)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS locations (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS location_pvz (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                platform    TEXT NOT NULL,
                pvz_id      TEXT,
                pvz_name    TEXT NOT NULL
            )
        """)
        await db.execute("PRAGMA foreign_keys = ON")
        await db.commit()


async def create_location(name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        cursor = await db.execute("INSERT INTO locations (name) VALUES (?)", (name,))
        await db.commit()
        return cursor.lastrowid


async def get_all_locations() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, name FROM locations ORDER BY id") as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_location(location_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, name FROM locations WHERE id=?", (location_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_location_name(location_id: int, name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE locations SET name=? WHERE id=?", (name, location_id))
        await db.commit()


async def delete_location(location_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("DELETE FROM locations WHERE id=?", (location_id,))
        await db.commit()


async def get_location_pvzs(location_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, location_id, platform, pvz_id, pvz_name FROM location_pvz WHERE location_id=? ORDER BY id",
            (location_id,)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def set_location_pvzs(location_id: int, pvzs: list) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("DELETE FROM location_pvz WHERE location_id=?", (location_id,))
        for pvz in pvzs:
            await db.execute(
                "INSERT INTO location_pvz (location_id, platform, pvz_id, pvz_name) VALUES (?, ?, ?, ?)",
                (location_id, pvz["platform"], pvz.get("pvz_id"), pvz["pvz_name"])
            )
        await db.commit()


async def get_location_with_pvzs(location_id: int) -> Optional[dict]:
    loc = await get_location(location_id)
    if loc is None:
        return None
    pvzs = await get_location_pvzs(location_id)
    loc["pvzs"] = [{"platform": p["platform"], "pvz_id": p["pvz_id"], "pvz_name": p["pvz_name"]} for p in pvzs]
    return loc


async def upsert_turnover(pvz: str, month: int, year: int, amount: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO turnover (pvz, month, year, amount)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(pvz, month, year) DO UPDATE SET amount=excluded.amount
        """, (pvz, month, year, amount))
        await db.commit()


async def get_turnover(pvz: str, month: int, year: int) -> Optional[float]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT amount FROM turnover WHERE pvz=? AND month=? AND year=?",
            (pvz, month, year)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def upsert_claim(claim: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO claims (id, pvz, claim_type, reason, amount, date_issued, deadline, status)
            VALUES (:id, :pvz, :claim_type, :reason, :amount, :date_issued, :deadline, :status)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                deadline=excluded.deadline
        """, claim)
        await db.commit()


async def get_active_claims() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM claims
            WHERE status != 'closed'
            ORDER BY deadline ASC
        """) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_unalerted_claims(hours: int) -> list:
    col = "alerted_24h" if hours == 24 else "alerted_2h"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"""
            SELECT * FROM claims
            WHERE status != 'closed'
            AND {col} = 0
            AND deadline IS NOT NULL
            AND datetime(deadline) <= datetime('now', '+{hours} hours')
            AND datetime(deadline) >= datetime('now')
        """) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def mark_alerted(claim_id: str, hours: int):
    col = "alerted_24h" if hours == 24 else "alerted_2h"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE claims SET {col}=1 WHERE id=?", (claim_id,))
        await db.commit()
