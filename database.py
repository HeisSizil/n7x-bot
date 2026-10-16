"""
database.py - Everything that talks to the SQLite database lives here.

SQLite stores all data in a single file (n7x.db) next to bot.py.
No server or setup is needed.

Each later stage adds its own table to the TABLES list below.
"""

import os
import sqlite3
from contextlib import contextmanager

# The database file sits in the same folder as this file.
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "n7x.db")

# Each string here creates one table if it doesn't exist yet.
# Later stages will add more tables to this list.
TABLES = [
    # A tiny key/value table, handy for simple settings.
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    # Stage 2: message ranks. We store ONLY counts and XP, never message text.
    """
    CREATE TABLE IF NOT EXISTS message_stats (
        guild_id    INTEGER NOT NULL,
        user_id     INTEGER NOT NULL,
        messages    INTEGER NOT NULL DEFAULT 0,  -- total messages sent
        xp          INTEGER NOT NULL DEFAULT 0,  -- total XP earned
        last_xp_at  REAL    NOT NULL DEFAULT 0,  -- when they last got XP (unix time)
        PRIMARY KEY (guild_id, user_id)
    )
    """,
    # Stage 3: voice ranks. Total seconds spent in voice with at least one other person.
    """
    CREATE TABLE IF NOT EXISTS voice_stats (
        guild_id  INTEGER NOT NULL,
        user_id   INTEGER NOT NULL,
        seconds   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    )
    """,
]


@contextmanager
def connect():
    """
    Open the database, give it to you, then save and close it.

    Use it like this:
        with connect() as conn:
            conn.execute("...")
    """
    conn = sqlite3.connect(DB_FILE)
    # Lets us read rows like a dictionary: row["xp"] instead of row[2]
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()  # save changes
    finally:
        conn.close()


def setup_database():
    """Create all tables. Safe to run every time the bot starts."""
    with connect() as conn:
        for table_sql in TABLES:
            conn.execute(table_sql)


# --- Small helpers so the cogs stay short ---

def execute(sql, params=()):
    """Run a query that changes data (INSERT, UPDATE, DELETE)."""
    with connect() as conn:
        conn.execute(sql, params)


def fetch_one(sql, params=()):
    """Run a query and return the first row (or None)."""
    with connect() as conn:
        return conn.execute(sql, params).fetchone()


def fetch_all(sql, params=()):
    """Run a query and return all rows as a list."""
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


# =====================================================================
# Stage 2: Message ranks
# =====================================================================

def record_message(guild_id, user_id, xp_amount, now, cooldown_seconds):
    """
    Count one message for a user, and give them XP if their cooldown is over.
    Returns True if XP was given, False if they were still on cooldown.
    """
    with connect() as conn:
        # Make sure the user has a row (does nothing if it already exists).
        conn.execute(
            "INSERT OR IGNORE INTO message_stats (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        # Always count the message.
        conn.execute(
            "UPDATE message_stats SET messages = messages + 1 WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        # Only give XP if enough time has passed since the last XP.
        result = conn.execute(
            """
            UPDATE message_stats
            SET xp = xp + ?, last_xp_at = ?
            WHERE guild_id = ? AND user_id = ? AND last_xp_at <= ?
            """,
            (xp_amount, now, guild_id, user_id, now - cooldown_seconds),
        )
        return result.rowcount > 0


def get_message_stats(guild_id, user_id):
    """Return a user's messages, xp and position (1 = top) by XP, or None."""
    row = fetch_one(
        "SELECT messages, xp FROM message_stats WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    if row is None:
        return None
    # Position = how many people have more XP than you, plus one.
    above = fetch_one(
        "SELECT COUNT(*) AS n FROM message_stats WHERE guild_id = ? AND xp > ?",
        (guild_id, row["xp"]),
    )
    return {"messages": row["messages"], "xp": row["xp"], "position": above["n"] + 1}


def top_by_messages(guild_id, limit=10):
    """Return the top users by message count."""
    return fetch_all(
        """
        SELECT user_id, messages, xp FROM message_stats
        WHERE guild_id = ?
        ORDER BY messages DESC, xp DESC
        LIMIT ?
        """,
        (guild_id, limit),
    )


# =====================================================================
# Stage 3: Voice ranks
# =====================================================================

def add_voice_seconds(guild_id, user_ids, seconds):
    """Add `seconds` of voice time to every user in `user_ids` (all in one go)."""
    with connect() as conn:
        for user_id in user_ids:
            conn.execute(
                "INSERT OR IGNORE INTO voice_stats (guild_id, user_id) VALUES (?, ?)",
                (guild_id, user_id),
            )
            conn.execute(
                "UPDATE voice_stats SET seconds = seconds + ? WHERE guild_id = ? AND user_id = ?",
                (seconds, guild_id, user_id),
            )


def get_voice_stats(guild_id, user_id):
    """Return a user's voice seconds and position (1 = top), or None."""
    row = fetch_one(
        "SELECT seconds FROM voice_stats WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    if row is None:
        return None
    above = fetch_one(
        "SELECT COUNT(*) AS n FROM voice_stats WHERE guild_id = ? AND seconds > ?",
        (guild_id, row["seconds"]),
    )
    return {"seconds": row["seconds"], "position": above["n"] + 1}


def top_by_voice(guild_id, limit=10):
    """Return the top users by voice time."""
    return fetch_all(
        """
        SELECT user_id, seconds FROM voice_stats
        WHERE guild_id = ?
        ORDER BY seconds DESC
        LIMIT ?
        """,
        (guild_id, limit),
    )
