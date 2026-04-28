import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "database.db"


def get_connection():
    """Return a SQLite connection with dictionary-style rows."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def init_db():
    """Create database tables if they do not already exist."""
    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('student', 'admin'))
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('Present', 'Absent')),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, date)
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS leave_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending'
                    CHECK(status IN ('Pending', 'Approved', 'Rejected')),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        connection.commit()


def seed_users():
    """Insert sample users only when they do not exist."""
    sample_users = [
        ("Student1", "student"),
        ("Student2", "student"),
        ("Admin", "admin"),
    ]
    with get_connection() as connection:
        cursor = connection.cursor()
        for name, role in sample_users:
            cursor.execute(
                "SELECT id FROM users WHERE name = ? AND role = ?;",
                (name, role),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    "INSERT INTO users (name, role) VALUES (?, ?);",
                    (name, role),
                )
        connection.commit()
