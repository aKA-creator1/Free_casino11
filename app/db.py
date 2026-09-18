import os, aiosqlite, hashlib, secrets
from datetime import datetime, date
from decimal import Decimal
from app import config

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "casino.db")
_db: aiosqlite.Connection = None


async def init_db():
    global _db
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            balance REAL DEFAULT 0,
            total_taps INTEGER DEFAULT 0,
            daily_taps INTEGER DEFAULT 0,
            daily_taps_date TEXT,
            total_won REAL DEFAULT 0,
            total_lost REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            type TEXT,
            game TEXT,
            amount REAL,
            balance_after REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            price REAL,
            items TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS pvp_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_type TEXT,
            player1_id INTEGER REFERENCES users(id),
            player2_id INTEGER REFERENCES users(id),
            bet REAL,
            state TEXT DEFAULT '{}',
            status TEXT DEFAULT 'waiting',
            result TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS gift_claims (
            user_id INTEGER REFERENCES users(id),
            gift_type TEXT,
            claimed_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, gift_type, claimed_at)
        );
        CREATE TABLE IF NOT EXISTS seeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_seed TEXT NOT NULL,
            client_seed TEXT DEFAULT '',
            nonce INTEGER DEFAULT 0
        );
    """)
    await _db.commit()


def get_pool():
    return _db


async def get_or_create_user(tg_id: int, username: str = "", first_name: str = "") -> dict:
    cur = await _db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    row = await cur.fetchone()
    if not row:
        cur = await _db.execute(
            "INSERT INTO users(tg_id,username,first_name,balance) VALUES(?,?,?,?)",
            (tg_id, username, first_name, config.START_BONUS)
        )
        await _db.commit()
        cur = await _db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
    return dict(row)


async def update_balance(user_id: int, delta: Decimal) -> dict:
    await _db.execute("UPDATE users SET balance = balance + ? WHERE id=?", (float(delta), user_id))
    await _db.commit()
    cur = await _db.execute("SELECT * FROM users WHERE id=?", (user_id,))
    return dict(await cur.fetchone())


async def record_tx(user_id: int, tx_type: str, game: str, amount: Decimal, balance_after: Decimal):
    await _db.execute(
        "INSERT INTO transactions(user_id,type,game,amount,balance_after) VALUES(?,?,?,?,?)",
        (user_id, tx_type, game, float(amount), float(balance_after))
    )
    await _db.commit()


async def do_tap(user_id: int) -> dict:
    cur = await _db.execute("SELECT * FROM users WHERE id=?", (user_id,))
    user = await cur.fetchone()
    today = date.today().isoformat()
    if user['daily_taps_date'] != today:
        await _db.execute("UPDATE users SET daily_taps=0, daily_taps_date=? WHERE id=?", (today, user_id))
        await _db.commit()
        taps_today = 0
    else:
        taps_today = user['daily_taps']

    if taps_today >= config.TAP_DAILY_LIMIT:
        return {"error": "limit", "remaining": 0, "taps_today": taps_today}

    reward = Decimal(str(config.TAP_REWARD))
    for threshold, mult in config.TAP_MULTIPLIER_THRESHOLDS:
        if taps_today < threshold:
            reward *= Decimal(str(mult))
            break

    if reward < Decimal("0.01"):
        reward = Decimal("0.01")

    import random
    r = random.random()
    if r < config.TAP_X20_CHANCE:
        reward *= 20
        mult_type = "x20"
    elif r < config.TAP_X20_CHANCE + config.TAP_X5_CHANCE:
        reward *= 5
        mult_type = "x5"
    else:
        mult_type = "x1"

    await _db.execute(
        "UPDATE users SET balance=balance+?, total_taps=total_taps+1, daily_taps=daily_taps+1 WHERE id=?",
        (float(reward), user_id)
    )
    await _db.commit()
    cur = await _db.execute("SELECT * FROM users WHERE id=?", (user_id,))
    row = await cur.fetchone()
    return {
        "balance": float(row['balance']),
        "reward": float(reward),
        "mult": mult_type,
        "taps_today": taps_today + 1,
        "remaining": config.TAP_DAILY_LIMIT - taps_today - 1
    }


async def get_top_users(limit: int = 20) -> list:
    cur = await _db.execute(
        "SELECT tg_id, username, first_name, balance FROM users ORDER BY balance DESC LIMIT ?",
        (limit,)
    )
    return [dict(r) for r in await cur.fetchall()]


async def get_user_stats() -> dict:
    cur = await _db.execute("SELECT COUNT(*) as c FROM users")
    total_users = (await cur.fetchone())['c']
    cur = await _db.execute("SELECT COALESCE(SUM(ABS(amount)),0) as s FROM transactions WHERE type='bet'")
    total_bets = (await cur.fetchone())['s']
    cur = await _db.execute("SELECT COUNT(*) as c FROM transactions WHERE type='bet'")
    total_games = (await cur.fetchone())['c']
    return {"total_users": total_users, "total_bets": float(total_bets), "total_games": total_games}


async def can_claim_gift(user_id: int, gift_type: str) -> bool:
    cur = await _db.execute(
        "SELECT 1 FROM gift_claims WHERE user_id=? AND gift_type=? AND claimed_at > datetime('now','-20 hours')",
        (user_id, gift_type)
    )
    return await cur.fetchone() is None


async def record_gift_claim(user_id: int, gift_type: str):
    await _db.execute("INSERT INTO gift_claims(user_id,gift_type) VALUES(?,?)", (user_id, gift_type))
    await _db.commit()


async def admin_give(tg_id: int, amount: Decimal) -> dict:
    cur = await _db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    row = await cur.fetchone()
    if not row:
        return {"error": "user_not_found"}
    await _db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (float(amount), tg_id))
    await _db.commit()
    cur = await _db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    return dict(await cur.fetchone())


async def get_seed() -> str:
    cur = await _db.execute("SELECT server_seed FROM seeds ORDER BY id DESC LIMIT 1")
    row = await cur.fetchone()
    return row['server_seed'] if row else secrets.token_hex(32)


async def create_seed() -> str:
    new_seed = secrets.token_hex(32)
    await _db.execute("INSERT INTO seeds(server_seed) VALUES(?)", (new_seed,))
    await _db.commit()
    return new_seed
