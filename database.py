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
    # Stage 6: warnings given by mods (or by the auto-mod). Only the reason is stored.
    """
    CREATE TABLE IF NOT EXISTS warnings (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id      INTEGER NOT NULL,
        user_id       INTEGER NOT NULL,
        moderator_id  INTEGER NOT NULL,   -- who gave it (the bot's ID for auto-mod)
        reason        TEXT    NOT NULL,
        created_at    REAL    NOT NULL    -- unix time
    )
    """,
    # Mod assistant: steps of confirmed plans. Delayed steps wait here until run_at,
    # so they still run after a bot restart. Only IDs and the step's arguments are stored.
    """
    CREATE TABLE IF NOT EXISTS scheduled_actions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id       TEXT    NOT NULL,             -- groups the steps of one confirmed plan
        step_number   INTEGER NOT NULL,             -- 1, 2, 3 (in the order they run)
        total_steps   INTEGER NOT NULL,
        guild_id      INTEGER NOT NULL,
        channel_id    INTEGER NOT NULL,             -- where the mod asked (results are reported there)
        moderator_id  INTEGER NOT NULL,
        member_id     INTEGER NOT NULL,
        tool          TEXT    NOT NULL,
        args_json     TEXT    NOT NULL,
        depends_on    INTEGER,                      -- step_number this step needs to succeed first (or NULL)
        language      TEXT    NOT NULL,
        run_at        REAL    NOT NULL,             -- unix time
        status        TEXT    NOT NULL DEFAULT 'pending',  -- pending, running, done, failed, skipped
        result        TEXT
    )
    """,
    # Stage 7: games. Total plays and wins per member (for /gamesleaderboard).
    """
    CREATE TABLE IF NOT EXISTS game_stats (
        guild_id  INTEGER NOT NULL,
        user_id   INTEGER NOT NULL,
        plays     INTEGER NOT NULL DEFAULT 0,
        wins      INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    )
    """,
    # Stage 7: how much game XP each member earned per day (UTC), to enforce the daily cap.
    """
    CREATE TABLE IF NOT EXISTS game_xp_daily (
        guild_id  INTEGER NOT NULL,
        user_id   INTEGER NOT NULL,
        day       TEXT    NOT NULL,   -- e.g. "2026-10-16"
        xp        INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, day)
    )
    """,
    # Stage 10: duels and tournaments (real members only, never test bots).
    """
    CREATE TABLE IF NOT EXISTS duel_stats (
        guild_id            INTEGER NOT NULL,
        user_id             INTEGER NOT NULL,
        duel_wins           INTEGER NOT NULL DEFAULT 0,
        duel_losses         INTEGER NOT NULL DEFAULT 0,
        tournaments_played  INTEGER NOT NULL DEFAULT 0,
        gold                INTEGER NOT NULL DEFAULT 0,   -- 1st places
        silver              INTEGER NOT NULL DEFAULT 0,   -- 2nd places
        bronze              INTEGER NOT NULL DEFAULT 0,   -- 3rd places
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


# Columns added after a table was first created. setup_database() adds any that are
# missing, so an existing n7x.db is upgraded automatically (old data is kept).
NEW_COLUMNS = [
    # Stage 9: game profile
    ("game_stats", "game_xp", "INTEGER NOT NULL DEFAULT 0"),          # lifetime game XP
    ("game_stats", "win_streak", "INTEGER NOT NULL DEFAULT 0"),       # current wins in a row
    ("game_stats", "best_win_streak", "INTEGER NOT NULL DEFAULT 0"),  # best wins in a row
    ("game_stats", "best_highlow", "INTEGER NOT NULL DEFAULT 0"),     # best /highlow streak
]


def setup_database():
    """Create all tables (and add any new columns). Safe to run every time the bot starts."""
    with connect() as conn:
        for table_sql in TABLES:
            conn.execute(table_sql)
        for table, column, definition in NEW_COLUMNS:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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


# =====================================================================
# Stage 6: Warnings
# =====================================================================

def add_warning(guild_id, user_id, moderator_id, reason, now):
    """Save a warning and return the member's new total number of warnings."""
    execute(
        "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (guild_id, user_id, moderator_id, reason, now),
    )
    return count_warnings(guild_id, user_id)


