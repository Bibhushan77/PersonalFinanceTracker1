import sqlite3
import os

DB_NAME = "finance.db"


def get_db_path():
    # Absolute path to finance.db in the same folder as this file
    base_dir = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_dir, DB_NAME)


def get_connection():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row  # access columns by name
    return conn


def init_db():
    """
    Create core tables if they don't exist:
      - users
      - transactions  (per user)
      - recurring_transactions  (per user)
      - budgets  (per user)
    """
    conn = get_connection()
    cur = conn.cursor()

    # ---------- USERS TABLE ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ---------- TRANSACTIONS TABLE (per user) ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            description TEXT,
            category TEXT NOT NULL,
            type TEXT NOT NULL,   -- 'Income' or 'Expense'
            amount REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    # ---------- RECURRING TRANSACTIONS TABLE (per user) ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS recurring_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            description TEXT,
            category TEXT NOT NULL,
            type TEXT NOT NULL,         -- 'Income' or 'Expense'
            amount REAL NOT NULL,
            frequency TEXT NOT NULL,    -- Daily / Weekly / Monthly / Yearly
            next_date TEXT NOT NULL,    -- YYYY-MM-DD
            reminder_type TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    # ---------- BUDGETS TABLE (per user) ----------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            UNIQUE (user_id, category),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    conn.commit()
    conn.close()
