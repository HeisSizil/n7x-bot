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