def count_warnings(guild_id, user_id):
    row = fetch_one(
        "SELECT COUNT(*) AS n FROM warnings WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    return row["n"]


def get_numbered_warnings(guild_id, user_id):
    """
    Return ALL of a member's warnings as a list of (number, row), oldest first.
    Warning #1 is their first warning. These are the numbers /warnings shows
    and /clearwarnings uses. New warnings never change existing numbers.
    """
    rows = fetch_all(
        """
        SELECT id, moderator_id, reason, created_at FROM warnings
        WHERE guild_id = ? AND user_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (guild_id, user_id),
    )
    return list(enumerate(rows, start=1))


def delete_warning(warning_id):
    """Delete one warning by its database ID."""
    execute("DELETE FROM warnings WHERE id = ?", (warning_id,))


def delete_all_warnings(guild_id, user_id):
    """Delete every warning a member has in this server. Returns how many were deleted."""
    with connect() as conn:
        result = conn.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return result.rowcount


# =====================================================================
# Mod assistant: scheduled plan steps
# =====================================================================

def add_scheduled_steps(steps):
    """Save all steps of a confirmed plan. `steps` is a list of dicts with the table's columns."""
    with connect() as conn:
        for step in steps:
            conn.execute(
                """
                INSERT INTO scheduled_actions
                    (plan_id, step_number, total_steps, guild_id, channel_id, moderator_id, member_id,
                     tool, args_json, depends_on, language, run_at)
                VALUES (:plan_id, :step_number, :total_steps, :guild_id, :channel_id, :moderator_id, :member_id,
                        :tool, :args_json, :depends_on, :language, :run_at)
                """,
                step,
            )


def get_due_steps(now):
    """Pending steps whose time has come, earliest first."""
    return fetch_all(
        "SELECT * FROM scheduled_actions WHERE status = 'pending' AND run_at <= ? ORDER BY run_at, step_number",
        (now,),
    )


def get_plan_step(plan_id, step_number):
    return fetch_one(
        "SELECT * FROM scheduled_actions WHERE plan_id = ? AND step_number = ?",
        (plan_id, step_number),
    )


def claim_step(step_id):
    """Mark a step as running. Returns False if someone else already took it (so it never runs twice)."""
    with connect() as conn:
        result = conn.execute(
            "UPDATE scheduled_actions SET status = 'running' WHERE id = ? AND status = 'pending'",
            (step_id,),
        )
        return result.rowcount == 1


def finish_step(step_id, status, result):
    execute("UPDATE scheduled_actions SET status = ?, result = ? WHERE id = ?", (status, result, step_id))


def fail_interrupted_steps():
    """Steps left 'running' by a crash or restart are marked failed (never re-run blindly)."""
    with connect() as conn:
        return conn.execute(
            "UPDATE scheduled_actions SET status = 'failed', result = 'Interrupted by a bot restart' "
            "WHERE status = 'running'"
        ).rowcount


def get_pending_steps_for_member(guild_id, member_id):
    """Pending (not yet run) scheduled steps that target this member, earliest first."""
    return fetch_all(
        "SELECT * FROM scheduled_actions WHERE guild_id = ? AND member_id = ? AND status = 'pending' ORDER BY run_at",
        (guild_id, member_id),
    )


def delete_pending_steps_for_member(guild_id, member_id):
    """Delete a member's pending scheduled steps. Returns how many were deleted."""
    with connect() as conn:
        return conn.execute(
            "DELETE FROM scheduled_actions WHERE guild_id = ? AND member_id = ? AND status = 'pending'",
            (guild_id, member_id),
        ).rowcount


# =====================================================================
# Stage 7: Games
# =====================================================================

def record_game(guild_id, user_id, outcome, xp_reward, day, daily_cap, highlow_streak=None):
    """
    Save one finished game.
    - outcome: "win", "loss" or "draw". Wins and losses update the win streak; draws don't.
    - xp_reward: XP earned (0 for losses). Never more than `daily_cap` game XP per day in total.
      The XP goes into the SAME total as chat XP (message_stats), so games count toward /rank.
    - highlow_streak: the final streak of a /highlow game (to keep the best one).
    Returns (xp actually given, game XP earned today after this game).
    """
    won = outcome == "win"
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO game_stats (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))
        if outcome == "win":
            streak_sql = "win_streak = win_streak + 1, best_win_streak = MAX(best_win_streak, win_streak + 1)"
        elif outcome == "loss":
            streak_sql = "win_streak = 0"
        else:
            streak_sql = "win_streak = win_streak"  # a draw keeps the streak
        conn.execute(
            f"UPDATE game_stats SET plays = plays + 1, wins = wins + ?, {streak_sql}, "
            f"best_highlow = MAX(best_highlow, ?) WHERE guild_id = ? AND user_id = ?",
            (1 if won else 0, highlow_streak or 0, guild_id, user_id),
        )

        return _grant_game_xp(conn, guild_id, user_id, xp_reward, day, daily_cap)


def _grant_game_xp(conn, guild_id, user_id, xp, day, daily_cap):
    """
    Give game XP without going over `daily_cap` game XP today (UTC). The XP goes into
    the same total as chat XP, and into the lifetime game XP. Returns (given, earned today).
    """
    # Old days aren't needed anymore.
    conn.execute("DELETE FROM game_xp_daily WHERE guild_id = ? AND user_id = ? AND day < ?",
                 (guild_id, user_id, day))
    row = conn.execute("SELECT xp FROM game_xp_daily WHERE guild_id = ? AND user_id = ? AND day = ?",
                       (guild_id, user_id, day)).fetchone()
    earned_today = row["xp"] if row else 0

    given = max(0, min(xp, daily_cap - earned_today))
    if given:
        conn.execute("INSERT OR IGNORE INTO game_xp_daily (guild_id, user_id, day) VALUES (?, ?, ?)",
                     (guild_id, user_id, day))
        conn.execute("UPDATE game_xp_daily SET xp = xp + ? WHERE guild_id = ? AND user_id = ? AND day = ?",
                     (given, guild_id, user_id, day))
        conn.execute("INSERT OR IGNORE INTO game_stats (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))
        conn.execute("UPDATE game_stats SET game_xp = game_xp + ? WHERE guild_id = ? AND user_id = ?",
                     (given, guild_id, user_id))
        conn.execute("INSERT OR IGNORE INTO message_stats (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))
        conn.execute("UPDATE message_stats SET xp = xp + ? WHERE guild_id = ? AND user_id = ?",
                     (given, guild_id, user_id))
    return given, earned_today + given


def give_game_xp(guild_id, user_id, xp, day, daily_cap):
    """Bonus game XP (tournament podium, duel win), respecting the daily cap. Returns (given, earned today)."""
    with connect() as conn:
        return _grant_game_xp(conn, guild_id, user_id, xp, day, daily_cap)


def get_game_profile(guild_id, user_id, day):
    """Everything /gameprofile shows. Missing rows count as zeros."""
    stats = fetch_one("SELECT * FROM game_stats WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    xp_row = fetch_one("SELECT xp FROM message_stats WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    today_row = fetch_one("SELECT xp FROM game_xp_daily WHERE guild_id = ? AND user_id = ? AND day = ?",
                          (guild_id, user_id, day))
    profile = {key: (stats[key] if stats else 0)
               for key in ("plays", "wins", "game_xp", "win_streak", "best_win_streak", "best_highlow")}
    profile["total_xp"] = xp_row["xp"] if xp_row else 0
    profile["game_xp_today"] = today_row["xp"] if today_row else 0
    return profile


def game_leaderboard_position(guild_id, user_id):
    """A member's position on /gamesleaderboard (same order), or None if they haven't played."""
    rows = fetch_all(
        """
        SELECT user_id FROM game_stats
        WHERE guild_id = ? AND plays > 0
        ORDER BY wins DESC, CAST(wins AS REAL) / plays DESC, plays DESC
        """,
        (guild_id,),
    )
    for position, row in enumerate(rows, start=1):
        if row["user_id"] == user_id:
            return position
    return None


def top_by_game_wins(guild_id, limit=10):
    """Top players by wins (ties: better win rate, then more plays)."""
    return fetch_all(
        """
        SELECT user_id, plays, wins FROM game_stats
        WHERE guild_id = ? AND plays > 0
        ORDER BY wins DESC, CAST(wins AS REAL) / plays DESC, plays DESC
        LIMIT ?
        """,
        (guild_id, limit),
    )


# =====================================================================
# Stage 10: Duels and tournaments
# =====================================================================

def _ensure_duel_row(conn, guild_id, user_id):
    conn.execute("INSERT OR IGNORE INTO duel_stats (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))


def record_duel(guild_id, winner_id, loser_id):
    with connect() as conn:
        for user_id in (winner_id, loser_id):
            _ensure_duel_row(conn, guild_id, user_id)
        conn.execute("UPDATE duel_stats SET duel_wins = duel_wins + 1 WHERE guild_id = ? AND user_id = ?",
                     (guild_id, winner_id))
        conn.execute("UPDATE duel_stats SET duel_losses = duel_losses + 1 WHERE guild_id = ? AND user_id = ?",
                     (guild_id, loser_id))


def record_tournament(guild_id, player_ids, podium_ids):
    """
    player_ids: every real member who played. podium_ids: (1st, 2nd, 3rd) - None for a test bot.
    """
    with connect() as conn:
        for user_id in player_ids:
            _ensure_duel_row(conn, guild_id, user_id)
            conn.execute("UPDATE duel_stats SET tournaments_played = tournaments_played + 1 "
                         "WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        for column, user_id in zip(("gold", "silver", "bronze"), podium_ids):
            if user_id is not None:
                _ensure_duel_row(conn, guild_id, user_id)
                conn.execute(f"UPDATE duel_stats SET {column} = {column} + 1 WHERE guild_id = ? AND user_id = ?",
                             (guild_id, user_id))


def get_duel_stats(guild_id, user_id):
    row = fetch_one("SELECT * FROM duel_stats WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    keys = ("duel_wins", "duel_losses", "tournaments_played", "gold", "silver", "bronze")
    return {key: (row[key] if row else 0) for key in keys}
