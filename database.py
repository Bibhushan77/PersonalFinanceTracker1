import sqlite3
import os

# -------------------------------------------------
# DATABASE LOCATION (stable path inside project)
# -------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "finance.db")


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # -----------------------------
    # USERS TABLE
    # -----------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)

    # -----------------------------
    # TRANSACTIONS TABLE
    # -----------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            description TEXT,
            category TEXT NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # -----------------------------
    # BUDGETS TABLE
    # -----------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            UNIQUE(user_id, category),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # -----------------------------
    # RECURRING TRANSACTIONS
    # -----------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS recurring_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            description TEXT,
            category TEXT NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            frequency TEXT NOT NULL,
            next_date TEXT NOT NULL,
            reminder_type TEXT,
            active INTEGER DEFAULT 1,
            last_reminded_on TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # Add column if database existed before
    try:
        cur.execute("ALTER TABLE recurring_transactions ADD COLUMN last_reminded_on TEXT")
    except sqlite3.OperationalError:
        pass

    # -----------------------------
    # NOTIFICATIONS TABLE
    # -----------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()