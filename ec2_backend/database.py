"""
database.py — SQLite helper for outpass.db
Auto-creates the schema on first import.
"""

import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "outpass.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS outpasses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pass_id       TEXT    UNIQUE NOT NULL,
    student_name  TEXT    NOT NULL,
    enrollment_no TEXT    NOT NULL,
    section       TEXT    NOT NULL DEFAULT 'C',
    category      TEXT    NOT NULL,
    destination   TEXT    NOT NULL,
    out_time      TEXT    NOT NULL,
    in_time       TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'approved',
    exited_at     TEXT,
    returned_at   TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row_factory set to sqlite3.Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def db():
    """Context manager: yields an open connection and commits on exit (rolls back on error)."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the outpasses table if it does not exist."""
    with db() as conn:
        conn.executescript(SCHEMA)
    print(f"[db] Schema initialised → {DB_PATH}")


def row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain Python dict."""
    return dict(row)


# Initialise schema immediately on import
init_db()
